"""Atomic memory layer (mem0-style) for Psyche.

Add-on module: stores small atomic facts with agent/run scope, indexes them in
a dedicated usearch index (<db>.mem.usearch) plus the atomic_memories_fts
table, and retrieves a few-KB slice via hybrid (vector + keyword + entity)
search. ADD-only writes with a cosine near-duplicate guard; conflicts resolve
at read time via recency and the superseded_by column. Base Psyche tables and
the chunks usearch index are untouched.
"""
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone

import numpy as np

from db import resolve_db_path, get_connection, init_db

# Skip storing a fact whose cosine similarity to an existing live fact exceeds this.
DUP_SIMILARITY = 0.95
# A new fact in [SUPERSEDE_LOW, DUP_SIMILARITY) to an existing live fact marks
# the old one superseded — write-time contradiction resolution, no LLM needed.
SUPERSEDE_LOW = 0.80
# Semantic matches below this similarity are discarded — weak matches waste
# injected tokens. bge-small scores unrelated text ~0.4-0.5, related ~0.6+.
DEFAULT_MIN_SCORE = float(os.getenv("PSYCHE_MEM_MIN_SCORE", "0.55"))
VALID_CATEGORIES = {"preference", "decision", "fact", "lesson"}

EXTRACTION_SYSTEM = """You extract durable atomic memories from a conversation transcript.
Return a STRICT JSON array (no markdown fences) of objects:
  {"fact": "<one self-contained sentence>", "category": "preference|decision|fact|lesson", "entities": ["<entity>", ...]}
Rules:
- Only durable information useful in FUTURE sessions: user preferences, decisions made and why, lessons learned, stable project facts.
- Exclude anything derivable from the code or repository itself, one-off task details, transient state, secrets, API keys, and file contents.
- Each fact must stand alone without the conversation.
- At most 15 facts. Return [] if nothing durable was said."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mem_index_path_for(db_path: str) -> str:
    """Derives the atomic-memory usearch index path for a database file."""
    return os.path.splitext(db_path)[0] + ".mem.usearch"


def _load_mem_index(db_path: str):
    try:
        from usearch.index import Index
    except ImportError:
        return None
    index_path = mem_index_path_for(db_path)
    if not os.path.exists(index_path):
        return None
    try:
        return Index.restore(index_path)
    except Exception:
        return None


def _add_to_mem_index(db_path: str, memory_id: int, vector: list[float]):
    try:
        from usearch.index import Index
    except ImportError:
        return
    index_path = mem_index_path_for(db_path)
    index = Index(ndim=len(vector), metric="cosine")
    if os.path.exists(index_path):
        try:
            index.load(index_path)
        except Exception:
            pass
    keys_arr = np.array([memory_id], dtype=np.int64)
    vectors_matrix = np.array([vector], dtype=np.float32).reshape(1, -1)
    try:
        if len(index) > 0 and memory_id in index:
            index.remove(keys_arr)
    except Exception:
        pass
    index.add(keys_arr, vectors_matrix)
    index.save(index_path)


def _remove_from_mem_index(db_path: str, memory_ids: list[int]):
    try:
        from usearch.index import Index
    except ImportError:
        return
    index_path = mem_index_path_for(db_path)
    if not os.path.exists(index_path):
        return
    try:
        index = Index.restore(index_path)
        if index is None or len(index) == 0:
            return
        present = [k for k in memory_ids if k in index]
        if not present:
            return
        for key in present:
            try:
                index.remove(np.int64(key))
            except Exception:
                pass
        # Mirror db.py: never persist an emptied index (usearch can segfault
        # reloading one); a fresh index is rebuilt lazily on the next add.
        if len(index) == 0:
            os.remove(index_path)
        else:
            index.save(index_path)
    except Exception:
        pass


def project_key_for(cwd: str | None) -> str | None:
    """Returns a stable project key for cwd: the git toplevel basename if cwd
    is in a git repo, else the cwd basename. None when cwd is falsy."""
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.basename(result.stdout.strip())
    except Exception:
        pass
    return os.path.basename(os.path.abspath(cwd))


def _get_llm(llm=None):
    if llm is not None:
        return llm
    from llm_client import LLMClient
    return LLMClient()


def _ensure_db(db_path: str = None) -> str:
    resolved = resolve_db_path(db_path)
    init_db(resolved)
    return resolved


def _embed(llm, text: str):
    if getattr(llm, "provider", "none") == "none":
        return None
    try:
        return llm.get_embedding(text)
    except Exception:
        return None


def _find_duplicate(db_path: str, vector) -> int | None:
    """Returns the id of an existing live fact nearly identical to vector, else None."""
    if vector is None:
        return None
    index = _load_mem_index(db_path)
    if index is None or len(index) == 0:
        return None
    try:
        matches = index.search(np.array(vector, dtype=np.float32), 1)
        if len(matches) == 0:
            return None
        key = int(matches[0].key)
        similarity = 1.0 - float(matches[0].distance)
        if similarity < DUP_SIMILARITY:
            return None
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM atomic_memories WHERE id = ? AND superseded_by IS NULL",
                (key,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _find_supersede_candidate(db_path: str, vector) -> tuple[int, float] | None:
    """Returns (id, similarity) of the top live fact whose similarity to vector
    is in [SUPERSEDE_LOW, DUP_SIMILARITY), else None."""
    if vector is None:
        return None
    index = _load_mem_index(db_path)
    if index is None or len(index) == 0:
        return None
    try:
        matches = index.search(np.array(vector, dtype=np.float32), 1)
        if len(matches) == 0:
            return None
        key = int(matches[0].key)
        similarity = 1.0 - float(matches[0].distance)
        if not (SUPERSEDE_LOW <= similarity < DUP_SIMILARITY):
            return None
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM atomic_memories WHERE id = ? AND superseded_by IS NULL",
                (key,),
            ).fetchone()
            return (row[0], similarity) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def add_memory(fact: str, category: str = None, entities: list[str] = None,
               agent_id: str = None, run_id: str = None, project: str = None,
               db_path: str = None, llm=None) -> dict:
    """Stores a single atomic fact verbatim. project=None means a global fact.
    A near (not duplicate) match in [0.80, 0.95) marks the old fact superseded.
    Returns {id, fact, duplicate_of, superseded}."""
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("fact must be a non-empty string")
    if category and category not in VALID_CATEGORIES:
        category = "fact"
    resolved = _ensure_db(db_path)
    llm = _get_llm(llm)
    vector = _embed(llm, fact)

    dup_id = _find_duplicate(resolved, vector)
    if dup_id is not None:
        return {"id": dup_id, "fact": fact, "duplicate_of": dup_id, "superseded": None}

    supersede = _find_supersede_candidate(resolved, vector)

    now = _now()
    blob = np.array(vector, dtype=np.float32).tobytes() if vector is not None else None
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO atomic_memories (fact, agent_id, run_id, category, project, embedding_blob, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fact, agent_id, run_id, category, project, blob, now, now),
        )
        memory_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO atomic_memories_fts (memory_id, fact) VALUES (?, ?)",
            (memory_id, fact),
        )
        for entity in set(e.strip().lower() for e in (entities or []) if e and e.strip()):
            cursor.execute(
                "INSERT OR IGNORE INTO memory_entities (memory_id, entity) VALUES (?, ?)",
                (memory_id, entity),
            )
        superseded_id = None
        if supersede is not None:
            cursor.execute(
                "UPDATE atomic_memories SET superseded_by = ?, updated_at = ? "
                "WHERE id = ? AND superseded_by IS NULL",
                (memory_id, now, supersede[0]),
            )
            if cursor.rowcount:
                superseded_id = supersede[0]
        conn.commit()
    finally:
        conn.close()
    if vector is not None:
        _add_to_mem_index(resolved, memory_id, vector)
    return {"id": memory_id, "fact": fact, "duplicate_of": None, "superseded": superseded_id}


def extract_and_store(transcript_text: str, agent_id: str = None, run_id: str = None,
                      project: str = None, db_path: str = None, llm=None,
                      debug_log=None) -> list[dict]:
    """LLM-extracts atomic facts from a transcript and stores them.

    Returns the list of stored facts. Returns [] without storing when no chat
    model is configured (CHAT_MODEL=none) — verbatim transcript storage would
    only add noise. debug_log, when given, receives one line explaining any
    zero-store outcome (llm failure vs unparseable output vs all duplicates),
    so callers' logs can distinguish failure from a genuine no-op.
    """
    def _note(reason):
        if debug_log:
            try:
                debug_log(reason)
            except Exception:
                pass

    llm = _get_llm(llm)
    if getattr(llm, "chat_model", "none") == "none":
        _note("skipped: no chat model configured")
        return []
    transcript_text = (transcript_text or "").strip()
    if len(transcript_text) < 200:
        _note("skipped: transcript under 200 chars")
        return []
    try:
        raw = llm.generate_completion(EXTRACTION_SYSTEM, transcript_text[-12000:])
    except Exception as e:
        _note(f"completion failed: {e}")
        return []
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        candidates = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Salvage a JSON array embedded in surrounding chatter
        # (e.g. "[...]\n```\nExplanation…" from a chatty model).
        try:
            start = raw.index("[")
            candidates, _ = json.JSONDecoder().raw_decode(raw[start:])
        except (ValueError, json.JSONDecodeError):
            _note(f"unparseable LLM output: {raw[:160]!r}")
            return []
    stored = []
    duplicates = 0
    for item in candidates[:15] if isinstance(candidates, list) else []:
        if not isinstance(item, dict) or not item.get("fact"):
            continue
        result = add_memory(
            fact=str(item["fact"]),
            category=item.get("category"),
            entities=item.get("entities") if isinstance(item.get("entities"), list) else None,
            agent_id=agent_id,
            run_id=run_id,
            project=project,
            db_path=db_path,
            llm=llm,
        )
        if result["duplicate_of"] is None:
            stored.append(result)
        else:
            duplicates += 1
    if not stored:
        n = len(candidates) if isinstance(candidates, list) else 0
        _note(f"stored 0: {n} candidates, {duplicates} duplicates")
    return stored


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "you", "your", "please",
    "can", "could", "would", "should", "want", "need", "use", "using",
    "make", "help", "how", "what", "when", "where", "why", "are", "was",
    "have", "has", "not", "all", "any", "but", "get", "set", "out", "new",
}


def _fts_tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9_]{3,}", query.lower())
            if t not in _STOPWORDS][:12]


def search_memories(query: str, agent_id: str = None, top: int = 8,
                    db_path: str = None, llm=None,
                    min_score: float = DEFAULT_MIN_SCORE,
                    project: str = None) -> list[dict]:
    """Hybrid search over live atomic facts. Returns [] on weak matches so
    callers don't inject noise. When project is given, returns that project's
    facts plus global facts (project IS NULL), with project facts boosted."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    query = (query or "").strip()
    if not query:
        return []

    # Semantic signal
    semantic = []  # list of (memory_id, similarity)
    llm = _get_llm(llm)
    vector = _embed(llm, query)
    if vector is not None:
        index = _load_mem_index(resolved)
        if index is not None and len(index) > 0:
            try:
                matches = index.search(np.array(vector, dtype=np.float32), min(top * 3, len(index)))
                semantic = [(int(m.key), 1.0 - float(m.distance)) for m in matches]
            except Exception:
                semantic = []

    conn = get_connection(resolved)
    try:
        try:
            conn.execute("SELECT 1 FROM atomic_memories LIMIT 1")
        except sqlite3.OperationalError:
            return []

        # Keyword signal
        keyword = []
        tokens = _fts_tokens(query)
        if tokens:
            match_expr = " OR ".join(f'"{t}"' for t in tokens)
            try:
                rows = conn.execute(
                    "SELECT memory_id FROM atomic_memories_fts WHERE atomic_memories_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (match_expr, top * 3),
                ).fetchall()
                keyword = [int(r[0]) for r in rows]
            except sqlite3.OperationalError:
                keyword = []

        # Entity signal
        entity_hits = []
        if tokens:
            placeholders = ",".join("?" for _ in tokens)
            rows = conn.execute(
                f"SELECT DISTINCT memory_id FROM memory_entities WHERE entity IN ({placeholders})",
                tokens,
            ).fetchall()
            entity_hits = [int(r[0]) for r in rows]

        # Gate: drop weak semantic matches entirely; bail if no signal remains.
        semantic = [(mid, sim) for mid, sim in semantic if sim >= min_score]
        if not semantic and not keyword and not entity_hits:
            return []

        # Reciprocal rank fusion across the three signals.
        scores = {}
        for rank, (mid, _sim) in enumerate(semantic):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (60 + rank + 1)
        for rank, mid in enumerate(keyword):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (60 + rank + 1)
        for rank, mid in enumerate(entity_hits):
            scores[mid] = scores.get(mid, 0.0) + 0.5 / (60 + rank + 1)
        if not scores:
            return []
        ranked_ids = [mid for mid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]

        placeholders = ",".join("?" for _ in ranked_ids)
        sql = (
            f"SELECT id, fact, category, agent_id, updated_at, project, retrieval_count "
            f"FROM atomic_memories WHERE id IN ({placeholders}) AND superseded_by IS NULL"
        )
        params = list(ranked_ids)
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        if project:
            sql += " AND (project = ? OR project IS NULL)"
            params.append(project)
        rows = {r[0]: r for r in conn.execute(sql, params).fetchall()}

        # Stable boost: same-project facts rank above globals at equal score.
        if project:
            for mid, row in rows.items():
                if row[5] == project:
                    scores[mid] = scores.get(mid, 0.0) + 0.01
        # Tiebreaks: higher retrieval_count, then more recent updated_at.
        by_recency = sorted(rows, key=lambda mid: rows[mid][4] or "", reverse=True)
        ranked_ids = sorted(
            by_recency,
            key=lambda mid: (-scores.get(mid, 0.0), -(rows[mid][6] or 0)),
        )

        sim_by_id = dict(semantic)
        results = []
        for mid in ranked_ids:
            if mid not in rows:
                continue
            _id, fact, category, agent, updated_at, row_project, retrieval_count = rows[mid]
            results.append({
                "id": _id, "fact": fact, "category": category,
                "agent_id": agent, "updated_at": updated_at,
                "project": row_project,
                "similarity": round(sim_by_id.get(mid, 0.0), 3),
            })
            if len(results) >= top:
                break

        if results:
            id_placeholders = ",".join("?" for _ in results)
            conn.execute(
                f"UPDATE atomic_memories SET retrieval_count = retrieval_count + 1, "
                f"last_retrieved_at = ? WHERE id IN ({id_placeholders})",
                [_now()] + [r["id"] for r in results],
            )
            conn.commit()
    finally:
        conn.close()

    return results


def format_facts(results: list[dict], max_chars: int = 3000, include_date: bool = True) -> str:
    """Formats facts as compact bullets for context injection.

    include_date=False omits the date from the suffix (cache-stable rendering).
    """
    lines = []
    total = 0
    for r in results:
        if include_date:
            date = (r.get("updated_at") or "")[:10]
            suffix = f" ({r['category']}, {date})" if r.get("category") else (f" ({date})" if date else "")
        else:
            suffix = f" ({r['category']})" if r.get("category") else ""
        line = f"- {r['fact']}{suffix}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def get_memory(memory_id: int, db_path: str = None) -> dict | None:
    resolved = resolve_db_path(db_path)
    conn = get_connection(resolved)
    try:
        row = conn.execute(
            "SELECT id, fact, category, agent_id, run_id, created_at, updated_at, superseded_by "
            "FROM atomic_memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        entities = [r[0] for r in conn.execute(
            "SELECT entity FROM memory_entities WHERE memory_id = ?", (memory_id,)
        ).fetchall()]
    finally:
        conn.close()
    return {"id": row[0], "fact": row[1], "category": row[2], "agent_id": row[3],
            "run_id": row[4], "created_at": row[5], "updated_at": row[6],
            "superseded_by": row[7], "entities": entities}


def update_memory(memory_id: int, fact: str, db_path: str = None, llm=None) -> bool:
    fact = (fact or "").strip()
    if not fact:
        raise ValueError("fact must be a non-empty string")
    resolved = resolve_db_path(db_path)
    llm = _get_llm(llm)
    vector = _embed(llm, fact)
    blob = np.array(vector, dtype=np.float32).tobytes() if vector is not None else None
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE atomic_memories SET fact = ?, embedding_blob = ?, updated_at = ? WHERE id = ?",
            (fact, blob, _now(), memory_id),
        )
        if cursor.rowcount == 0:
            return False
        cursor.execute("DELETE FROM atomic_memories_fts WHERE memory_id = ?", (memory_id,))
        cursor.execute("INSERT INTO atomic_memories_fts (memory_id, fact) VALUES (?, ?)", (memory_id, fact))
        conn.commit()
    finally:
        conn.close()
    if vector is not None:
        _add_to_mem_index(resolved, memory_id, vector)
    return True


def delete_memory(memory_id: int, db_path: str = None) -> bool:
    resolved = resolve_db_path(db_path)
    conn = get_connection(resolved)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM atomic_memories WHERE id = ?", (memory_id,))
        deleted = cursor.rowcount > 0
        cursor.execute("DELETE FROM atomic_memories_fts WHERE memory_id = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()
    if deleted:
        _remove_from_mem_index(resolved, [memory_id])
    return deleted


def list_entities(db_path: str = None) -> list[dict]:
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    conn = get_connection(resolved)
    try:
        try:
            rows = conn.execute(
                "SELECT e.entity, COUNT(*) FROM memory_entities e "
                "JOIN atomic_memories m ON m.id = e.memory_id AND m.superseded_by IS NULL "
                "GROUP BY e.entity ORDER BY COUNT(*) DESC, e.entity"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"entity": r[0], "count": r[1]} for r in rows]


def entity_graph(db_path: str = None, max_nodes: int = 60, min_cooccurrence: int = 1) -> dict:
    """A co-occurrence graph over memory entities: nodes are entities (colored by
    their dominant memory category), edges connect entities that appear together
    in the same live memory. Shaped exactly like the concept graph
    ({id,name,definition,category} nodes; {id,source,target,relationship,description}
    edges) so the web SVG engine renders it directly. Empty graph on no data."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return {"nodes": [], "edges": []}
    conn = get_connection(resolved)
    try:
        try:
            top = conn.execute(
                "SELECT e.entity, COUNT(*) c "
                "FROM memory_entities e JOIN atomic_memories m "
                "  ON m.id = e.memory_id AND m.superseded_by IS NULL "
                "GROUP BY e.entity ORDER BY c DESC, e.entity LIMIT ?",
                (max_nodes,),
            ).fetchall()
            if not top:
                return {"nodes": [], "edges": []}
            cat_rows = conn.execute(
                "SELECT e.entity, COALESCE(m.category,'general') cat, COUNT(*) c "
                "FROM memory_entities e JOIN atomic_memories m "
                "  ON m.id = e.memory_id AND m.superseded_by IS NULL "
                "GROUP BY e.entity, cat",
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT a.entity, b.entity, COUNT(*) c "
                "FROM memory_entities a JOIN memory_entities b "
                "  ON a.memory_id = b.memory_id AND a.entity < b.entity "
                "JOIN atomic_memories m ON m.id = a.memory_id AND m.superseded_by IS NULL "
                "GROUP BY a.entity, b.entity HAVING c >= ?",
                (min_cooccurrence,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"nodes": [], "edges": []}
    finally:
        conn.close()

    keep = {r[0] for r in top}
    best = {}  # entity -> (count, dominant category)
    for ent, cat, c in cat_rows:
        if ent in keep and c > best.get(ent, (0, None))[0]:
            best[ent] = (c, cat)

    ids = {ent: i + 1 for i, (ent, _) in enumerate(top)}
    nodes = [{"id": ids[ent], "name": ent, "definition": f"In {c} memories",
              "category": best.get(ent, (0, "general"))[1]} for ent, c in top]

    edges = []
    for a, b, c in edge_rows:
        if a in keep and b in keep:
            edges.append({"id": len(edges) + 1, "source": a, "target": b,
                          "relationship": "co-occurs",
                          "description": f"Together in {c} memories"})
    return {"nodes": nodes, "edges": edges}


def list_memories(limit: int = 50, project: str = None, category: str = None,
                  include_superseded: bool = False, db_path: str = None) -> list[dict]:
    """Lists facts (live by default), newest first."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    conn = get_connection(resolved)
    try:
        sql = ("SELECT id, fact, category, project, retrieval_count, updated_at, superseded_by "
               "FROM atomic_memories WHERE 1=1 ")
        params = []
        if not include_superseded:
            sql += "AND superseded_by IS NULL "
        if project:
            sql += "AND project = ? "
            params.append(project)
        if category:
            sql += "AND category = ? "
            params.append(category)
        sql += "ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"id": r[0], "fact": r[1], "category": r[2], "project": r[3],
             "retrieval_count": r[4], "updated_at": r[5], "superseded_by": r[6]}
            for r in rows]


def prune_stale(weeks: int = 8, dry_run: bool = False, db_path: str = None) -> list[int]:
    """Deletes (or, dry_run, lists) live facts never retrieved and not updated
    in the last `weeks` weeks. Returns the affected ids."""
    from datetime import timedelta
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()
    conn = get_connection(resolved)
    try:
        try:
            rows = conn.execute(
                "SELECT id FROM atomic_memories WHERE superseded_by IS NULL "
                "AND retrieval_count = 0 AND updated_at < ?", (cutoff,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        ids = [r[0] for r in rows]
        if not dry_run and ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM atomic_memories WHERE id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM atomic_memories_fts WHERE memory_id IN ({placeholders})", ids)
            conn.commit()
    finally:
        conn.close()
    if not dry_run and ids:
        _remove_from_mem_index(resolved, ids)
    return ids


def stats(db_path: str = None) -> dict:
    """Returns {total, by_category, by_project, total_retrievals, never_retrieved}."""
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return {"total": 0, "by_category": {}, "by_project": {}, "total_retrievals": 0, "never_retrieved": 0}
    conn = get_connection(resolved)
    try:
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM atomic_memories WHERE superseded_by IS NULL").fetchone()[0]
            by_category = dict(conn.execute(
                "SELECT COALESCE(category,'(none)'), COUNT(*) FROM atomic_memories "
                "WHERE superseded_by IS NULL GROUP BY category").fetchall())
            by_project = dict(conn.execute(
                "SELECT COALESCE(project,'(global)'), COUNT(*) FROM atomic_memories "
                "WHERE superseded_by IS NULL GROUP BY project ORDER BY COUNT(*) DESC LIMIT 5").fetchall())
            total_retrievals = conn.execute(
                "SELECT COALESCE(SUM(retrieval_count),0) FROM atomic_memories").fetchone()[0]
            never_retrieved = conn.execute(
                "SELECT COUNT(*) FROM atomic_memories WHERE superseded_by IS NULL "
                "AND retrieval_count = 0").fetchone()[0]
        except sqlite3.OperationalError:
            return {"total": 0, "by_category": {}, "by_project": {}, "total_retrievals": 0, "never_retrieved": 0}
    finally:
        conn.close()
    return {"total": total, "by_category": by_category, "by_project": by_project,
            "total_retrievals": total_retrievals, "never_retrieved": never_retrieved}


MEM_LEDGER_PATH = os.path.expanduser("~/.psyche/mem_ledger.jsonl")

CACHE_DISCOUNT = {
    "anthropic": 0.9,
    "openai": 0.5,
    "gemini": 0.75,
    "ollama": 0.0,
    "local": 0.0,
    "none": 0.0,
}


def ledger_summary(path: str = None, with_transcripts: bool = False,
                   projects_root: str = None) -> dict:
    """Summarizes the hook injection ledger: total injections, facts and chars
    injected, and tokens injected estimated as chars/4 — which equals the
    re-derivation avoided (an injected fact is one the agent didn't re-derive).
    Also computes cache-exposure metrics and a per-provider modeled savings estimate.

    When with_transcripts=True, also reads Claude Code transcript JSONL files to
    produce measured cache metrics (cache_read_total, cache_creation_total, etc.).
    """
    path = path or MEM_LEDGER_PATH
    total_injections = total_facts = total_chars = 0
    session_start_count = prompt_submit_count = prompt_submit_facts = 0
    block_hashes: set = set()
    # For transcript pass: list of (session_id, cwd) for session_start events.
    session_starts: list = []
    # For per-project block-change metric: cwd -> set of block_hashes.
    cwd_block_hashes: dict = {}
    # For block_tokens approximation: sum of session_start chars.
    session_start_chars_total = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total_injections += 1
                total_facts += int(entry.get("count", 0))
                total_chars += int(entry.get("chars", 0))
                event = entry.get("event", "")
                if event == "session_start":
                    session_start_count += 1
                    session_start_chars_total += int(entry.get("chars", 0))
                    bh = entry.get("block_hash")
                    if bh:
                        block_hashes.add(bh)
                    cwd = entry.get("cwd")
                    session_starts.append((entry.get("session_id", ""), cwd))
                    # Per-project block-hash grouping.
                    bucket = cwd if cwd is not None else "(unknown)"
                    if bh:
                        cwd_block_hashes.setdefault(bucket, set()).add(bh)
                elif event == "prompt_submit":
                    prompt_submit_count += 1
                    prompt_submit_facts += int(entry.get("count", 0))
    except OSError:
        pass
    distinct_session_blocks = len(block_hashes)
    # Per-project block-change metric: sum distinct_in_group - 1 per group.
    session_block_changes = sum(
        max(0, len(hashes) - 1) for hashes in cwd_block_hashes.values()
    )
    tokens_injected = total_chars // 4
    provider = (
        os.getenv("CHAT_PROVIDER", "").lower()
        or os.getenv("LLM_PROVIDER", "anthropic").lower()
    )
    discount = CACHE_DISCOUNT.get(provider, CACHE_DISCOUNT["anthropic"])
    estimated_savings_tokens = int(tokens_injected * discount)
    result = {
        "total_injections": total_injections,
        "total_facts": total_facts,
        "total_chars": total_chars,
        "tokens_injected": tokens_injected,
        "session_start_count": session_start_count,
        "distinct_session_blocks": distinct_session_blocks,
        "session_block_changes": session_block_changes,
        "prompt_submit_count": prompt_submit_count,
        "prompt_submit_facts": prompt_submit_facts,
        "estimated_savings_tokens": estimated_savings_tokens,
        "cache_discount_used": discount,
        "cache_provider_used": provider or "anthropic",
    }
    if with_transcripts:
        try:
            import transcript_usage  # noqa: PLC0415
            cache_read_total = cache_creation_total = input_uncached_total = 0
            measured_sessions = warm_sessions = 0
            for sid, cwd in session_starts:
                kwargs = {}
                if projects_root is not None:
                    kwargs["projects_root"] = projects_root
                tpath = transcript_usage.transcript_path(sid, cwd, **kwargs)
                if tpath is None:
                    continue
                usage = transcript_usage.parse_transcript_usage(tpath)
                if usage["turns"] == 0:
                    continue
                measured_sessions += 1
                cache_read_total += usage["cache_read"]
                cache_creation_total += usage["cache_creation"]
                input_uncached_total += usage["input_uncached"]
                if usage["cache_read"] > 0:
                    warm_sessions += 1
            measured_coverage = (
                measured_sessions / session_start_count if session_start_count else 0
            )
            denom = cache_read_total + cache_creation_total + input_uncached_total
            cache_read_share = cache_read_total / denom if denom else 0
            # block_tokens: average session_start chars // 4 (approximation).
            block_tokens = (
                session_start_chars_total // session_start_count // 4
                if session_start_count else 0
            )
            block_tokens_exact = False
            psyche_avoided_tokens = int(block_tokens * (1.25 - 0.1) * warm_sessions)
            psyche_block_read_tokens = int(block_tokens * 0.1 * warm_sessions)
            result.update({
                "measured_sessions": measured_sessions,
                "measured_coverage": measured_coverage,
                "cache_read_total": cache_read_total,
                "cache_creation_total": cache_creation_total,
                "input_uncached_total": input_uncached_total,
                "cache_read_share": cache_read_share,
                "warm_sessions": warm_sessions,
                "block_tokens": block_tokens,
                "block_tokens_exact": block_tokens_exact,
                "psyche_avoided_tokens": psyche_avoided_tokens,
                "psyche_block_read_tokens": psyche_block_read_tokens,
            })
        except Exception:
            pass
    return result


def standing_fact_rows(top: int = 12, db_path: str = None, project: str = None,
                       stable: bool = False) -> list[dict]:
    """Durable preference/decision/lesson facts for session-start injection.

    stable=True orders by id ASC (cache-stable; new facts append, existing
    order never changes). stable=False keeps the legacy updated_at DESC order.
    With a project, project facts are listed before globals in both modes.
    """
    resolved = resolve_db_path(db_path)
    if not os.path.exists(resolved):
        return []
    conn = get_connection(resolved)
    try:
        try:
            sql = (
                "SELECT id, fact, category, agent_id, updated_at, project FROM atomic_memories "
                "WHERE superseded_by IS NULL AND category IN ('preference','decision','lesson') "
            )
            params = []
            if project:
                sql += "AND (project = ? OR project IS NULL) "
                params.append(project)
                if stable:
                    sql += "ORDER BY (project IS NULL), id ASC LIMIT ?"
                else:
                    sql += "ORDER BY (project IS NULL), updated_at DESC LIMIT ?"
            else:
                if stable:
                    sql += "ORDER BY id ASC LIMIT ?"
                else:
                    sql += "ORDER BY updated_at DESC LIMIT ?"
            params.append(top)
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [{"id": r[0], "fact": r[1], "category": r[2], "agent_id": r[3],
             "updated_at": r[4], "project": r[5]}
            for r in rows]


def standing_facts(top: int = 12, db_path: str = None, max_chars: int = 1500) -> str:
    return format_facts(standing_fact_rows(top, db_path), max_chars=max_chars)

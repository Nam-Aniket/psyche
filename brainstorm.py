"""GBrain-inspired brainstorm layer for Psyche: cross-topic collision engine + gap reporter.

Purely additive. Reads embeddings from existing per-topic DBs; writes hypotheses to its
own ~/.psyche/brainstorm.db. Does not touch any existing retrieval/memory/graph code.
"""
import glob
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone

import numpy as np

import db

_COLLIDE_SYSTEM = (
    "You are a hypothesis generator. You are given two unrelated notes from the user's "
    "private knowledge base. Find a NON-OBVIOUS connection and state it as a single "
    "FALSIFIABLE hypothesis - a claim that could be proven wrong by evidence. Reject vague "
    "'these both relate to X' observations. Then give the single cheapest real-world test "
    "that could kill it. Respond ONLY with JSON: "
    '{"hypothesis": "...", "kill_test": "..."}'
)


def collide(text_a, text_b, llm):
    """Return {'hypothesis','kill_test'} bridging the two texts, or None after one retry."""
    from build_graph import clean_json_text
    prompt = f"NOTE A:\n{text_a}\n\nNOTE B:\n{text_b}"
    for _ in range(2):
        raw = llm.generate_completion(_COLLIDE_SYSTEM, prompt)
        try:
            data = json.loads(clean_json_text(raw))
            if "hypothesis" in data and "kill_test" in data:
                return {"hypothesis": data["hypothesis"], "kill_test": data["kill_test"]}
        except (json.JSONDecodeError, TypeError):
            pass
        prompt = "Your last reply was not valid JSON. " + prompt  # stricter retry
    return None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ledger_path():
    return db.resolve_db_path("brainstorm.db")


def _ledger_conn(path=None):
    conn = sqlite3.connect(path or _ledger_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            kill_test TEXT,
            topic_a TEXT, chunk_a INTEGER,
            topic_b TEXT, chunk_b INTEGER,
            snippet_a TEXT, snippet_b TEXT,
            drift REAL,
            embedding_blob BLOB,
            status TEXT NOT NULL DEFAULT 'new',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hypotheses)")}
    if "realized_sim" not in cols:
        conn.execute("ALTER TABLE hypotheses ADD COLUMN realized_sim REAL")
    conn.commit()
    return conn


_HYP_COLS = ["id", "text", "kill_test", "topic_a", "chunk_a", "topic_b", "chunk_b",
             "snippet_a", "snippet_b", "drift", "realized_sim", "status", "notes",
             "created_at", "updated_at"]


def _pair_exists(path, topic_a, chunk_a, topic_b, chunk_b):
    """True if this collided pair (either ordering) is already in the ledger."""
    conn = _ledger_conn(path)
    row = conn.execute(
        "SELECT 1 FROM hypotheses WHERE (topic_a=? AND chunk_a=? AND topic_b=? AND chunk_b=?) "
        "OR (topic_a=? AND chunk_a=? AND topic_b=? AND chunk_b=?) LIMIT 1",
        (topic_a, chunk_a, topic_b, chunk_b, topic_b, chunk_b, topic_a, chunk_a)).fetchone()
    conn.close()
    return row is not None


def insert_hypothesis(path, *, text, kill_test, topic_a, chunk_a, snippet_a,
                      topic_b, chunk_b, snippet_b, drift, embedding=None, realized_sim=None):
    conn = _ledger_conn(path)
    now = _now()
    blob = np.asarray(embedding, dtype=np.float32).tobytes() if embedding is not None else None
    cur = conn.execute(
        """INSERT INTO hypotheses
           (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
            drift, embedding_blob, status, notes, created_at, updated_at, realized_sim)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'new', NULL, ?, ?, ?)""",
        (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
         drift, blob, now, now, realized_sim))
    conn.commit()
    hid = cur.lastrowid
    conn.close()
    return hid


def list_hypotheses(path, status=None):
    conn = _ledger_conn(path)
    q = f"SELECT {', '.join(_HYP_COLS)} FROM hypotheses"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY created_at DESC"
    rows = [dict(zip(_HYP_COLS, r)) for r in conn.execute(q, args).fetchall()]
    conn.close()
    return rows


def update_hypothesis(path, hid, status=None, notes=None, text=None, kill_test=None, embedding=None):
    conn = _ledger_conn(path)
    sets, args = ["updated_at = ?"], [_now()]
    if status is not None:
        sets.append("status = ?"); args.append(status)
    if notes is not None:
        sets.append("notes = ?"); args.append(notes)
    if text is not None:
        sets.append("text = ?"); args.append(text)
    if kill_test is not None:
        sets.append("kill_test = ?"); args.append(kill_test)
    if embedding is not None:
        sets.append("embedding_blob = ?"); args.append(np.asarray(embedding, dtype=np.float32).tobytes())
    args.append(hid)
    conn.execute(f"UPDATE hypotheses SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()
    conn.close()


def _base_dir():
    """The ~/.psyche directory where topic DBs live."""
    return os.path.dirname(db.resolve_db_path("knowledge.db"))


def _topic_name(path):
    name = os.path.basename(path)
    if name == "knowledge.db":
        return "default"
    if name.startswith("topic_") and name.endswith(".db"):
        return name[len("topic_"):-len(".db")]
    return None


def discover_topics(base_dir=None):
    """Map topic name -> db path for knowledge.db + topic_*.db under base_dir."""
    base_dir = base_dir or _base_dir()
    out = {}
    for path in [os.path.join(base_dir, "knowledge.db")] + sorted(glob.glob(os.path.join(base_dir, "topic_*.db"))):
        if os.path.exists(path):
            t = _topic_name(path)
            if t:
                out[t] = path
    return out


def _embed_signature(db_path):
    """Return (embed_model, dim) for a topic DB, or (None, None) if unreadable."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='embed_model'").fetchone()
        model = row[0] if row else None
        blob = conn.execute("SELECT embedding_blob FROM embeddings LIMIT 1").fetchone()
        dim = len(np.frombuffer(blob[0], dtype=np.float32)) if blob else None
        conn.close()
        return (model, dim)
    except Exception:
        return (None, None)


import random

MIN_POOL = 50
MIN_CHUNK_CHARS = 200
DEDUP_THRESHOLD = 0.85
# ponytail: below this average line length a chunk is a table-of-contents / index /
# catalog / endnote block, not prose. Calibrated 2026-07-06 on the real corpus:
# junk chunks measured <=22, real prose >=32 (median 64). Retune if the corpus changes.
MIN_AVG_LINE = 30


def _is_prose(text):
    """Reject non-prose chunks (TOCs, indexes, catalogs) that pass the char floor but
    are just many short lines. True = looks like real prose worth colliding."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 2:
        return True  # a single block/paragraph — treat as prose
    return sum(len(ln) for ln in lines) / len(lines) >= MIN_AVG_LINE


class SparseCorpusError(Exception):
    pass


class NoChatModelError(Exception):
    pass


class IncompatibleTopicsError(Exception):
    pass


def _fetch_text(base_dir, topic, chunk_id):
    found = discover_topics(base_dir)
    conn = db.get_connection(found[topic])
    recs = db.get_chunks_by_ids(conn, [chunk_id])
    conn.close()
    return recs[0]["text"] if recs else ""


def generate_hypotheses(count=5, drift=0.5, topics=None, llm=None,
                        base_dir=None, ledger_path=None, seed=None):
    if llm is None:
        from llm_client import LLMClient
        llm = LLMClient()
    # Raw-pairs mode: no chat model in Psyche, so the engine returns the collided pairs
    # and the CALLING llm (via MCP) writes the falsifiable hypothesis. This is the C2
    # design. If a chat model IS configured, the engine writes hypotheses itself.
    raw_mode = getattr(llm, "chat_model", "none") == "none"

    ledger_path = ledger_path or _ledger_path()
    found = discover_topics(base_dir)
    kept, skipped = select_compatible_topics(found, requested=topics)
    if not kept:
        raise IncompatibleTopicsError("no embedding-compatible topics to collide.")

    matrix, index = build_pool(kept)
    if matrix.shape[0] < MIN_POOL:
        raise SparseCorpusError(f"pooled corpus has {matrix.shape[0]} chunks (< {MIN_POOL}); ingest more first.")

    band = drift_band(drift)
    results, skipped_pairs, attempts = [], 0, 0
    max_attempts = count * 12
    if seed:
        # Seeded mode: anchor each collision on notes RELEVANT to the seed topic, so
        # one side of every pair is on-topic and the partner is the distant surprise.
        seed_vec = llm.get_embedding(seed)
        order = _relevance_order(seed_vec, matrix)[:max(count * 15, 50)]
    else:
        order = list(range(len(index)))
        random.shuffle(order)

    while len(results) < count and attempts < max_attempts and order:
        attempts += 1
        anchor = order.pop(0)   # front = most-relevant-first when seeded; random otherwise
        text_a = _fetch_text(base_dir, index[anchor]["topic"], index[anchor]["chunk_id"])
        if len(text_a) < MIN_CHUNK_CHARS or not _is_prose(text_a):
            continue
        p = pick_partner(anchor, matrix, index, band)
        if p is None:
            wlo, whi = band
            p = pick_partner(anchor, matrix, index, (wlo - 0.05, whi + 0.05))  # widen once
        if p is None:
            continue
        p_idx, realized = p
        text_b = _fetch_text(base_dir, index[p_idx]["topic"], index[p_idx]["chunk_id"])
        if len(text_b) < MIN_CHUNK_CHARS or not _is_prose(text_b):
            continue
        ta, ca = index[anchor]["topic"], index[anchor]["chunk_id"]
        tb, cb = index[p_idx]["topic"], index[p_idx]["chunk_id"]
        if _pair_exists(ledger_path, ta, ca, tb, cb):
            continue

        if raw_mode:
            # No chat model: hand the raw collided pair to the calling LLM to write up.
            hid = insert_hypothesis(
                ledger_path, text="(raw collision - calling LLM to write the hypothesis)",
                kill_test=None, topic_a=ta, chunk_a=ca, snippet_a=text_a[:300],
                topic_b=tb, chunk_b=cb, snippet_b=text_b[:300], drift=drift, embedding=None,
                realized_sim=realized)
            results.append({
                "id": hid, "needs_hypothesis": True, "drift": drift,
                "source_a": {"topic": ta, "snippet": text_a[:300]},
                "source_b": {"topic": tb, "snippet": text_b[:300]},
            })
            continue

        out = collide(text_a, text_b, llm)
        if out is None:
            skipped_pairs += 1
            continue
        emb = np.asarray(llm.get_embedding(out["hypothesis"]), dtype=np.float32)
        if is_duplicate(ledger_path, emb, DEDUP_THRESHOLD):
            continue
        hid = insert_hypothesis(
            ledger_path, text=out["hypothesis"], kill_test=out["kill_test"],
            topic_a=ta, chunk_a=ca, snippet_a=text_a[:300],
            topic_b=tb, chunk_b=cb, snippet_b=text_b[:300],
            drift=drift, embedding=emb, realized_sim=realized)
        results.append({
            "id": hid, "hypothesis": out["hypothesis"], "kill_test": out["kill_test"], "drift": drift,
            "source_a": {"topic": ta, "snippet": text_a[:300]},
            "source_b": {"topic": tb, "snippet": text_b[:300]},
        })
    return results


def _cluster_label(members, index):
    """Label a cluster by its dominant topic + most common source."""
    topics = Counter(index[i]["topic"] for i in members)
    sources = Counter(index[i]["source"] for i in members)
    return {
        "topic": topics.most_common(1)[0][0],
        "source": sources.most_common(1)[0][0],
        "size": len(members),
    }


def report_gaps(topics=None, top=10, base_dir=None, ledger_path=None, num_clusters=None):
    """Report the most disconnected cluster pairs across the pooled corpus."""
    from build_graph import kmeans
    found = discover_topics(base_dir)
    kept, _ = select_compatible_topics(found, requested=topics)
    matrix, index = build_pool(kept)
    n = matrix.shape[0]
    if n < 2:
        return {"cluster_gaps": [], "isolated_concepts": [], "note": "not enough material yet"}

    k = num_clusters or max(2, min(12, int(np.sqrt(n))))
    labels, centroids = kmeans(matrix, k)

    members = {c: [i for i in range(n) if labels[i] == c] for c in range(k)}
    if sum(1 for c in members if members[c]) < 2:
        # kmeans collapsed everything into one cluster (can happen when the corpus is
        # very homogeneous or on a degenerate init) — no gaps to report this pass.
        return {"cluster_gaps": [], "isolated_concepts": [],
                "note": "clustering did not separate the corpus; try again or ingest more variety"}
    gaps = []
    for a in range(k):
        for b in range(a + 1, k):
            if not members[a] or not members[b]:
                continue
            sim = float(np.dot(centroids[a], centroids[b]))
            gaps.append({
                "cluster_a": _cluster_label(members[a], index),
                "cluster_b": _cluster_label(members[b], index),
                "similarity": sim,
            })
    gaps.sort(key=lambda g: g["similarity"])  # most distant first
    return {"cluster_gaps": gaps[:top], "isolated_concepts": []}


def _source_titles(conn):
    """chunk_id -> source title map, cheaply (no chunk text loaded)."""
    rows = conn.execute(
        "SELECT c.id, s.title FROM chunks c JOIN sources s ON c.source_id = s.id"
    ).fetchall()
    return {cid: title for cid, title in rows}


def build_pool(kept):
    """Load embeddings from each kept topic DB into one matrix + parallel index.

    Returns (matrix: np.ndarray [N, dim], index: list[{"topic","chunk_id","source"}]).
    Global identity is (topic, chunk_id) because chunk ids are only unique within a file.
    """
    vecs, index = [], []
    for topic, path in kept.items():
        conn = db.get_connection(path)
        titles = _source_titles(conn)
        for rec in db.get_all_embeddings_only(conn):
            emb = rec["embedding"]
            if emb is None:
                continue
            vecs.append(emb)
            index.append({"topic": topic, "chunk_id": rec["chunk_id"],
                          "source": titles.get(rec["chunk_id"], "?")})
        conn.close()
    matrix = np.array(vecs, dtype=np.float32) if vecs else np.empty((0, 0), dtype=np.float32)
    return matrix, index


# ponytail: band coefficients are the calibration knob, tuned to bge-small-en-v1.5 on
# Aniket's real corpus (measured 2026-07-06: cross-topic cosine spans ~0.30-0.78, median
# ~0.55). drift maps onto that real range: 0 -> [0.62,0.72] (mildest), 1 -> [0.32,0.42]
# (most distant found in practice). Re-measure + retune if the embedding model changes.
BAND_HI = 0.72      # upper edge at drift=0
BAND_SPAN = 0.10    # band width
BAND_SLOPE = 0.30   # how far the band slides down per unit drift


def drift_band(drift):
    """Return (low, high) cosine-similarity window for a drift in [0,1]."""
    hi = BAND_HI - BAND_SLOPE * drift
    return (hi - BAND_SPAN, hi)


def _cosims(anchor_vec, matrix):
    q = anchor_vec
    qn = np.linalg.norm(q)
    norms = np.linalg.norm(matrix, axis=1)
    norms = np.where(norms == 0, 1e-10, norms)
    if qn == 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    return np.dot(matrix, q) / (qn * norms)


def _relevance_order(seed_vec, matrix):
    """Indices of pooled chunks sorted by cosine relevance to the seed (most relevant first)."""
    sims = _cosims(np.asarray(seed_vec, dtype=np.float32), matrix)
    return list(np.argsort(sims)[::-1])


def pick_partner(anchor_idx, matrix, index, band):
    """Pick a partner row index for the anchor, preferring (a) different topic,
    (b) different source same topic, (c) same source. Returns index or None if band empty."""
    lo, hi = band
    sims = _cosims(matrix[anchor_idx], matrix)
    a = index[anchor_idx]
    tiers = {"diff_topic": [], "diff_source": [], "same": []}
    for j, s in enumerate(sims):
        if j == anchor_idx:
            continue
        if lo <= s <= hi:
            if index[j]["topic"] != a["topic"]:
                tiers["diff_topic"].append((j, s))
            elif index[j]["source"] != a["source"]:
                tiers["diff_source"].append((j, s))
            else:
                tiers["same"].append((j, s))
    for key in ("diff_topic", "diff_source", "same"):
        if tiers[key]:
            j, s = random.choice(tiers[key])   # sample the band, don't bunch at its edge
            return (j, float(s))
    return None


def is_duplicate(path, embedding, threshold=0.85):
    """True if `embedding` cosine >= threshold to ANY stored hypothesis (including killed)."""
    conn = _ledger_conn(path)
    rows = conn.execute("SELECT embedding_blob FROM hypotheses WHERE embedding_blob IS NOT NULL").fetchall()
    conn.close()
    q = np.asarray(embedding, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return False
    for (blob,) in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        vn = np.linalg.norm(v)
        if vn == 0:
            continue
        if float(np.dot(q, v) / (qn * vn)) >= threshold:
            return True
    return False


def select_compatible_topics(found, requested=None):
    """Keep topics sharing the majority (embed_model, dim); return (kept: dict, skipped: dict).

    requested: optional list of topic names to restrict to (None = all discovered).
    """
    names = list(found) if requested is None else [t for t in requested if t in found]
    sigs = {t: _embed_signature(found[t]) for t in names}
    counts = {}
    for sig in sigs.values():
        if sig[0] is not None:
            counts[sig] = counts.get(sig, 0) + 1
    if not counts:
        return ({}, {t: found[t] for t in names})
    majority = max(counts, key=counts.get)
    kept, skipped = {}, {}
    for t in names:
        (kept if sigs[t] == majority else skipped)[t] = found[t]
    return (kept, skipped)

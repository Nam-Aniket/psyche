"""GBrain-inspired brainstorm layer for Psyche: cross-topic collision engine + gap reporter.

Purely additive. Reads embeddings from existing per-topic DBs; writes hypotheses to its
own ~/.psyche/brainstorm.db. Does not touch any existing retrieval/memory/graph code.
"""
import glob
import json
import os
import sqlite3
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
    conn.commit()
    return conn


_HYP_COLS = ["id", "text", "kill_test", "topic_a", "chunk_a", "topic_b", "chunk_b",
             "snippet_a", "snippet_b", "drift", "status", "notes", "created_at", "updated_at"]


def insert_hypothesis(path, *, text, kill_test, topic_a, chunk_a, snippet_a,
                      topic_b, chunk_b, snippet_b, drift, embedding):
    conn = _ledger_conn(path)
    now = _now()
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    cur = conn.execute(
        """INSERT INTO hypotheses
           (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
            drift, embedding_blob, status, notes, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'new', NULL, ?, ?)""",
        (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
         drift, blob, now, now))
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


def update_hypothesis(path, hid, status=None, notes=None):
    conn = _ledger_conn(path)
    sets, args = ["updated_at = ?"], [_now()]
    if status is not None:
        sets.append("status = ?"); args.append(status)
    if notes is not None:
        sets.append("notes = ?"); args.append(notes)
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


# ponytail: band coefficients are the calibration knob. If first real runs on a given
# embedding model put the "interesting" collisions elsewhere, tune BAND_HI/BAND_SPAN here.
BAND_HI = 0.75      # upper edge at drift=0
BAND_SPAN = 0.15    # band width
BAND_SLOPE = 0.45   # how far the band slides down per unit drift


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
            return max(tiers[key], key=lambda t: t[1])[0]
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

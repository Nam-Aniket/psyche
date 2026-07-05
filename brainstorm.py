"""GBrain-inspired brainstorm layer for Psyche: cross-topic collision engine + gap reporter.

Purely additive. Reads embeddings from existing per-topic DBs; writes hypotheses to its
own ~/.psyche/brainstorm.db. Does not touch any existing retrieval/memory/graph code.
"""
import glob
import os
import sqlite3

import numpy as np

import db


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

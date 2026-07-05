"""Tests for the brainstorm layer (collision engine + gap reporter).

Synthetic vectors + temp SQLite DBs; no LLM calls (a fake stub stands in).
"""
import os
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import brainstorm


def _make_topic_db(path, embed_model="BAAI/bge-small-en-v1.5", dim=4, n=0):
    """Create a minimal Psyche-shaped topic DB with n chunks + embeddings.

    Embeddings are deterministic-random *directions* (seeded), NOT parallel vectors,
    so cosine similarities spread across a range — otherwise every pair is 1.0 and no
    drift band would ever match.
    """
    db.init_db(path)                # create schema
    conn = db.get_connection(path)
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('embed_model', ?)", (embed_model,))
    conn.execute("INSERT INTO sources (id, title, author, checksum, created_at) VALUES (1, 'src', 'a', ?, '2026-01-01')", (path,))
    for i in range(1, n + 1):
        conn.execute("INSERT INTO chunks (id, source_id, chunk_index, text) VALUES (?,1,?,?)",
                     (i, i, f"chunk text number {i} " * 20))
        rng = np.random.default_rng(1000 + i)               # varied directions
        vec = rng.standard_normal(dim).astype(np.float32)
        conn.execute("INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?,?)",
                     (i, vec.tobytes()))
    conn.commit()
    conn.close()


class TestTopicDiscovery(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=2)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=2)
        _make_topic_db(os.path.join(self.dir, "topic_odd.db"), embed_model="other-model", dim=8, n=2)

    def test_discover_maps_filenames_to_topic_names(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        self.assertEqual(found["default"], os.path.join(self.dir, "knowledge.db"))
        self.assertEqual(found["naval"], os.path.join(self.dir, "topic_naval.db"))
        self.assertEqual(found["odd"], os.path.join(self.dir, "topic_odd.db"))

    def test_compatible_set_drops_mismatched_model(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, skipped = brainstorm.select_compatible_topics(found, requested=None)
        self.assertIn("default", kept)
        self.assertIn("naval", kept)
        self.assertIn("odd", skipped)  # different embed_model -> dropped


if __name__ == "__main__":
    unittest.main()

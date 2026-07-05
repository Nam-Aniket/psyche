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


class TestPool(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=3)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=2)

    def test_pool_concatenates_and_tags_topic(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, _ = brainstorm.select_compatible_topics(found, requested=None)
        matrix, index = brainstorm.build_pool(kept)
        self.assertEqual(matrix.shape[0], 5)         # 3 + 2 chunks
        self.assertEqual(len(index), 5)
        topics = {row["topic"] for row in index}
        self.assertEqual(topics, {"default", "naval"})
        # identity is (topic, chunk_id); chunk_id 1 exists in BOTH topics
        pairs = {(r["topic"], r["chunk_id"]) for r in index}
        self.assertIn(("default", 1), pairs)
        self.assertIn(("naval", 1), pairs)
        # each index row carries a source label
        self.assertTrue(all("source" in r for r in index))


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = os.path.join(tempfile.mkdtemp(), "brainstorm.db")

    def test_insert_list_update_roundtrip(self):
        emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        hid = brainstorm.insert_hypothesis(
            self.ledger, text="H1", kill_test="ten emails",
            topic_a="default", chunk_a=1, snippet_a="a...",
            topic_b="naval", chunk_b=1, snippet_b="b...",
            drift=0.5, embedding=emb)
        self.assertIsInstance(hid, int)
        rows = brainstorm.list_hypotheses(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "new")
        self.assertEqual(rows[0]["topic_b"], "naval")

        brainstorm.update_hypothesis(self.ledger, hid, status="killed", notes="reality said no")
        killed = brainstorm.list_hypotheses(self.ledger, status="killed")
        self.assertEqual(len(killed), 1)
        self.assertEqual(killed[0]["notes"], "reality said no")
        self.assertEqual(brainstorm.list_hypotheses(self.ledger, status="new"), [])


class TestDedup(unittest.TestCase):
    def setUp(self):
        self.ledger = os.path.join(tempfile.mkdtemp(), "brainstorm.db")
        self.base = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        brainstorm.insert_hypothesis(
            self.ledger, text="stored", kill_test="t",
            topic_a="default", chunk_a=1, snippet_a="a",
            topic_b="naval", chunk_b=1, snippet_b="b",
            drift=0.5, embedding=self.base)
        brainstorm.update_hypothesis(self.ledger, 1, status="killed")

    def test_near_duplicate_of_killed_is_rejected(self):
        near = np.array([0.99, 0.01, 0.0], dtype=np.float32)   # cosine ~1.0
        self.assertTrue(brainstorm.is_duplicate(self.ledger, near, threshold=0.85))

    def test_distinct_vector_is_allowed(self):
        far = np.array([0.0, 1.0, 0.0], dtype=np.float32)      # orthogonal
        self.assertFalse(brainstorm.is_duplicate(self.ledger, far, threshold=0.85))


if __name__ == "__main__":
    unittest.main()

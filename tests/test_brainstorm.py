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


class TestProse(unittest.TestCase):
    def test_rejects_toc_and_index_like_chunks(self):
        toc = ("Table of Contents\nSection I: Start Here\nHow I Got Here\n"
               "Get Leads\n#1 Warm Outreach\n#2 Post Free Content")
        index = "456 INDEX Money and prices, 296-318\nas production, 88\nGold, 40-42\nSee also Banks"
        self.assertFalse(brainstorm._is_prose(toc))
        self.assertFalse(brainstorm._is_prose(index))

    def test_keeps_real_prose(self):
        prose = ("If your messaging is clear, straightforward, and focused on them, with each "
                 "communication adding value to their lives by teaching them something they did "
                 "not know, your outreach will be welcomed with open arms.")
        self.assertTrue(brainstorm._is_prose(prose))
        self.assertTrue(brainstorm._is_prose("one single long line with no breaks at all here friend"))


class TestSeed(unittest.TestCase):
    def test_relevance_order_ranks_by_cosine_to_seed(self):
        # seed points along axis 0; idx0 aligned, idx2 orthogonal
        matrix = np.array([
            [1.0, 0.0],   # idx0: aligned with seed -> most relevant
            [0.7, 0.7],   # idx1: partial
            [0.0, 1.0],   # idx2: orthogonal -> least relevant
        ], dtype=np.float32)
        seed_vec = np.array([1.0, 0.0], dtype=np.float32)
        order = brainstorm._relevance_order(seed_vec, matrix)
        self.assertEqual(order[0], 0)    # most relevant first
        self.assertEqual(order[-1], 2)   # least relevant last


class TestDriftAndPartner(unittest.TestCase):
    def test_drift_band_endpoints(self):
        # Calibrated to bge-small-en-v1.5 on the real corpus (cross-topic cosine ~0.30-0.78).
        lo, hi = brainstorm.drift_band(0.0)
        self.assertAlmostEqual(lo, 0.62, places=3)
        self.assertAlmostEqual(hi, 0.72, places=3)
        lo, hi = brainstorm.drift_band(0.5)
        self.assertAlmostEqual(lo, 0.47, places=3)
        self.assertAlmostEqual(hi, 0.57, places=3)
        lo, hi = brainstorm.drift_band(1.0)
        self.assertAlmostEqual(lo, 0.32, places=3)
        self.assertAlmostEqual(hi, 0.42, places=3)

    def test_partner_prefers_different_topic_in_band(self):
        # anchor at index 0 (topic default). Two in-band candidates:
        # idx1 same topic, idx2 different topic -> must pick idx2.
        matrix = np.array([
            [1.0, 0.0],     # anchor
            [0.7, 0.71],    # same-topic, in band ~0.7
            [0.7, 0.71],    # diff-topic, in band ~0.7
        ], dtype=np.float32)
        index = [
            {"topic": "default", "chunk_id": 1, "source": "s1"},
            {"topic": "default", "chunk_id": 2, "source": "s1"},
            {"topic": "naval",   "chunk_id": 9, "source": "s2"},
        ]
        p, _sim = brainstorm.pick_partner(0, matrix, index, band=(0.5, 0.9))
        self.assertEqual(index[p]["topic"], "naval")

    def test_partner_none_when_band_empty(self):
        matrix = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)  # sim 1.0, outside band
        index = [{"topic": "default", "chunk_id": 1, "source": "s"},
                 {"topic": "naval", "chunk_id": 1, "source": "s"}]
        self.assertIsNone(brainstorm.pick_partner(0, matrix, index, band=(0.2, 0.5)))


class _FakeLLM:
    """Stub with the LLMClient surface brainstorm uses."""
    def __init__(self, replies, chat_model="fake"):
        self.replies = list(replies)
        self.chat_model = chat_model
        self._embed_seed = 0

    def generate_completion(self, system, prompt):
        return self.replies.pop(0)

    def get_embedding(self, text):
        # distinct embedding per call so dedup doesn't collapse every hypothesis
        self._embed_seed += 1
        rng = np.random.default_rng(self._embed_seed)
        return rng.standard_normal(8).astype(np.float32).tolist()


class TestCollide(unittest.TestCase):
    def test_parses_valid_json(self):
        llm = _FakeLLM(['{"hypothesis": "H", "kill_test": "T"}'])
        out = brainstorm.collide("text a", "text b", llm)
        self.assertEqual(out, {"hypothesis": "H", "kill_test": "T"})

    def test_strips_markdown_fence(self):
        llm = _FakeLLM(['```json\n{"hypothesis": "H2", "kill_test": "T2"}\n```'])
        out = brainstorm.collide("a", "b", llm)
        self.assertEqual(out["hypothesis"], "H2")

    def test_retries_once_then_returns_none(self):
        llm = _FakeLLM(["not json", "still not json"])
        self.assertIsNone(brainstorm.collide("a", "b", llm))
        self.assertEqual(llm.replies, [])  # both attempts consumed


class TestGaps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Two clearly separated blobs so kmeans finds distant clusters.
        gpath = os.path.join(self.dir, "knowledge.db")
        db.init_db(gpath)
        conn = db.get_connection(gpath)
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('embed_model','BAAI/bge-small-en-v1.5')")
        conn.execute("INSERT INTO sources (id, title, author, checksum, created_at) VALUES (1,'s','a','gapchk','2026-01-01')")
        # Two near-orthogonal blobs WITH tiny jitter — identical vectors would trigger a
        # degenerate kmeans init (both seeds land on the same point). Jitter keeps every
        # vector distinct so kmeans separates the two blobs.
        rng = np.random.default_rng(0)
        vecs = list(np.array([1.0, 0.0], np.float32) + rng.standard_normal((30, 2)).astype(np.float32) * 0.02)
        vecs += list(np.array([0.0, 1.0], np.float32) + rng.standard_normal((30, 2)).astype(np.float32) * 0.02)
        for i, v in enumerate(vecs, start=1):
            conn.execute("INSERT INTO chunks (id, source_id, chunk_index, text) VALUES (?,1,?,?)",
                         (i, i, f"text chunk {i} " * 20))
            conn.execute("INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?,?)", (i, v.tobytes()))
        conn.commit(); conn.close()
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_reports_distant_cluster_pairs(self):
        out = brainstorm.report_gaps(topics=None, top=5, base_dir=self.dir,
                                     ledger_path=self.ledger, num_clusters=2)
        self.assertIn("cluster_gaps", out)
        self.assertGreaterEqual(len(out["cluster_gaps"]), 1)
        gap = out["cluster_gaps"][0]
        self.assertIn("cluster_a", gap)
        self.assertIn("cluster_b", gap)
        self.assertLess(gap["similarity"], 0.5)  # the two blobs are orthogonal


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # 60 chunks across two topics to clear the <50 sparse guard
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=40)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=20)
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_generates_and_stores(self):
        llm = _FakeLLM(['{"hypothesis": "H%d", "kill_test": "T"}' % i for i in range(30)])
        out = brainstorm.generate_hypotheses(
            count=2, drift=0.9, topics=None, llm=llm,
            base_dir=self.dir, ledger_path=self.ledger)
        self.assertGreaterEqual(len(out), 1)
        self.assertIn("hypothesis", out[0])
        self.assertIn("source_a", out[0])
        self.assertIn("topic", out[0]["source_a"])
        stored = brainstorm.list_hypotheses(self.ledger)
        self.assertGreaterEqual(len(stored), 1)

    def test_sparse_corpus_refused(self):
        small = tempfile.mkdtemp()
        _make_topic_db(os.path.join(small, "knowledge.db"), n=5)
        llm = _FakeLLM([])
        with self.assertRaises(brainstorm.SparseCorpusError):
            brainstorm.generate_hypotheses(
                count=1, drift=0.5, topics=None, llm=llm,
                base_dir=small, ledger_path=os.path.join(small, "brainstorm.db"))

    def test_raw_pairs_mode_when_no_chat_model(self):
        # chat_model 'none' -> return raw collided pairs for the calling LLM to write up.
        llm = _FakeLLM([], chat_model="none")
        out = brainstorm.generate_hypotheses(
            count=2, drift=0.9, topics=None, llm=llm,
            base_dir=self.dir, ledger_path=self.ledger)
        self.assertGreaterEqual(len(out), 1)
        self.assertTrue(out[0]["needs_hypothesis"])
        self.assertNotIn("hypothesis", out[0])       # engine did NOT write one
        self.assertIn("snippet", out[0]["source_a"])
        # stored as a pending row with no embedding yet
        stored = brainstorm.list_hypotheses(self.ledger)
        self.assertGreaterEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "new")

    def test_update_hypothesis_fills_text_and_embedding(self):
        # calling LLM writes the hypothesis back onto a raw-pair row
        llm = _FakeLLM([], chat_model="none")
        out = brainstorm.generate_hypotheses(
            count=1, drift=0.9, topics=None, llm=llm,
            base_dir=self.dir, ledger_path=self.ledger)
        hid = out[0]["id"]
        brainstorm.update_hypothesis(self.ledger, hid, text="real hypothesis",
                                     kill_test="ten cold emails",
                                     embedding=np.array([0.3, 0.4, 0.5], dtype=np.float32))
        row = brainstorm.list_hypotheses(self.ledger)[0]
        self.assertEqual(row["text"], "real hypothesis")
        self.assertEqual(row["kill_test"], "ten cold emails")


class _EndlessLLM(_FakeLLM):
    """Chat-capable stub that never runs out: fresh valid JSON per collide call."""
    def __init__(self):
        super().__init__([], chat_model="stub")
        self.calls = 0

    def generate_completion(self, system, prompt):
        self.calls += 1
        return '{"hypothesis": "hypothesis number %d", "kill_test": "k"}' % self.calls


class TestPairDedupBothModes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=30)
        _make_topic_db(os.path.join(self.dir, "topic_two.db"), n=30)
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_chat_mode_never_recollides_a_stored_pair(self):
        llm = _EndlessLLM()
        first = brainstorm.generate_hypotheses(count=3, drift=0.5, llm=llm,
                                               base_dir=self.dir, ledger_path=self.ledger)
        self.assertTrue(first)
        brainstorm.generate_hypotheses(count=3, drift=0.5, llm=llm,
                                       base_dir=self.dir, ledger_path=self.ledger)
        stored = brainstorm.list_hypotheses(self.ledger)
        canon = [tuple(sorted([(r["topic_a"], r["chunk_a"]), (r["topic_b"], r["chunk_b"])]))
                 for r in stored]
        self.assertEqual(len(canon), len(set(canon)), "same pair collided twice")


class TestPartnerSampling(unittest.TestCase):
    def test_partner_varies_across_runs_within_band(self):
        matrix = np.random.default_rng(7).standard_normal((40, 8)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        index = [{"topic": "t%d" % (i % 2), "chunk_id": i, "source": "s%d" % (i % 5)}
                 for i in range(40)]
        seen = set()
        for _ in range(30):
            got = brainstorm.pick_partner(0, matrix, index, (-1.0, 1.0))
            self.assertIsNotNone(got)
            seen.add(got[0])
        self.assertGreater(len(seen), 1, "partner choice is deterministic")


class TestRealizedSim(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_ledger_gains_realized_sim_column(self):
        conn = brainstorm._ledger_conn(self.ledger)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hypotheses)")}
        conn.close()
        self.assertIn("realized_sim", cols)

    def test_insert_and_list_roundtrip_realized_sim(self):
        hid = brainstorm.insert_hypothesis(
            self.ledger, text="h", kill_test="k", topic_a="a", chunk_a=1,
            snippet_a="sa", topic_b="b", chunk_b=2, snippet_b="sb",
            drift=0.5, embedding=None, realized_sim=0.44)
        row = brainstorm.list_hypotheses(self.ledger)[0]
        self.assertEqual(row["id"], hid)
        self.assertAlmostEqual(row["realized_sim"], 0.44, places=6)


class TestPartnerReturnsSim(unittest.TestCase):
    def test_pick_partner_returns_index_and_similarity(self):
        # anchor row 0; rows 1..2 inside the (0.4, 0.8) band, row 3 outside
        matrix = np.array([[1, 0, 0, 0], [0.7, 0.7, 0, 0],
                           [0.5, 0.86, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        index = [{"topic": "t1", "chunk_id": 1, "source": "s"},
                 {"topic": "t2", "chunk_id": 2, "source": "s"},
                 {"topic": "t2", "chunk_id": 3, "source": "s"},
                 {"topic": "t2", "chunk_id": 4, "source": "s"}]
        got = brainstorm.pick_partner(0, matrix, index, (0.4, 0.8))
        self.assertIsNotNone(got)
        j, sim = got
        self.assertIn(j, (1, 2))
        self.assertTrue(0.4 <= sim <= 0.8)


if __name__ == "__main__":
    unittest.main()

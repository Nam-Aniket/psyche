import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBED_MODEL"] = "none"
os.environ["CHAT_MODEL"] = "none"
os.environ.pop("RERANK_PROVIDER", None)
os.environ["RERANK_PROVIDER"] = "none"

import numpy as np
import db


class TestBuildState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            s1 = db.add_source(conn, "Test Book", "Author A", "t.txt", "ck_test1")
            cid = db.add_chunk(conn, s1, 0, "Philosophy of mind.", location="Ch 1")
            # No embeddings inserted — provider is "none", so matrix stays empty.
        finally:
            conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_state_returns_appstate(self):
        from web.state import build_state
        from web.deps import AppState

        st = build_state(self.db_path)
        self.assertIsInstance(st, AppState)

    def test_build_state_db_path_set(self):
        from web.state import build_state

        st = build_state(self.db_path)
        # build_state passes db_path through resolve_db_path; since our temp path
        # is already absolute it should be returned unchanged.
        self.assertEqual(st.db_path, self.db_path)

    def test_build_state_llm_not_none(self):
        from web.state import build_state

        st = build_state(self.db_path)
        self.assertIsNotNone(st.llm)

    def test_build_state_chunk_ids_is_ndarray(self):
        from web.state import build_state

        st = build_state(self.db_path)
        self.assertIsInstance(st.chunk_ids, np.ndarray)

    def test_build_state_embeddings_matrix_is_ndarray(self):
        from web.state import build_state

        st = build_state(self.db_path)
        self.assertIsInstance(st.embeddings_matrix, np.ndarray)

    def test_build_state_provider_none_leaves_empty_arrays(self):
        """With LLM_PROVIDER=none no embeddings are written, so arrays stay empty."""
        from web.state import build_state

        st = build_state(self.db_path)
        # provider is "none" → no semantic indexing → arrays empty
        self.assertEqual(st.chunk_ids.shape[0], 0)

    def test_build_state_usearch_index_none_when_no_provider(self):
        from web.state import build_state

        st = build_state(self.db_path)
        self.assertIsNone(st.usearch_index)


if __name__ == "__main__":
    unittest.main()

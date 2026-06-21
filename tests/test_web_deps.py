import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"

import numpy as np


class TestAppState(unittest.TestCase):
    def test_appstate_construction(self):
        from web.deps import AppState

        class FakeLLM:
            provider = "fake"
            chat_provider = "fake"
            embed_model = "fake-embed"
            chat_model = "fake-chat"

        chunk_ids = np.array([1, 2], dtype=np.int32)
        matrix = np.zeros((2, 8), dtype=np.float32)
        st = AppState(
            db_path="/tmp/test.db",
            llm=FakeLLM(),
            chunk_ids=chunk_ids,
            embeddings_matrix=matrix,
            usearch_index=None,
        )
        self.assertEqual(st.db_path, "/tmp/test.db")
        self.assertEqual(st.llm.provider, "fake")
        self.assertEqual(st.chunk_ids.shape, (2,))
        self.assertIsNone(st.usearch_index)

    def test_appstate_fields_present(self):
        from web.deps import AppState
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(AppState)}
        self.assertIn("db_path", field_names)
        self.assertIn("llm", field_names)
        self.assertIn("chunk_ids", field_names)
        self.assertIn("embeddings_matrix", field_names)
        self.assertIn("usearch_index", field_names)

    def test_get_state_returns_psyche_from_request(self):
        from web.deps import AppState, get_state

        class FakeLLM:
            provider = "none"
            chat_provider = "none"
            embed_model = "none"
            chat_model = "none"

        st = AppState(
            db_path="/tmp/x.db",
            llm=FakeLLM(),
            chunk_ids=np.array([], dtype=np.int32),
            embeddings_matrix=np.array([], dtype=np.float32),
            usearch_index=None,
        )

        class FakeAppState:
            psyche = st

        class FakeApp:
            state = FakeAppState()

        class FakeRequest:
            app = FakeApp()

        result = get_state(FakeRequest())
        self.assertIs(result, st)

    def test_error_response_model(self):
        from web.deps import ErrorResponse
        er = ErrorResponse(detail="something went wrong")
        self.assertEqual(er.detail, "something went wrong")


if __name__ == "__main__":
    unittest.main()

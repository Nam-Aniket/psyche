"""Self-tests that verify WebTestCase builds a working client and seeded DB."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBED_MODEL"] = "none"
os.environ["CHAT_MODEL"] = "none"
os.environ["RERANK_PROVIDER"] = "none"


class TestWebTestCaseFixture(unittest.TestCase):
    """These tests import WebTestCase to confirm it wires up correctly."""

    def _make_case(self):
        from tests.test_web_base import WebTestCase

        tc = WebTestCase()
        tc.setUp()
        return tc

    def test_client_is_created(self):
        from fastapi.testclient import TestClient

        tc = self._make_case()
        try:
            self.assertIsInstance(tc.client, TestClient)
        finally:
            tc.tearDown()

    def test_db_seeded_with_two_sources(self):
        import db

        tc = self._make_case()
        try:
            conn = db.get_connection(tc.db_path)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM sources").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 2)
        finally:
            tc.tearDown()

    def test_db_seeded_with_three_chunks(self):
        import db

        tc = self._make_case()
        try:
            conn = db.get_connection(tc.db_path)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 3)
        finally:
            tc.tearDown()

    def test_db_seeded_with_three_embeddings(self):
        import db

        tc = self._make_case()
        try:
            conn = db.get_connection(tc.db_path)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 3)
        finally:
            tc.tearDown()

    def test_state_llm_is_fakelllm(self):
        tc = self._make_case()
        try:
            self.assertEqual(tc.state.llm.provider, "fake")
        finally:
            tc.tearDown()

    def test_state_db_path_is_temp(self):
        import tempfile

        tc = self._make_case()
        try:
            # db_path should be inside a temp directory, not the system DB
            self.assertTrue(os.path.exists(tc.db_path))
            self.assertIn(tempfile.gettempdir(), tc.db_path)
        finally:
            tc.tearDown()

    def test_health_endpoint_reachable_via_fixture_client(self):
        tc = self._make_case()
        try:
            resp = tc.client.get("/health")
            self.assertEqual(resp.status_code, 200)
        finally:
            tc.tearDown()

    def test_concepts_seeded(self):
        import db

        tc = self._make_case()
        try:
            conn = db.get_connection(tc.db_path)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 2)
        finally:
            tc.tearDown()

    def test_concept_links_seeded(self):
        import db

        tc = self._make_case()
        try:
            conn = db.get_connection(tc.db_path)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM concept_links").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 1)
        finally:
            tc.tearDown()

    def test_teardown_cleans_temp_dir(self):
        tc = self._make_case()
        db_path = tc.db_path
        tc.tearDown()
        self.assertFalse(os.path.exists(db_path))


if __name__ == "__main__":
    unittest.main()

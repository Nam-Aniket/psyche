import os
import sys
import unittest

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ.setdefault("RERANK_PROVIDER", "none")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_web_base import WebTestCase


class TestGetSources(WebTestCase):
    def test_sources_returns_200(self):
        resp = self.client.get("/sources")
        self.assertEqual(resp.status_code, 200)

    def test_sources_returns_list(self):
        resp = self.client.get("/sources")
        data = resp.json()
        self.assertIsInstance(data, list)

    def test_sources_contains_seeded_titles(self):
        resp = self.client.get("/sources")
        titles = [s["title"] for s in resp.json()]
        self.assertIn("Meditations", titles)
        self.assertIn("Letters", titles)

    def test_sources_has_chunk_count(self):
        resp = self.client.get("/sources")
        meditations = next(s for s in resp.json() if s["title"] == "Meditations")
        self.assertEqual(meditations["chunk_count"], 2)
        letters = next(s for s in resp.json() if s["title"] == "Letters")
        self.assertEqual(letters["chunk_count"], 1)

    def test_sources_has_required_keys(self):
        resp = self.client.get("/sources")
        for src in resp.json():
            for key in ("id", "title", "author", "chunk_count"):
                self.assertIn(key, src)


class TestIngestStatus(WebTestCase):
    """GET /ingest/status?checksum=<sha256> — dedup check."""

    def test_status_known_checksum_found(self):
        # "ck_med" is the checksum seeded for Meditations in WebTestCase.setUp
        resp = self.client.get("/ingest/status", params={"checksum": "ck_med"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["already_ingested"])
        self.assertIsNotNone(data["source_id"])
        self.assertIsInstance(data["source_id"], int)

    def test_status_unknown_checksum_not_found(self):
        resp = self.client.get("/ingest/status", params={"checksum": "deadbeefdeadbeef"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["already_ingested"])
        self.assertIsNone(data["source_id"])

    def test_status_missing_checksum_returns_422(self):
        # FastAPI validates query param presence; no checksum -> 422 Unprocessable Entity
        resp = self.client.get("/ingest/status")
        self.assertEqual(resp.status_code, 422)

    def test_status_has_required_keys(self):
        resp = self.client.get("/ingest/status", params={"checksum": "ck_let"})
        data = resp.json()
        self.assertIn("already_ingested", data)
        self.assertIn("source_id", data)


import tempfile


class TestPostIngestLocalPath(WebTestCase):
    """POST /ingest with a JSON body containing a server-side file path."""

    def _write_txt(self, content: str) -> str:
        """Write a temp .txt file and return its absolute path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            dir=self.tmp.name
        )
        f.write(content)
        f.close()
        return f.name

    def test_ingest_new_file_returns_200(self):
        path = self._write_txt("Philosophy is the love of wisdom. " * 40)
        resp = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp.status_code, 200)

    def test_ingest_new_file_returns_source_id_and_chunk_count(self):
        path = self._write_txt("Socrates knew he knew nothing. " * 40)
        resp = self.client.post("/ingest", json={"path": path})
        data = resp.json()
        self.assertIn("source_id", data)
        self.assertIn("chunk_count", data)
        self.assertIn("skipped", data)
        self.assertIsInstance(data["source_id"], int)
        self.assertGreater(data["chunk_count"], 0)
        self.assertFalse(data["skipped"])

    def test_ingest_same_file_twice_skips_second(self):
        path = self._write_txt("The unexamined life is not worth living. " * 40)
        resp1 = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp1.status_code, 200)
        resp2 = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["skipped"])
        self.assertEqual(data2["chunk_count"], 0)

    def test_ingest_force_reingest(self):
        path = self._write_txt("Virtue is its own reward. " * 40)
        resp1 = self.client.post("/ingest", json={"path": path})
        sid1 = resp1.json()["source_id"]
        resp2 = self.client.post("/ingest", json={"path": path, "force": True})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertFalse(data2["skipped"])
        self.assertGreater(data2["chunk_count"], 0)
        # After force re-ingest a new source_id is created
        self.assertIsInstance(data2["source_id"], int)

    def test_ingest_missing_path_returns_404(self):
        resp = self.client.post("/ingest", json={"path": "/nonexistent/file.txt"})
        self.assertEqual(resp.status_code, 404)

    def test_ingest_title_and_author_override(self):
        path = self._write_txt("The mind is everything. " * 40)
        resp = self.client.post(
            "/ingest",
            json={"path": path, "title": "MyBook", "author": "MyAuthor"}
        )
        self.assertEqual(resp.status_code, 200)
        # Verify it appears in sources with the custom title/author
        sources_resp = self.client.get("/sources")
        sources = sources_resp.json()
        match = next((s for s in sources if s["title"] == "MyBook"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["author"], "MyAuthor")

    def test_ingest_unsupported_ext_returns_400(self):
        f = tempfile.NamedTemporaryFile(
            suffix=".xyz", delete=False, dir=self.tmp.name
        )
        f.write(b"data")
        f.close()
        resp = self.client.post("/ingest", json={"path": f.name})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()

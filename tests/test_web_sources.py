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


if __name__ == "__main__":
    unittest.main()

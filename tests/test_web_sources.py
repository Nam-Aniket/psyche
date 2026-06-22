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


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["RERANK_PROVIDER"] = "none"

from tests.test_web_base import WebTestCase


class TestSearchEndpoint(WebTestCase):
    """Tests for POST /search."""

    def test_search_returns_200(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        self.assertEqual(resp.status_code, 200)

    def test_search_response_is_list(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data, list)

    def test_search_result_has_required_keys(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertTrue(len(data) > 0, "Expected at least one result")
        item = data[0]
        for key in ("chunk_id", "text", "location", "source_title", "source_author", "score"):
            self.assertIn(key, item, f"Missing key: {key}")

    def test_search_score_is_float(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data[0]["score"], float)

    def test_search_chunk_id_is_int(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data[0]["chunk_id"], int)

    def test_search_result_contains_seeded_source(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        titles = [r["source_title"] for r in resp.json()]
        self.assertIn("Meditations", titles)

    def test_search_default_limit_is_five(self):
        # Seeded DB has 3 chunks; default limit=5 means we get all 3 back
        resp = self.client.post("/search", json={"query_text": "life"})
        data = resp.json()
        self.assertLessEqual(len(data), 5)

    def test_search_custom_limit_respected(self):
        resp = self.client.post("/search", json={"query_text": "life", "limit": 1})
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_search_limit_zero_uses_default(self):
        # limit=0 is falsy; endpoint should fall back to default (5)
        resp = self.client.post("/search", json={"query_text": "life", "limit": 0})
        self.assertEqual(resp.status_code, 200)

    def test_search_missing_query_text_returns_422(self):
        resp = self.client.post("/search", json={})
        self.assertEqual(resp.status_code, 422)

    def test_search_empty_query_text_returns_400(self):
        resp = self.client.post("/search", json={"query_text": ""})
        self.assertEqual(resp.status_code, 400)

    def test_search_results_ordered_by_score_descending(self):
        resp = self.client.post("/search", json={"query_text": "stoic philosophy"})
        data = resp.json()
        if len(data) > 1:
            scores = [r["score"] for r in data]
            self.assertEqual(scores, sorted(scores, reverse=True))


class TestChatEndpoint(WebTestCase):
    """Tests for POST /chat."""

    def test_chat_returns_200(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        self.assertEqual(resp.status_code, 200)

    def test_chat_response_has_answer_key(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIn("answer", data)

    def test_chat_response_has_sources_key(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIn("sources", data)

    def test_chat_sources_is_list(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIsInstance(data["sources"], list)

    def test_chat_answer_is_fake_answer(self):
        # FakeLLM.generate_completion always returns "FAKE ANSWER"
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertEqual(data["answer"], "FAKE ANSWER")

    def test_chat_sources_have_required_keys(self):
        resp = self.client.post("/chat", json={"query_text": "discipline cosmos"})
        data = resp.json()
        sources = data["sources"]
        self.assertTrue(len(sources) > 0)
        for key in ("chunk_id", "text", "location", "source_title", "source_author", "score"):
            self.assertIn(key, sources[0], f"Missing key in source: {key}")

    def test_chat_source_scores_ordered_descending(self):
        resp = self.client.post("/chat", json={"query_text": "discipline cosmos"})
        sources = resp.json()["sources"]
        if len(sources) > 1:
            scores = [s["score"] for s in sources]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_chat_limit_restricts_sources(self):
        resp = self.client.post("/chat", json={"query_text": "life", "limit": 1})
        sources = resp.json()["sources"]
        self.assertLessEqual(len(sources), 1)

    def test_chat_default_limit_is_five(self):
        resp = self.client.post("/chat", json={"query_text": "life"})
        sources = resp.json()["sources"]
        self.assertLessEqual(len(sources), 5)

    def test_chat_missing_query_returns_422(self):
        resp = self.client.post("/chat", json={})
        self.assertEqual(resp.status_code, 422)

    def test_chat_empty_query_returns_400(self):
        resp = self.client.post("/chat", json={"query_text": ""})
        self.assertEqual(resp.status_code, 400)

    def test_chat_returns_503_when_provider_is_none(self):
        original_provider = self.state.llm.provider
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.provider = "none"
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            self.assertEqual(resp.status_code, 503)
        finally:
            self.state.llm.provider = original_provider
            self.state.llm.chat_model = original_chat_model

    def test_chat_returns_503_when_chat_model_is_none(self):
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            self.assertEqual(resp.status_code, 503)
        finally:
            self.state.llm.chat_model = original_chat_model

    def test_chat_503_detail_mentions_provider(self):
        original_provider = self.state.llm.provider
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.provider = "none"
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            data = resp.json()
            self.assertIn("detail", data)
            self.assertIsInstance(data["detail"], str)
        finally:
            self.state.llm.provider = original_provider
            self.state.llm.chat_model = original_chat_model


if __name__ == "__main__":
    unittest.main()

"""POST /provider — browser-driven chat config. Validates that a rejected
config (cloud provider without a key) is NOT persisted (rollback), so it can
never brick the next startup. Env writes are isolated under a temp HOME.
"""
import os
import sys

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tempfile
import unittest

from tests.test_web_base import WebTestCase


class TestSetProvider(WebTestCase):
    def setUp(self):
        super().setUp()
        # POST /provider calls load_dotenv(override=True), which mutates the global
        # os.environ — snapshot and fully restore it so nothing leaks into other tests.
        self._env_snapshot = dict(os.environ)
        self._tmp_home = tempfile.mkdtemp(prefix="psyche_home_")
        os.environ["HOME"] = self._tmp_home
        for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "CHAT_PROVIDER"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)
        super().tearDown()

    def _env_path(self):
        return os.path.join(self._tmp_home, ".psyche", ".env")

    def test_set_none_succeeds_and_keeps_embeddings_local(self):
        resp = self.client.post("/provider", json={"chat_provider": "none"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["chat_provider"], "none")
        self.assertEqual(body["provider"], "local")  # embeddings stay local

    def test_gemini_without_key_rejected_and_not_persisted(self):
        resp = self.client.post("/provider", json={"chat_provider": "gemini"})
        self.assertEqual(resp.status_code, 400, resp.text)
        # The rejected config must NOT have been written to .env.
        if os.path.exists(self._env_path()):
            with open(self._env_path()) as f:
                contents = f.read()
            self.assertNotIn("CHAT_PROVIDER=gemini", contents,
                             "rejected gemini config leaked into .env (would brick startup)")

    def test_unsupported_provider_rejected(self):
        resp = self.client.post("/provider", json={"chat_provider": "bogus"})
        self.assertEqual(resp.status_code, 400)

    def test_gemini_with_key_succeeds(self):
        resp = self.client.post("/provider", json={"chat_provider": "gemini", "api_key": "AIza-test-key"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["chat_provider"], "gemini")
        self.assertEqual(body["provider"], "local")


if __name__ == "__main__":
    unittest.main()

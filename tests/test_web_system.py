import os
import sys
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["RERANK_PROVIDER"] = "none"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from tests.test_web_base import WebTestCase


class TestSystemRouterRegistered(WebTestCase):
    """Routes /provider, /connect, /connect/status, /supported-clients are reachable."""

    def test_provider_endpoint_exists(self):
        resp = self.client.get("/provider")
        self.assertNotEqual(resp.status_code, 404, "GET /provider should be registered")
        self.assertNotEqual(resp.status_code, 405, "GET /provider should allow GET")

    def test_connect_endpoint_exists(self):
        resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
        self.assertNotEqual(resp.status_code, 404, "POST /connect should be registered")

    def test_connect_status_endpoint_exists(self):
        resp = self.client.get("/connect/status", params={"client": "claude-code"})
        self.assertNotEqual(resp.status_code, 404, "GET /connect/status should be registered")

    def test_supported_clients_endpoint_exists(self):
        resp = self.client.get("/supported-clients")
        self.assertNotEqual(resp.status_code, 404, "GET /supported-clients should be registered")


class TestGetProvider(WebTestCase):
    """GET /provider returns the FakeLLM attributes wired into AppState."""

    def test_status_200(self):
        resp = self.client.get("/provider")
        self.assertEqual(resp.status_code, 200)

    def test_response_keys(self):
        resp = self.client.get("/provider")
        body = resp.json()
        for key in ("provider", "chat_provider", "embed_model", "chat_model", "db_path"):
            self.assertIn(key, body, f"key {key!r} missing from /provider response")

    def test_provider_matches_fake_llm(self):
        resp = self.client.get("/provider")
        body = resp.json()
        self.assertEqual(body["provider"], "fake")
        self.assertEqual(body["chat_provider"], "fake")
        self.assertEqual(body["embed_model"], "fake-embed")
        self.assertEqual(body["chat_model"], "fake-chat")

    def test_db_path_is_absolute(self):
        resp = self.client.get("/provider")
        body = resp.json()
        self.assertTrue(
            os.path.isabs(body["db_path"]),
            f"db_path should be absolute, got {body['db_path']!r}",
        )

    def test_no_llm_construction_per_request(self):
        r1 = self.client.get("/provider")
        r2 = self.client.get("/provider")
        self.assertEqual(r1.json()["provider"], r2.json()["provider"])


class TestGetSupportedClients(WebTestCase):
    """GET /supported-clients returns the canonical list of client names."""

    def test_status_200(self):
        resp = self.client.get("/supported-clients")
        self.assertEqual(resp.status_code, 200)

    def test_returns_list(self):
        resp = self.client.get("/supported-clients")
        self.assertIsInstance(resp.json(), list)

    def test_all_four_clients_present(self):
        resp = self.client.get("/supported-clients")
        body = resp.json()
        for name in ("claude-code", "codex", "gemini", "antigravity"):
            self.assertIn(name, body, f"{name!r} missing from /supported-clients")

    def test_exactly_four_clients(self):
        resp = self.client.get("/supported-clients")
        body = resp.json()
        self.assertEqual(len(body), 4, f"Expected exactly 4 clients, got {len(body)}: {body}")


class TestPostConnect(WebTestCase):
    """POST /connect calls connect(client, dry_run=...) and returns {actions:[...]}."""

    def setUp(self):
        super().setUp()
        # Redirect HOME so connect() writes into a temp dir, never the real ~/.claude
        import tempfile
        self._home_tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home_tmp.name
        import importlib
        import connect as _c
        importlib.reload(_c)

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        self._home_tmp.cleanup()
        super().tearDown()

    def test_status_200_dry_run(self):
        resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
        self.assertEqual(resp.status_code, 200)

    def test_response_has_actions_key(self):
        resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
        self.assertIn("actions", resp.json())

    def test_actions_is_list_of_strings(self):
        resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
        actions = resp.json()["actions"]
        self.assertIsInstance(actions, list)
        for item in actions:
            self.assertIsInstance(item, str, f"non-string action item: {item!r}")

    def test_dry_run_returns_nonempty_actions(self):
        resp = self.client.post("/connect", json={"client": "codex", "dry_run": True})
        self.assertGreater(len(resp.json()["actions"]), 0, "dry_run should return at least one action")

    def test_antigravity_alias_accepted(self):
        resp = self.client.post("/connect", json={"client": "antigravity", "dry_run": True})
        self.assertEqual(resp.status_code, 200)

    def test_unknown_client_returns_400(self):
        resp = self.client.post("/connect", json={"client": "unknown-tool", "dry_run": True})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.json())

    def test_missing_client_field_returns_422(self):
        resp = self.client.post("/connect", json={"dry_run": True})
        self.assertEqual(resp.status_code, 422)

    def test_connect_writes_files_when_not_dry_run(self):
        resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": False})
        self.assertEqual(resp.status_code, 200)
        settings_path = os.path.join(self._home_tmp.name, ".claude", "settings.json")
        self.assertTrue(
            os.path.exists(settings_path),
            "settings.json should exist after a real (non-dry-run) connect call",
        )


class TestGetConnectStatus(WebTestCase):
    """GET /connect/status?client=X is always a dry-run preview; never writes files."""

    def setUp(self):
        super().setUp()
        import tempfile
        self._home_tmp = tempfile.TemporaryDirectory()
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self._home_tmp.name
        import importlib
        import connect as _c
        importlib.reload(_c)

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        self._home_tmp.cleanup()
        super().tearDown()

    def test_status_200(self):
        resp = self.client.get("/connect/status", params={"client": "claude-code"})
        self.assertEqual(resp.status_code, 200)

    def test_response_has_actions_key(self):
        resp = self.client.get("/connect/status", params={"client": "claude-code"})
        self.assertIn("actions", resp.json())

    def test_actions_is_list(self):
        resp = self.client.get("/connect/status", params={"client": "claude-code"})
        self.assertIsInstance(resp.json()["actions"], list)

    def test_dry_run_does_not_write_files(self):
        self.client.get("/connect/status", params={"client": "claude-code"})
        settings_path = os.path.join(self._home_tmp.name, ".claude", "settings.json")
        self.assertFalse(
            os.path.exists(settings_path),
            "/connect/status must not write ~/.claude/settings.json (dry_run=True)",
        )

    def test_codex_dry_run_no_files(self):
        self.client.get("/connect/status", params={"client": "codex"})
        config_path = os.path.join(self._home_tmp.name, ".codex", "config.toml")
        self.assertFalse(os.path.exists(config_path))

    def test_antigravity_accepted(self):
        resp = self.client.get("/connect/status", params={"client": "antigravity"})
        self.assertEqual(resp.status_code, 200)

    def test_unknown_client_returns_400(self):
        resp = self.client.get("/connect/status", params={"client": "notepad"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_client_param_returns_422(self):
        resp = self.client.get("/connect/status")
        self.assertEqual(resp.status_code, 422)

    def test_status_and_post_connect_dry_run_return_same_actions(self):
        get_resp = self.client.get("/connect/status", params={"client": "codex"})
        post_resp = self.client.post("/connect", json={"client": "codex", "dry_run": True})
        self.assertEqual(get_resp.json()["actions"], post_resp.json()["actions"])


if __name__ == "__main__":
    unittest.main()

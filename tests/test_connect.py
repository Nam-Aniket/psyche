import json
import os
import tempfile
import unittest


class TestConnect(unittest.TestCase):
    def setUp(self):
        self._orig_home = os.environ.get("HOME")
        self._tmpdir = tempfile.mkdtemp()
        os.environ["HOME"] = self._tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    def _import_connect(self):
        # Re-import each time so expanduser picks up the monkeypatched HOME
        import importlib
        import connect
        importlib.reload(connect)
        return connect

    def test_protocol_block_includes_synthesis_and_placement(self):
        connect = self._import_connect()
        block = connect._get_protocol_block()
        self.assertIn("submit_guidance_plan", block)
        self.assertIn("append-only", block)

    def test_claude_code_creates_mcp_entry(self):
        connect = self._import_connect()
        actions = connect.connect("claude-code")
        self.assertTrue(len(actions) > 0)

        settings_path = os.path.expanduser("~/.claude/settings.json")
        self.assertTrue(os.path.exists(settings_path), "settings.json should be created")

        with open(settings_path, "r") as f:
            data = json.load(f)

        psyche = data["mcpServers"]["psyche"]
        self.assertIn("start-mcp", psyche["args"])
        # connect resolves the repo .venv python when present, else falls back to
        # the running interpreter (pip/npm/global installs and CI have no .venv).
        self.assertEqual(psyche["command"], connect._VENV_PYTHON)
        self.assertRegex(
            psyche["command"], r"python(\d(\.\d+)?)?(\.exe)?$",
            f"command should point at a python executable, got {psyche['command']!r}",
        )

    def test_idempotent(self):
        connect = self._import_connect()

        # Pre-populate settings.json with an unrelated key
        settings_path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump({"otherKey": "shouldSurvive", "mcpServers": {"other": {"command": "x"}}}, f)

        connect.connect("claude-code")
        connect.connect("claude-code")  # second call — must be idempotent

        with open(settings_path, "r") as f:
            data = json.load(f)

        # psyche entry present exactly (dict equality check is enough; no duplication possible in JSON)
        self.assertIn("psyche", data["mcpServers"])
        # unrelated key survives
        self.assertEqual(data["otherKey"], "shouldSurvive")
        # other MCP server survives
        self.assertIn("other", data["mcpServers"])

    def test_dry_run_writes_nothing(self):
        connect = self._import_connect()
        actions = connect.connect("codex", dry_run=True)

        self.assertTrue(len(actions) > 0, "dry_run should still return action strings")

        config_path = os.path.expanduser("~/.codex/config.toml")
        agents_path = os.path.expanduser("~/.codex/AGENTS.md")
        self.assertFalse(os.path.exists(config_path), "config.toml must not be created on dry_run")
        self.assertFalse(os.path.exists(agents_path), "AGENTS.md must not be created on dry_run")

    def test_codex_marker_idempotent(self):
        connect = self._import_connect()

        connect.connect("codex")
        connect.connect("codex")  # second call

        config_path = os.path.expanduser("~/.codex/config.toml")
        with open(config_path, "r") as f:
            content = f.read()

        marker = "# >>> psyche (managed) >>>"
        count = content.count(marker)
        self.assertEqual(count, 1, f"marker should appear exactly once, found {count}")


if __name__ == "__main__":
    unittest.main()

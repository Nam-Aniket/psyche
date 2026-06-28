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

    def test_claude_code_installs_auto_memory_hooks(self):
        connect = self._import_connect()
        connect.connect("claude-code")

        settings_path = os.path.expanduser("~/.claude/settings.json")
        with open(settings_path, "r") as f:
            data = json.load(f)

        hooks = data["hooks"]
        # Every lifecycle event Psyche manages is wired.
        for event in ("Stop", "PreCompact", "SessionEnd", "SessionStart", "UserPromptSubmit"):
            self.assertIn(event, hooks, f"{event} hook should be installed")
            cmd = hooks[event][0]["hooks"][0]["command"]
            self.assertIn("hooks", cmd)
        # Stop drives the time-gated checkpoint.
        self.assertIn("psyche_stop.py", hooks["Stop"][0]["hooks"][0]["command"])
        # Flush events reuse the full extractor.
        self.assertIn("psyche_extract.py", hooks["SessionEnd"][0]["hooks"][0]["command"])

    def test_claude_code_hooks_preserve_foreign_and_dont_duplicate(self):
        connect = self._import_connect()
        settings_path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        foreign = {"hooks": [{"type": "command", "command": "/usr/bin/other-tool"}]}
        with open(settings_path, "w") as f:
            json.dump({"hooks": {"Stop": [foreign]}}, f)

        connect.connect("claude-code")
        connect.connect("claude-code")  # second call must not duplicate

        with open(settings_path, "r") as f:
            data = json.load(f)

        stop_groups = data["hooks"]["Stop"]
        # Foreign hook survives.
        self.assertIn(foreign, stop_groups)
        # Exactly one Psyche group despite two connects.
        psyche_groups = [g for g in stop_groups if connect._is_psyche_group(g)]
        self.assertEqual(len(psyche_groups), 1, "Psyche Stop hook must not duplicate")

    def test_dry_run_writes_no_hooks(self):
        connect = self._import_connect()
        connect.connect("claude-code", dry_run=True)
        settings_path = os.path.expanduser("~/.claude/settings.json")
        self.assertFalse(os.path.exists(settings_path), "dry_run must not create settings.json")

    def test_gemini_installs_auto_memory_hooks(self):
        connect = self._import_connect()
        connect.connect("gemini")

        settings_path = os.path.expanduser("~/.gemini/settings.json")
        self.assertTrue(os.path.exists(settings_path), "gemini settings.json should be created")
        with open(settings_path) as f:
            hooks = json.load(f)["hooks"]
        for event in ("AfterAgent", "SessionEnd", "SessionStart", "BeforeAgent"):
            self.assertIn(event, hooks)
            self.assertIn("hooks", hooks[event][0]["hooks"][0]["command"])
        # AfterAgent is Gemini's per-turn-end -> the time-gated checkpoint.
        self.assertIn("psyche_stop.py", hooks["AfterAgent"][0]["hooks"][0]["command"])

    def test_detect_clients(self):
        connect = self._import_connect()
        os.makedirs(os.path.expanduser("~/.claude"), exist_ok=True)
        os.makedirs(os.path.expanduser("~/.gemini"), exist_ok=True)
        detected = connect.detect_clients()
        self.assertIn("claude-code", detected)
        self.assertIn("gemini", detected)
        self.assertNotIn("codex", detected)  # ~/.codex not created

    def test_auto_connect_wires_detected_once_then_noops(self):
        connect = self._import_connect()
        os.makedirs(os.path.expanduser("~/.claude"), exist_ok=True)

        first = connect.auto_connect()
        self.assertTrue(any("claude-code" in a for a in first), "should wire detected Claude")
        self.assertTrue(os.path.exists(os.path.expanduser("~/.claude/settings.json")))

        # Sentinel makes a second call a no-op...
        self.assertEqual(connect.auto_connect(), [])
        # ...but force re-runs.
        self.assertTrue(len(connect.auto_connect(force=True)) > 0)

    def test_auto_connect_dry_run_writes_no_sentinel(self):
        connect = self._import_connect()
        os.makedirs(os.path.expanduser("~/.claude"), exist_ok=True)
        connect.auto_connect(dry_run=True)
        self.assertFalse(os.path.exists(connect._AUTOCONNECT_SENTINEL),
                         "dry_run must not persist the sentinel")

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

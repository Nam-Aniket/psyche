"""Setup must not mutate host integrations without explicit flags."""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import setup_cmd


class TestSetupConsent(unittest.TestCase):
    def test_default_options_are_side_effect_free(self):
        options = setup_cmd._parse_setup_options([])
        self.assertFalse(options.connect)
        self.assertFalse(options.watcher)
        self.assertFalse(options.git_hook)
        self.assertFalse(options.global_link)

    def test_integrations_require_their_own_flags(self):
        options = setup_cmd._parse_setup_options(["--connect", "--watcher", "--git-hook", "--global-link"])
        self.assertTrue(options.connect)
        self.assertTrue(options.watcher)
        self.assertTrue(options.git_hook)
        self.assertTrue(options.global_link)

    def test_installed_setup_runs_wizard_without_reinstalling_cwd(self):
        options = SimpleNamespace(connect=False, watcher=False, git_hook=False, global_link=False)
        with mock.patch.dict(os.environ, {"TESTING": "true"}, clear=True), \
             mock.patch.object(sys, "argv", ["psyche"]), \
             mock.patch.object(setup_cmd, "_parse_setup_options", return_value=options), \
             mock.patch.object(setup_cmd, "run_wizard_phase") as wizard, \
             mock.patch.object(setup_cmd, "_run_requested_integrations") as integrations, \
             mock.patch.object(setup_cmd.subprocess, "run") as run:
            setup_cmd.run_setup()
        wizard.assert_called_once_with()
        integrations.assert_called_once()
        run.assert_not_called()

    def test_macos_watcher_uses_resolved_psyche_not_npx(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(setup_cmd.os.path, "expanduser", return_value=temp_dir), \
             mock.patch.object(setup_cmd, "_psyche_command", return_value=["/opt/psyche/bin/psyche"]), \
             mock.patch.object(setup_cmd.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            watch_dir = os.path.join(temp_dir, "watched notes")
            os.makedirs(watch_dir)
            setup_cmd.setup_macos_watcher("/source/checkout", watch_dir)
            with open(os.path.join(temp_dir, ".psyche", "sync.sh")) as f:
                script = f.read()
        self.assertNotIn("npx", script)
        self.assertIn("/opt/psyche/bin/psyche ingest", script)
        self.assertIn("watched notes", script)
        self.assertNotIn("/source/checkout", script)

    def test_global_link_never_replaces_an_existing_command(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(setup_cmd.os.path, "expanduser", return_value=temp_dir):
            existing = os.path.join(temp_dir, "psyche")
            with open(existing, "w") as f:
                f.write("owned by something else")
            with self.assertRaises(FileExistsError):
                setup_cmd._install_global_link("/opt/psyche/bin/psyche")
            with open(existing) as f:
                self.assertEqual(f.read(), "owned by something else")


if __name__ == "__main__":
    unittest.main()

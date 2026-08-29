"""Privacy boundary tests for automatic transcript extraction."""
import os
import sys
import unittest
from unittest import mock

HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import psyche_extract  # noqa: E402


class TestExtractionProviderConsent(unittest.TestCase):
    def _resolve(self, **overrides):
        env = {"TESTING": "true", "LLM_PROVIDER": "local", **overrides}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(psyche_extract.shutil, "which", return_value="/usr/local/bin/claude"), \
             mock.patch.object(psyche_extract.os.path, "exists", return_value=True):
            return psyche_extract._resolve_llm()

    def test_local_mode_never_uses_claude_cli_without_opt_in(self):
        llm = self._resolve()
        self.assertEqual(llm.chat_model, "none")
        self.assertNotIsInstance(llm, psyche_extract._ClaudeCLIChat)

    def test_claude_cli_extraction_requires_explicit_opt_in(self):
        llm = self._resolve(PSYCHE_ALLOW_CLAUDE_CLI_EXTRACTION="1")
        self.assertIsInstance(llm, psyche_extract._ClaudeCLIChat)
        self.assertEqual(llm.chat_model, "claude-sonnet-cli")

    def test_false_like_opt_in_stays_offline(self):
        llm = self._resolve(PSYCHE_ALLOW_CLAUDE_CLI_EXTRACTION="false")
        self.assertEqual(llm.chat_model, "none")


if __name__ == "__main__":
    unittest.main()

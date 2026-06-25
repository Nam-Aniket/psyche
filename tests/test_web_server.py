import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBED_MODEL"] = "none"
os.environ["CHAT_MODEL"] = "none"
os.environ["RERANK_PROVIDER"] = "none"


class TestWebServerModule(unittest.TestCase):
    def test_server_module_importable(self):
        import web.server  # noqa: F401

        self.assertTrue(True)

    def test_server_has_main_function(self):
        import web.server

        self.assertTrue(callable(web.server.main))

    def test_server_main_accepts_no_args(self):
        """main() signature takes no positional args (reads from env/CLI)."""
        import inspect
        import web.server

        sig = inspect.signature(web.server.main)
        # All params should have defaults so main() can be called bare.
        for name, param in sig.parameters.items():
            self.assertIsNot(
                param.default,
                inspect.Parameter.empty,
                f"Parameter '{name}' of web.server.main() has no default value",
            )

    def test_cli_includes_web_in_usage(self):
        """cli.py usage string must mention 'web'."""
        cli_path = os.path.join(
            os.path.dirname(__file__), "..", "cli.py"
        )
        with open(cli_path) as f:
            source = f.read()
        self.assertIn("web", source)

    def test_cli_dispatches_web_subcommand(self):
        """cli.py must have an elif branch for the 'web' subcommand."""
        cli_path = os.path.join(
            os.path.dirname(__file__), "..", "cli.py"
        )
        with open(cli_path) as f:
            source = f.read()
        # Look for the dispatch pattern used by every other subcommand.
        self.assertIn('subcommand == "web"', source)


if __name__ == "__main__":
    unittest.main()

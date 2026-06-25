import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"


class TestWebPackageImportable(unittest.TestCase):
    def test_fastapi_importable(self):
        import fastapi  # noqa: F401
        self.assertTrue(True)

    def test_uvicorn_importable(self):
        import uvicorn  # noqa: F401
        self.assertTrue(True)

    def test_web_package_importable(self):
        import web  # noqa: F401
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()

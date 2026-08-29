"""The browser must disclose cloud data flow instead of making absolutes."""
import os
import unittest


class TestWebPrivacyCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "web", "static", "app.js")) as f:
            cls.app_js = f.read()

    def test_cloud_chat_disclosure_is_present(self):
        self.assertIn("retrieved passages are sent to ${label}", self.app_js)
        self.assertIn("selected cloud provider", self.app_js)

    def test_old_false_chat_claims_are_gone(self):
        self.assertNotIn("nothing leaves this machine either way", self.app_js)
        self.assertNotIn("Every answer is pulled straight from your indexed documents, with citations. Nothing leaves", self.app_js)


if __name__ == "__main__":
    unittest.main()

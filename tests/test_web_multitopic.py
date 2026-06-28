"""Multitopic foundation: topic validation, default passthrough, /topics listing."""
from tests.test_web_base import WebTestCase


class TestMultitopic(WebTestCase):
    def test_invalid_topic_is_rejected(self):
        # A topic name becomes a topic_<name>.db filename; anything outside the
        # safe charset must 400 (path-traversal guard), not resolve a path.
        self.assertEqual(self.client.get("/graph/nodes?topic=../etc").status_code, 400)
        self.assertEqual(self.client.get("/memory/list?topic=a/b").status_code, 400)
        self.assertEqual(
            self.client.post("/search", json={"query_text": "x", "topic": "bad name"}).status_code,
            400,
        )

    def test_no_topic_uses_default_state(self):
        # No topic → the injected default state (seeded Stoicism/Virtue concepts).
        r = self.client.get("/graph/nodes")
        self.assertEqual(r.status_code, 200)
        names = {n["name"] for n in r.json()}
        self.assertIn("Stoicism", names)

    def test_topics_endpoint_shape(self):
        r = self.client.get("/topics")
        self.assertEqual(r.status_code, 200)
        topics = r.json()
        self.assertIsInstance(topics, list)
        self.assertTrue(topics, "must always include at least the Default entry")
        default = topics[0]
        self.assertTrue(default["is_default"])
        self.assertEqual(default["name"], "")
        for t in topics:
            self.assertEqual(set(t), {"name", "label", "is_default", "sources"})

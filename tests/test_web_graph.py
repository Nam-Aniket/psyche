import os
import sys
import tempfile
import unittest

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["RERANK_PROVIDER"] = "none"
# Force the offline co-occurrence path inside build_concept_graph (no network/ONNX).
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBED_MODEL"] = "none"
os.environ["CHAT_MODEL"] = "none"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import db as _db
from tests.test_web_base import WebTestCase


class TestGraphRouterRegistered(WebTestCase):
    def test_graph_nodes_route_exists(self):
        resp = self.client.get("/graph/nodes")
        self.assertEqual(resp.status_code, 200)

    def test_graph_edges_route_exists(self):
        resp = self.client.get("/graph/edges")
        self.assertEqual(resp.status_code, 200)

    def test_graph_build_route_exists(self):
        resp = self.client.post("/graph/build", json={})
        self.assertNotEqual(resp.status_code, 404)


class TestGraphNodes(WebTestCase):
    def test_nodes_returns_list(self):
        resp = self.client.get("/graph/nodes")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_nodes_contain_seeded_concepts(self):
        resp = self.client.get("/graph/nodes")
        names = [n["name"] for n in resp.json()]
        self.assertIn("Stoicism", names)
        self.assertIn("Virtue", names)

    def test_nodes_schema(self):
        resp = self.client.get("/graph/nodes")
        for node in resp.json():
            with self.subTest(node=node):
                self.assertIn("id", node)
                self.assertIn("name", node)
                self.assertIn("definition", node)
                self.assertIn("category", node)

    def test_nodes_id_is_integer(self):
        resp = self.client.get("/graph/nodes")
        for node in resp.json():
            with self.subTest(node=node):
                self.assertIsInstance(node["id"], int)


class TestGraphEdges(WebTestCase):
    def test_edges_returns_list(self):
        resp = self.client.get("/graph/edges")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_edges_contain_seeded_link(self):
        resp = self.client.get("/graph/edges")
        edges = resp.json()
        found = any(
            e["source"] == "Stoicism" and e["target"] == "Virtue" and e["relationship"] == "emphasizes"
            for e in edges
        )
        self.assertTrue(found, f"Expected Stoicism->Virtue edge, got: {edges}")

    def test_edges_schema(self):
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIn("id", edge)
                self.assertIn("source", edge)
                self.assertIn("target", edge)
                self.assertIn("relationship", edge)
                self.assertIn("description", edge)

    def test_edges_id_is_integer(self):
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIsInstance(edge["id"], int)

    def test_edges_source_target_are_strings(self):
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIsInstance(edge["source"], str)
                self.assertIsInstance(edge["target"], str)


class TestGraphBuildEmptyDB(WebTestCase):
    """POST /graph/build on an empty database must return 400, not kill the process."""

    def setUp(self):
        super().setUp()
        # Build a second app wired to an empty DB so we can test the guard.
        self._empty_tmp = tempfile.TemporaryDirectory()
        empty_db_path = os.path.join(self._empty_tmp.name, "empty.db")
        _db.init_db(empty_db_path)

        import web.app
        from web.deps import AppState

        empty_state = AppState(
            db_path=empty_db_path,
            llm=self.state.llm,
            chunk_ids=np.array([], dtype=np.int32),
            embeddings_matrix=np.array([], dtype=np.float32),
            usearch_index=None,
        )
        empty_app = web.app.create_app()
        empty_app.state.psyche = empty_state
        from fastapi.testclient import TestClient
        self._empty_client = TestClient(empty_app)

    def tearDown(self):
        self._empty_client.close()
        self._empty_tmp.cleanup()
        super().tearDown()

    def test_empty_db_returns_400(self):
        resp = self._empty_client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("detail", body)
        self.assertIn("empty", body["detail"].lower())

    def test_empty_db_does_not_raise_system_exit(self):
        try:
            self._empty_client.post("/graph/build", json={})
        except SystemExit:
            self.fail("POST /graph/build raised SystemExit — guard is missing")


class TestGraphBuildSuccess(WebTestCase):
    """POST /graph/build with seeded chunks must return {status: 'ok'}."""

    def test_build_returns_ok(self):
        resp = self.client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")

    def test_build_response_includes_clusters(self):
        resp = self.client.post("/graph/build", json={"clusters": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["clusters"], 3)

    def test_build_default_clusters_is_6(self):
        resp = self.client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["clusters"], 6)


if __name__ == "__main__":
    unittest.main()

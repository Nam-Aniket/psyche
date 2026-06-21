import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["LLM_PROVIDER"] = "none"
os.environ["EMBED_MODEL"] = "none"
os.environ["CHAT_MODEL"] = "none"
os.environ["RERANK_PROVIDER"] = "none"

import numpy as np
import db
from fastapi.testclient import TestClient


class FakeLLM:
    provider = "fake"
    chat_provider = "fake"
    embed_model = "fake-embed"
    chat_model = "fake-chat"


class TestCreateApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        db.init_db(self.db_path)

        from web.deps import AppState
        self.state = AppState(
            db_path=self.db_path,
            llm=FakeLLM(),
            chunk_ids=np.array([], dtype=np.int32),
            embeddings_matrix=np.array([], dtype=np.float32),
            usearch_index=None,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _make_client(self):
        from web.app import create_app

        app = create_app()
        app.state.psyche = self.state
        return TestClient(app)

    def test_create_app_returns_fastapi_instance(self):
        from fastapi import FastAPI
        from web.app import create_app

        app = create_app()
        self.assertIsInstance(app, FastAPI)

    def test_health_endpoint_returns_200(self):
        client = self._make_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_unknown_route_returns_404(self):
        client = self._make_client()
        resp = client.get("/nonexistent-route")
        self.assertEqual(resp.status_code, 404)

    def test_unhandled_exception_returns_500(self):
        from web.app import create_app
        from fastapi import APIRouter

        app = create_app()
        app.state.psyche = self.state

        router = APIRouter()

        @router.get("/boom")
        def boom():
            raise RuntimeError("test explosion")

        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("detail", resp.json())

    def test_app_state_psyche_accessible(self):
        from web.app import create_app
        from web.deps import get_state
        from fastapi import Request
        from fastapi.testclient import TestClient

        app = create_app()
        app.state.psyche = self.state

        @app.get("/check-state")
        def check_state(request: Request):
            st = get_state(request)
            return {"db_path": st.db_path}

        client = TestClient(app)
        resp = client.get("/check-state")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["db_path"], self.db_path)

    def test_lifespan_skips_build_state_when_psyche_already_set(self):
        """Lifespan guard: if app.state.psyche is pre-set, build_state is NOT called."""
        import web.state as ws
        from web.app import create_app

        called = []
        original = ws.build_state

        def spy(_db_path=None):
            called.append(True)
            return original(_db_path)

        ws.build_state = spy
        try:
            app = create_app()
            app.state.psyche = self.state  # pre-inject
            with TestClient(app):
                pass
            self.assertEqual(called, [], "build_state should not be called when psyche is pre-set")
        finally:
            ws.build_state = original


if __name__ == "__main__":
    unittest.main()

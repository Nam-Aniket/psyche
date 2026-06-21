"""Reusable base test case for all web endpoint tests.

Usage:
    from tests.test_web_base import WebTestCase

    class TestMyRouter(WebTestCase):
        def test_something(self):
            resp = self.client.get("/my-route")
            self.assertEqual(resp.status_code, 200)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be set before any psyche module is imported so LLMClient.check_and_run_setup
# is a no-op and no network calls fire during test collection or setUp.
os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
# Force pure-RRF reranking so flashrank never tries to download a model.
os.environ.setdefault("RERANK_PROVIDER", "none")

import numpy as np

import db
from fastapi.testclient import TestClient


DIM = 8


def fake_embedding(seed: int) -> list:
    """Deterministic stub embedding; never loads a real model."""
    rng = np.random.default_rng(seed)
    return rng.random(DIM, dtype=np.float32).tolist()


class FakeLLM:
    """Offline stand-in for LLMClient.

    provider != 'none' so semantic code-paths exercise the numpy/usearch
    branches; generate_completion returns a fixed string so /chat tests
    work without any network.
    """

    provider = "fake"
    chat_provider = "fake"
    embed_model = "fake-embed"
    chat_model = "fake-chat"

    def get_embedding(self, text: str) -> list:
        return fake_embedding(abs(hash(text)) % 10000)

    def get_embeddings_batch(self, texts: list) -> list:
        return [self.get_embedding(t) for t in texts]

    def generate_completion(self, system_instruction: str, prompt: str) -> str:
        return "FAKE ANSWER"


class WebTestCase(unittest.TestCase):
    """Base class for all web layer tests.

    Provides:
        self.db_path  — absolute path to a seeded temp SQLite DB
        self.state    — AppState with FakeLLM (no network, no real LLMClient)
        self.client   — fastapi.testclient.TestClient wired to create_app()
                       with self.state already injected on app.state.psyche

    Seed data:
        Sources   — "Meditations" (Marcus Aurelius), "Letters" (Seneca)
        Chunks    — 3 total with real embeddings (dim=8)
        Concepts  — "Stoicism", "Virtue"
        Links     — Stoicism → Virtue (emphasizes)
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        db.init_db(self.db_path)

        conn = db.get_connection(self.db_path)
        try:
            # Source 1: Meditations — 2 chunks + embeddings
            s1 = db.add_source(conn, "Meditations", "Marcus Aurelius", "m.txt", "ck_med")
            for i, text in enumerate(
                ["On discipline and the will.", "Nature and the cosmos."]
            ):
                cid = db.add_chunk(conn, s1, i, text, location=f"Book {i + 1}")
                db.add_embedding(conn, cid, fake_embedding(i))

            # Source 2: Letters — 1 chunk + embedding
            s2 = db.add_source(conn, "Letters", "Seneca", "l.txt", "ck_let")
            cid = db.add_chunk(conn, s2, 0, "On the shortness of life.", location="Letter 1")
            db.add_embedding(conn, cid, fake_embedding(99))

            # Concepts and links
            db.add_concept(conn, "Stoicism", "A school of philosophy.", "Philosophy")
            db.add_concept(conn, "Virtue", "Moral excellence.", "Philosophy")
            db.add_concept_link(
                conn,
                "Stoicism",
                "Virtue",
                "emphasizes",
                "Stoicism centers on virtue.",
            )
        finally:
            conn.close()

        # Build the usearch index from the seeded embeddings (real sibling .usearch file).
        db.build_or_update_usearch_index(self.db_path)

        # Construct AppState directly with FakeLLM so build_state() (and its
        # real LLMClient) is never invoked during tests.
        from web.deps import AppState

        usearch_index = None
        try:
            from usearch.index import Index  # type: ignore

            ip = db.index_path_for(self.db_path)
            if os.path.exists(ip):
                usearch_index = Index.restore(ip)
        except Exception:
            usearch_index = None

        chunk_ids = np.array([], dtype=np.int32)
        matrix = np.array([], dtype=np.float32)
        if usearch_index is None:
            c = db.get_connection(self.db_path)
            try:
                recs = db.get_all_embeddings_only(c)
            finally:
                c.close()
            valid = [r for r in recs if r["embedding"] is not None]
            if valid:
                chunk_ids = np.array([r["chunk_id"] for r in valid], dtype=np.int32)
                matrix = np.vstack([r["embedding"] for r in valid])

        self.state = AppState(
            db_path=self.db_path,
            llm=FakeLLM(),
            chunk_ids=chunk_ids,
            embeddings_matrix=matrix,
            usearch_index=usearch_index,
        )

        import web.app

        app = web.app.create_app()
        # Inject state BEFORE TestClient enters context so lifespan guard finds
        # psyche already set and skips the real build_state() call entirely.
        app.state.psyche = self.state
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

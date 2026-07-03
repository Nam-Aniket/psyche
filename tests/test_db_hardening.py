import os
import shutil
import sqlite3
import tempfile
import threading
import unittest

import db
import query


class TestConnectionHardening(unittest.TestCase):
    """Every connection (init_db, get_connection, sync hook, web) must carry the
    concurrency-safety pragmas so two clients on one DB don't trip 'locked'."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "k.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_pragmas_sets_busy_timeout_and_wal(self):
        conn = sqlite3.connect(self.path)
        try:
            db._apply_connection_pragmas(conn)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            conn.close()

    def test_get_connection_is_hardened(self):
        db.init_db(self.path)
        conn = db.get_connection(self.path)
        try:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            conn.close()

    def test_init_db_leaves_db_in_wal(self):
        db.init_db(self.path)
        conn = sqlite3.connect(self.path)
        try:
            # WAL is a persistent DB property — a fresh connection sees it.
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            conn.close()


class TestRankerThreadSafety(unittest.TestCase):
    """Background prewarm + a concurrent first tool call must not race."""

    def test_concurrent_get_ranker_is_consistent(self):
        prev = os.environ.get("RERANK_PROVIDER")
        os.environ["RERANK_PROVIDER"] = "none"  # avoid loading a real model in CI
        query._ranker = None
        query._ranker_loaded = False
        results = []

        def grab():
            results.append(query.get_ranker())

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All callers agree on a single result, no exception raised.
        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(id(r) for r in results)), 1)

        if prev is None:
            os.environ.pop("RERANK_PROVIDER", None)
        else:
            os.environ["RERANK_PROVIDER"] = prev


if __name__ == "__main__":
    unittest.main()

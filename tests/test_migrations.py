import unittest
import os
import sqlite3
import db


class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_migrations.db"
        self.resolved_db_path = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)
        # Snapshot MIGRATIONS so per-test mutations don't leak.
        self._saved_migrations = list(db.MIGRATIONS)

    def tearDown(self):
        # Restore MIGRATIONS to avoid polluting other tests.
        db.MIGRATIONS[:] = self._saved_migrations
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)

    def test_fresh_db_gets_schema_version_stamped(self):
        db.init_db(self.db_path)
        conn = db.get_connection(self.resolved_db_path)
        try:
            stamped = db.get_metadata(conn, "schema_version")
            self.assertEqual(stamped, str(db.SCHEMA_VERSION))
        finally:
            conn.close()

    def test_v3_columns_present_on_fresh_db(self):
        db.init_db(self.db_path)
        conn = db.get_connection(self.resolved_db_path)
        try:
            cols = lambda t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            self.assertIn("project", cols("atomic_memories"))
            self.assertIn("retrieval_count", cols("atomic_memories"))
            self.assertIn("last_retrieved_at", cols("atomic_memories"))
            self.assertIn("plan_id", cols("goals"))
            self.assertIn("plan_id", cols("experiments"))
            self.assertEqual(db.get_metadata(conn, "schema_version"), str(db.SCHEMA_VERSION))
        finally:
            conn.close()

    def test_pending_migration_runs_once_and_advances_version(self):
        # Start from a fresh, stamped DB at the current SCHEMA_VERSION.
        db.init_db(self.db_path)

        calls = {"count": 0}

        def fake_migration(conn):
            calls["count"] += 1
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _migration_probe (id INTEGER PRIMARY KEY)"
            )

        target_version = db.SCHEMA_VERSION + 1
        db.MIGRATIONS.append((target_version, fake_migration))

        # Simulate a DB that is one version behind the (now bumped) schema.
        conn = db.get_connection(self.resolved_db_path)
        try:
            db.set_metadata(conn, "schema_version", str(db.SCHEMA_VERSION))
        finally:
            conn.close()

        # Patch SCHEMA_VERSION so the runner sees the migration as pending.
        original_version = db.SCHEMA_VERSION
        db.SCHEMA_VERSION = target_version
        try:
            conn = db.get_connection(self.resolved_db_path)
            try:
                db._run_migrations(conn)
                conn.commit()
            finally:
                conn.close()

            self.assertEqual(calls["count"], 1)

            conn = db.get_connection(self.resolved_db_path)
            try:
                self.assertEqual(
                    db.get_metadata(conn, "schema_version"), str(target_version)
                )
                # Probe table created by the migration exists.
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='_migration_probe'"
                )
                self.assertIsNotNone(cur.fetchone())
            finally:
                conn.close()

            # Running again with no pending migration must not re-run it.
            conn = db.get_connection(self.resolved_db_path)
            try:
                db._run_migrations(conn)
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(calls["count"], 1)
        finally:
            db.SCHEMA_VERSION = original_version


if __name__ == "__main__":
    unittest.main()

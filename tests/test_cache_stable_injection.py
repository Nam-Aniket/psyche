"""Tests for cache-stable session-start injection (Step 4)."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

import db
import memzero

# Make hooks importable
HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS_DIR)
import _hook_common as hc  # noqa: E402


def _insert_fact(conn, fact, category="preference", project=None):
    conn.execute(
        "INSERT INTO atomic_memories (fact, category, project, agent_id, run_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,datetime('now'),datetime('now'))",
        (fact, category, project, "agent", "run"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _touch_updated_at(conn, row_id):
    """Bump updated_at to now (simulates a supersession / re-touch)."""
    conn.execute(
        "UPDATE atomic_memories SET updated_at = datetime('now') WHERE id = ?",
        (row_id,),
    )
    conn.commit()


class TestStandingFactRowsStable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            # Insert three facts in order; ids will be 1, 2, 3
            self.id1 = _insert_fact(conn, "fact-alpha")
            time.sleep(0.01)
            self.id2 = _insert_fact(conn, "fact-beta")
            time.sleep(0.01)
            self.id3 = _insert_fact(conn, "fact-gamma")
        finally:
            conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_stable_order_unchanged_after_touch(self):
        """stable=True keeps id ASC even when the oldest fact is touched."""
        rows_before = memzero.standing_fact_rows(top=10, db_path=self.db_path, stable=True)
        ids_before = [r["id"] for r in rows_before]

        # Touch the oldest fact (id1) — updated_at is now the newest
        conn = db.get_connection(self.db_path)
        try:
            _touch_updated_at(conn, self.id1)
        finally:
            conn.close()

        rows_after = memzero.standing_fact_rows(top=10, db_path=self.db_path, stable=True)
        ids_after = [r["id"] for r in rows_after]

        self.assertEqual(ids_before, ids_after, "stable order must be invariant to updated_at changes")
        self.assertEqual(ids_before, sorted(ids_before), "stable order must be id ASC")

    def test_unstable_order_surfaces_touched_fact_first(self):
        """stable=False (legacy) surfaces the most recently touched fact first."""
        conn = db.get_connection(self.db_path)
        try:
            _touch_updated_at(conn, self.id1)
        finally:
            conn.close()

        rows = memzero.standing_fact_rows(top=10, db_path=self.db_path, stable=False)
        self.assertEqual(rows[0]["id"], self.id1, "unstable order: touched fact must sort first")


class TestFormatFactsIncludeDate(unittest.TestCase):
    def test_no_date_format_deterministic_across_touch(self):
        """include_date=False renders identically regardless of updated_at."""
        row = {"id": 1, "fact": "prefer dark mode", "category": "preference",
               "updated_at": "2024-01-01T00:00:00", "project": None}
        text1 = memzero.format_facts([row], include_date=False)

        # Simulate a touch by changing updated_at
        row["updated_at"] = "2026-06-12T12:34:56"
        text2 = memzero.format_facts([row], include_date=False)

        self.assertEqual(text1, text2, "include_date=False output must be invariant to updated_at")
        self.assertNotIn("2024", text1)
        self.assertNotIn("2026", text1)

    def test_include_date_true_contains_date(self):
        """include_date=True (default) still embeds the date."""
        row = {"id": 1, "fact": "prefer dark mode", "category": "preference",
               "updated_at": "2024-03-15T00:00:00", "project": None}
        text = memzero.format_facts([row], include_date=True)
        self.assertIn("2024-03-15", text)


class TestSessionStartBlockStable(unittest.TestCase):
    """Session-start injected text is identical across a fact-touch (no new facts)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            self.id1 = _insert_fact(conn, "always use type hints")
            time.sleep(0.01)
            self.id2 = _insert_fact(conn, "prefer short functions")
        finally:
            conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _build_injection_text(self):
        rows = memzero.standing_fact_rows(top=12, db_path=self.db_path, stable=True)
        return memzero.format_facts(rows, max_chars=1500, include_date=False)

    def test_block_identical_after_touch(self):
        text1 = self._build_injection_text()

        conn = db.get_connection(self.db_path)
        try:
            _touch_updated_at(conn, self.id1)
        finally:
            conn.close()

        text2 = self._build_injection_text()
        self.assertEqual(text1, text2, "session-start block must be identical after a touch (no new facts)")


class TestStableBlockHash(unittest.TestCase):
    def test_returns_12_char_hex(self):
        h = hc.stable_block_hash("hello world")
        self.assertEqual(len(h), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_same_input_same_hash(self):
        self.assertEqual(hc.stable_block_hash("abc"), hc.stable_block_hash("abc"))

    def test_different_input_different_hash(self):
        self.assertNotEqual(hc.stable_block_hash("abc"), hc.stable_block_hash("xyz"))


def _insert_fact_at(conn, fact, ts, category="decision", project=None):
    """Insert a standing fact with an explicit timestamp (deterministic ordering)."""
    conn.execute(
        "INSERT INTO atomic_memories (fact, category, project, agent_id, run_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (fact, category, project, "agent", "run", ts, ts),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestSplitStandingBlock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            for i in range(15):
                _insert_fact_at(conn, f"decision number {i} about something durable",
                                f"2026-06-01T00:00:{i:02d}+00:00")
        finally:
            conn.close()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_stable_slots_plus_recent_tail(self):
        stable, tail = memzero.standing_fact_rows_split(db_path=self.db_path)
        self.assertEqual(len(stable), memzero.STABLE_SLOTS)
        self.assertEqual(len(tail), memzero.RECENT_SLOTS)
        stable_ids = [r["id"] for r in stable]
        self.assertEqual(stable_ids, sorted(stable_ids))          # oldest-first, byte-stable
        tail_ids = {r["id"] for r in tail}
        self.assertFalse(tail_ids & set(stable_ids))              # no overlap
        newest_id = max(r["id"] for r in stable + tail)
        self.assertIn(newest_id, tail_ids)                        # newest decision surfaces

    def test_stable_prefix_identical_across_new_facts(self):
        s1, _ = memzero.standing_fact_rows_split(db_path=self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            _insert_fact_at(conn, "decision sixteen just landed",
                            "2026-06-01T00:01:00+00:00")
        finally:
            conn.close()
        s2, t2 = memzero.standing_fact_rows_split(db_path=self.db_path)
        self.assertEqual([r["id"] for r in s1], [r["id"] for r in s2])
        self.assertIn("decision sixteen just landed", [r["fact"] for r in t2])


if __name__ == "__main__":
    unittest.main()

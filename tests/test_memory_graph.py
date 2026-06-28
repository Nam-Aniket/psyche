"""Tests for memzero.entity_graph — the co-occurrence graph over memory entities
that powers the web Memory tab's graph view."""
import os
import unittest
from datetime import datetime, timezone

import db
import memzero


class TestEntityGraph(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_entity_graph.db"
        self.resolved = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved):
            os.remove(self.resolved)
        db.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.resolved):
            os.remove(self.resolved)

    def _add(self, conn, fact, category, entities):
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO atomic_memories (fact, category, created_at, updated_at) VALUES (?,?,?,?)",
            (fact, category, now, now),
        )
        mid = cur.lastrowid
        for e in entities:
            conn.execute("INSERT INTO memory_entities (memory_id, entity) VALUES (?,?)", (mid, e))
        conn.commit()
        return mid

    def test_empty_store_returns_empty_graph(self):
        self.assertEqual(memzero.entity_graph(db_path=self.db_path), {"nodes": [], "edges": []})

    def test_cooccurrence_nodes_and_edges(self):
        conn = db.get_connection(self.resolved)
        try:
            self._add(conn, "Alice prefers Python", "fact", ["Alice", "Python"])
            self._add(conn, "Alice drinks Coffee", "preference", ["Alice", "Coffee"])
            self._add(conn, "Python pairs with Coffee", "fact", ["Python", "Coffee"])
        finally:
            conn.close()

        g = memzero.entity_graph(db_path=self.db_path)
        names = {n["name"] for n in g["nodes"]}
        self.assertEqual(names, {"Alice", "Python", "Coffee"})
        # Every node carries the concept-graph fields the SVG engine needs.
        for n in g["nodes"]:
            self.assertEqual(set(n), {"id", "name", "definition", "category"})
        # Three entities each pair once -> three co-occurrence edges.
        pairs = {frozenset((e["source"], e["target"])) for e in g["edges"]}
        self.assertEqual(pairs, {frozenset(("Alice", "Python")),
                                 frozenset(("Alice", "Coffee")),
                                 frozenset(("Python", "Coffee"))})

    def test_min_cooccurrence_filters_weak_edges(self):
        conn = db.get_connection(self.resolved)
        try:
            self._add(conn, "m1", "fact", ["X", "Y"])   # X-Y once
            self._add(conn, "m2", "fact", ["X", "Y"])   # X-Y twice
            self._add(conn, "m3", "fact", ["X", "Z"])   # X-Z once
        finally:
            conn.close()

        g = memzero.entity_graph(db_path=self.db_path, min_cooccurrence=2)
        pairs = {frozenset((e["source"], e["target"])) for e in g["edges"]}
        self.assertEqual(pairs, {frozenset(("X", "Y"))})  # only the >=2 pair survives


if __name__ == "__main__":
    unittest.main()

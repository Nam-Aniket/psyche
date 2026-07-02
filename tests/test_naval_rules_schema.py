import unittest
import os
import db


class TestNavalRulesSchema(unittest.TestCase):
    """Task 1: maps layer + temporal model for the naval decision engine —
    additive columns on `rules` and a `rule_links` table for tensions/evolution."""

    def setUp(self):
        self.db_path = "test_naval_rules.db"
        self.resolved_db_path = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)

    def tearDown(self):
        if os.path.exists(self.resolved_db_path):
            os.remove(self.resolved_db_path)

    def test_naval_columns_and_rule_links_on_fresh_db(self):
        db.init_db(self.db_path)
        conn = db.get_connection(self.resolved_db_path)
        try:
            rules_cols = {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
            self.assertTrue(
                {"map", "source_date", "source_tier", "principle_type", "current_stance"}
                <= rules_cols,
                f"missing naval columns; have {rules_cols}",
            )
            self.assertEqual(
                conn.execute("SELECT count(*) FROM rule_links").fetchone()[0], 0
            )
            link_cols = {r[1] for r in conn.execute("PRAGMA table_info(rule_links)")}
            self.assertTrue(
                {"rule_a", "rule_b", "link_type", "as_of", "why", "source"} <= link_cols,
                f"missing rule_links columns; have {link_cols}",
            )
        finally:
            conn.close()

    def test_add_rule_persists_map_fields_and_groups_by_map(self):
        db.init_db(self.db_path)
        conn = db.get_connection(self.resolved_db_path)
        try:
            db.add_rule(
                conn, "naval",
                "Own equity; renting out your time can't make you rich.",
                source="HTGR tweetstorm", confidence="core",
                map="leverage", source_date="2018-05-31",
                source_tier="canonical", principle_type="derived",
            )
            db.add_rule(
                conn, "naval",
                "Desire is a contract you make to be unhappy until you get what you want.",
                source="Almanack", confidence="core",
                map="equanimity", source_date="2020-01-01",
                source_tier="canonical", principle_type="axiom",
            )
            grouped = db.get_rules_by_map(conn, "naval")
            self.assertIn("leverage", grouped)
            self.assertIn("equanimity", grouped)
            self.assertEqual(len(grouped["leverage"]), 1)
            self.assertEqual(grouped["leverage"][0]["source_tier"], "canonical")
            self.assertEqual(grouped["leverage"][0]["map"], "leverage")
        finally:
            conn.close()

    def test_add_rule_link_records_evolution(self):
        db.init_db(self.db_path)
        conn = db.get_connection(self.resolved_db_path)
        try:
            a = db.add_rule(
                conn, "naval", "Happiness is a choice / a skill you can develop.",
                source="TFS#97", source_date="2015-08-18",
                source_tier="evidence", map="equanimity", principle_type="axiom",
            )
            b = db.add_rule(
                conn, "naval",
                "Distrust the word 'happiness'; aim for peace and equanimity.",
                source="Modern Wisdom #922", source_date="2025-03-01",
                source_tier="evidence", map="equanimity", principle_type="axiom",
                current_stance="peace over the word happiness",
            )
            lid = db.add_rule_link(
                conn, a, b, "evolution",
                as_of="2025-03-01",
                why="Naval disavows his earlier crisp formulation on tape",
                source="Modern Wisdom #922",
            )
            self.assertIsInstance(lid, int)
            row = conn.execute(
                "SELECT rule_a, rule_b, link_type FROM rule_links WHERE id = ?", (lid,)
            ).fetchone()
            self.assertEqual((row[0], row[1], row[2]), (a, b, "evolution"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

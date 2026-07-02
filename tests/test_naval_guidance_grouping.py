import unittest
import os

import db
import guidance


class TestNavalGuidanceGrouping(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_naval_guidance.db"
        self.resolved = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved):
            os.remove(self.resolved)
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.resolved)
        self.r1 = db.add_rule(
            self.conn, "naval", "Seek wealth, not status. — Decision rule: owned asset or rank?",
            source="HTGR", map="leverage", source_tier="canonical", principle_type="axiom")
        self.r2 = db.add_rule(
            self.conn, "naval", "Happiness is a skill. — Decision rule: practice, not outcome.",
            source="nav.al", map="equanimity", source_tier="canonical", principle_type="axiom",
            current_stance="2025: downgraded to a contextual note; operative frame is peace.")
        self.r3 = db.add_rule(
            self.conn, "naval", "Barbell your risk. — Decision rule: no fragile middle.",
            source="Antifragile — Taleb", map="antifragility", source_tier="foundational",
            principle_type="axiom")
        db.add_rule_link(self.conn, self.r1, self.r2, "tension",
                         why="creating wealth vs desire for external things")

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.resolved):
            os.remove(self.resolved)

    def test_groups_rules_under_map_headings_in_pack_order(self):
        out = guidance.format_rules_by_map(self.conn, "naval")
        self.assertIn("Wealth & Leverage says:", out)
        self.assertIn("Risk & Optionality says:", out)
        self.assertIn("Happiness & Desire says:", out)
        # pack order: leverage before antifragility before equanimity
        self.assertLess(out.index("Wealth & Leverage"), out.index("Risk & Optionality"))
        self.assertLess(out.index("Risk & Optionality"), out.index("Happiness & Desire"))
        # tier tags visible so the synthesizer can tell Naval's words from grounding
        self.assertIn("[canonical]", out)
        self.assertIn("[foundational]", out)

    def test_surfaces_tension_line_and_current_stance(self):
        out = guidance.format_rules_by_map(self.conn, "naval")
        self.assertIn("Tension:", out)
        self.assertIn("creating wealth vs desire", out)
        self.assertIn("Current stance:", out)
        self.assertIn("operative frame is peace", out)

    def test_domain_without_maps_renders_empty(self):
        out = guidance.format_rules_by_map(self.conn, "wealth")
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()

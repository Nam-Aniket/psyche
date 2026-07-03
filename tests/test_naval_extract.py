import unittest
import os
import db
from naval_extract import schema, writer


VALID_ATOM = {
    "statement": "Renting out your time can't make you rich; you must own equity.",
    "decision_rule": "Paid for hours, or own a piece that keeps paying? No ownership -> labor -> flag.",
    "map": "leverage",
    "source": "HTGR tweetstorm",
    "source_date": "2018-05-31",
    "source_tier": "canonical",
    "principle_type": "derived",
}


class TestNavalExtractSchema(unittest.TestCase):
    def test_validate_accepts_complete_atom(self):
        self.assertEqual(schema.validate(VALID_ATOM), [])

    def test_validate_rejects_missing_decision_rule(self):
        atom = {k: v for k, v in VALID_ATOM.items() if k != "decision_rule"}
        errors = schema.validate(atom)
        self.assertTrue(any("decision_rule" in e for e in errors))
        self.assertFalse(schema.is_rule(atom))

    def test_validate_flags_unknown_map(self):
        atom = dict(VALID_ATOM, map="bogus")
        self.assertTrue(any("map" in e for e in schema.validate(atom)))

    def test_validate_flags_bad_tier(self):
        atom = dict(VALID_ATOM, source_tier="hearsay")
        self.assertTrue(any("source_tier" in e for e in schema.validate(atom)))


class TestNavalExtractWriter(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_naval_extract.db"
        self.resolved = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved):
            os.remove(self.resolved)
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.resolved)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.resolved):
            os.remove(self.resolved)

    def test_write_atom_inserts_rule_with_map_fields(self):
        rid = writer.write_atom(self.conn, VALID_ATOM)
        self.assertIsInstance(rid, int)
        grouped = db.get_rules_by_map(self.conn, "naval")
        self.assertIn("leverage", grouped)
        rule = grouped["leverage"][0]
        self.assertEqual(rule["map"], "leverage")
        self.assertEqual(rule["source_tier"], "canonical")
        self.assertEqual(rule["source_date"], "2018-05-31")
        self.assertIn("Decision rule:", rule["rule_text"])

    def test_write_atom_returns_none_for_note(self):
        note = {k: v for k, v in VALID_ATOM.items() if k != "decision_rule"}
        rid = writer.write_atom(self.conn, note)
        self.assertIsNone(rid)
        self.assertEqual(db.get_rules(self.conn, domain="naval"), [])

    def test_write_atom_raises_on_invalid_rule(self):
        bad = dict(VALID_ATOM, map="bogus")
        with self.assertRaises(ValueError):
            writer.write_atom(self.conn, bad)


if __name__ == "__main__":
    unittest.main()

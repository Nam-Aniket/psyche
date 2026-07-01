import unittest
import os
import tempfile
import textwrap
import db
from naval_extract import load


SAMPLE = textwrap.dedent("""
map: leverage
source_tier: canonical
atoms:
  - id: t-01
    statement: "Seek wealth, not status."
    decision_rule: "Owned asset or just rank? Rank -> flag."
    source: "Src"
    source_date: "2018-05-31"
    principle_type: axiom
  - id: t-02
    statement: "A principle with no decision rule is a note."
    source: "Src"
    source_date: "2018-05-31"
    principle_type: derived
""")

EMPTY = "map: persuasion\nsource_tier: canonical\natoms: []\n"


class TestNavalLoad(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "leverage.yaml"), "w") as f:
            f.write(SAMPLE)
        with open(os.path.join(self.dir, "persuasion.yaml"), "w") as f:
            f.write(EMPTY)
        self.db_path = "test_naval_load.db"
        self.resolved = db.resolve_db_path(self.db_path)
        if os.path.exists(self.resolved):
            os.remove(self.resolved)
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.resolved)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.resolved):
            os.remove(self.resolved)

    def test_writes_rules_and_skips_notes(self):
        summary = load.load_atoms_dir(self.conn, self.dir)
        self.assertEqual(len(summary["written"]), 1)      # t-01 only
        self.assertEqual(summary["notes"], ["t-02"])       # note skipped, not written
        self.assertEqual(summary["by_map"], {"leverage": 1})
        grouped = db.get_rules_by_map(self.conn, "naval")
        rule = grouped["leverage"][0]
        self.assertEqual(rule["map"], "leverage")
        self.assertEqual(rule["source_tier"], "canonical")

    def test_fail_closed_leaves_no_partial_writes(self):
        # An invalid rule-atom must abort the whole load before anything is written.
        with open(os.path.join(self.dir, "aaa_bad.yaml"), "w") as f:
            f.write("map: bogus\nsource_tier: canonical\natoms:\n"
                    "  - id: b-1\n    statement: x\n    decision_rule: 'y?'\n"
                    "    source: s\n    source_date: '2018-01-01'\n    principle_type: axiom\n")
        with self.assertRaises(ValueError):
            load.load_atoms_dir(self.conn, self.dir)
        self.assertEqual(db.get_rules(self.conn, domain="naval"), [])


if __name__ == "__main__":
    unittest.main()

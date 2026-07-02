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

FOUND_ONE = textwrap.dedent("""
map: antifragility
source_tier: foundational
atoms:
  - id: f-01
    statement: "Barbell your risk."
    decision_rule: "Everything medium-risk -> restructure."
    source: "Antifragile - Taleb"
    source_date: "2012-11-27"
    principle_type: axiom
""")

EVIDENCE = textwrap.dedent("""
source: "Test Pod #1"
source_id: 999
source_date: "2025"
source_tier: evidence
items:
  - id: ev-01
    rule: t-01
    stance: confirms
    chunk: 0
    quote: "Seek wealth quote."
    note: "n1"
  - id: ev-02
    rule: t-01
    stance: strains
    chunk: 1
    quote: "Old quote."
    note: "n2"
evolutions:
  - id: evo-01
    from: t-01
    to: f-01
    as_of: "2025"
    why: "stance shifted"
    quote: "q"
    current_stance: "new stance text"
""")

LINKED = textwrap.dedent("""
map: antifragility
source_tier: foundational
atoms:
  - id: f-01
    statement: "Barbell your risk."
    decision_rule: "Everything medium-risk -> restructure."
    source: "Antifragile - Taleb"
    source_date: "2012-11-27"
    principle_type: axiom
    supports: t-01
  - id: f-02
    statement: "Via negativa."
    decision_rule: "Remove before adding."
    source: "Antifragile - Taleb"
    source_date: "2012-11-27"
    principle_type: axiom
    supports: missing-99
  - id: f-03
    statement: "Judge across alternative histories."
    decision_rule: "Grade the process, not the outcome."
    source: "Fooled by Randomness - Taleb"
    source_date: "2001-01-01"
    principle_type: axiom
    tension_with: t-01
""")


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

    def test_link_pass_materializes_supports_and_tensions(self):
        load.load_atoms_dir(self.conn, self.dir)
        fdir = os.path.join(self.dir, "foundational")
        os.makedirs(fdir)
        with open(os.path.join(fdir, "antifragility.yaml"), "w") as f:
            f.write(LINKED)
        load.load_atoms_dir(self.conn, fdir)

        result = load.link_atoms_dirs(self.conn, [self.dir, fdir])
        # f-01 supports t-01 and f-03 tension t-01 written; f-02's target is unknown
        self.assertEqual(sorted(w[0] for w in result["written"]), ["f-01", "f-03"])
        self.assertEqual(result["unresolved"], [("f-02", "supports", "missing-99")])
        rows = self.conn.execute(
            "SELECT rule_a, rule_b, link_type FROM rule_links ORDER BY link_type").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r[2] for r in rows}, {"supports", "tension"})

        # idempotent: a second pass writes nothing new (tension checked both directions)
        again = load.link_atoms_dirs(self.conn, [self.dir, fdir])
        self.assertEqual(again["written"], [])
        self.assertEqual(again["skipped_existing"], 2)
        n = self.conn.execute("SELECT count(*) FROM rule_links").fetchone()[0]
        self.assertEqual(n, 2)

    def test_evidence_loader_writes_evidence_evolution_and_stance(self):
        load.load_atoms_dir(self.conn, self.dir)
        fdir = os.path.join(self.dir, "foundational")
        os.makedirs(fdir)
        with open(os.path.join(fdir, "antifragility.yaml"), "w") as f:
            f.write(FOUND_ONE)
        load.load_atoms_dir(self.conn, fdir)
        evdir = os.path.join(self.dir, "evidence")
        os.makedirs(evdir)
        with open(os.path.join(evdir, "2025-test.yaml"), "w") as f:
            f.write(EVIDENCE)

        result = load.load_evidence_dir(self.conn, evdir, [self.dir, fdir])
        self.assertEqual(result["evidence"], 2)
        self.assertEqual(result["evolutions"], 1)
        rows = self.conn.execute(
            "SELECT rule_id, stance, source, as_of FROM rule_evidence ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "confirms")
        self.assertEqual(rows[0][2], "Test Pod #1")
        self.assertEqual(rows[0][3], "2025")
        evo = self.conn.execute(
            "SELECT rule_a, rule_b, as_of, why FROM rule_links WHERE link_type='evolution'").fetchall()
        self.assertEqual(len(evo), 1)
        self.assertEqual(evo[0][2], "2025")
        stance = self.conn.execute(
            "SELECT current_stance FROM rules WHERE id=?", (evo[0][0],)).fetchone()[0]
        self.assertEqual(stance, "new stance text")
        # evidence never mints rules
        n_rules = self.conn.execute(
            "SELECT count(*) FROM rules WHERE source_tier='evidence'").fetchone()[0]
        self.assertEqual(n_rules, 0)

        # idempotent second load: nothing duplicated
        again = load.load_evidence_dir(self.conn, evdir, [self.dir, fdir])
        self.assertEqual(again["evidence"], 0)
        self.assertEqual(again["skipped"], 2)
        self.assertEqual(again["evolutions"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM rule_evidence").fetchone()[0], 2)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM rule_links WHERE link_type='evolution'").fetchone()[0], 1)

    def test_evidence_loader_fail_closed_on_bad_rule_ref(self):
        load.load_atoms_dir(self.conn, self.dir)
        evdir = os.path.join(self.dir, "evidence")
        os.makedirs(evdir)
        with open(os.path.join(evdir, "bad.yaml"), "w") as f:
            f.write(EVIDENCE.replace("rule: t-01", "rule: nope-99"))
        with self.assertRaises(ValueError):
            load.load_evidence_dir(self.conn, evdir, [self.dir])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM rule_evidence").fetchone()[0], 0)

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

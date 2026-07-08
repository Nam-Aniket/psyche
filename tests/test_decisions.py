"""Tests for the decision journal ledger (decisions.py).

Temp SQLite DBs; no LLM calls, no MCP server needed. Mirrors the
test_brainstorm.py setup conventions.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decisions


def _valid(**over):
    """A fully valid journal_decision kwargs dict; override any field per test."""
    kw = dict(
        situation="TidyMyData prospect wants 50% off the audit",
        game="lemons market",
        game_source="atoms",
        atoms_applied=["coop-01"],
        recommendation="Hold price; offer the free teardown instead of a discount",
        falsifier="They walk AND a comparable prospect later converts at full price",
        prediction="They accept the teardown within a week",
        confidence=70,
        review_by="2026-07-22",
    )
    kw.update(over)
    return kw


class LedgerBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)


class TestJournalDecision(LedgerBase):
    def test_roundtrip(self):
        rec = decisions.journal_decision(self.path, **_valid())
        self.assertEqual(rec["status"], "open")
        self.assertEqual(rec["atoms_applied"], ["coop-01"])
        self.assertEqual(rec["confidence"], 70)
        again = decisions.get_decision(self.path, rec["id"])
        self.assertEqual(again, rec)

    def test_missing_prediction_rejected_no_row(self):
        with self.assertRaises(decisions.ValidationError):
            decisions.journal_decision(self.path, **_valid(prediction=""))
        self.assertEqual(decisions.list_decisions(self.path), [])

    def test_bad_confidence_rejected(self):
        for bad in (-1, 101, "70", 70.5, True):
            with self.assertRaises(decisions.ValidationError):
                decisions.journal_decision(self.path, **_valid(confidence=bad))

    def test_bad_review_by_rejected(self):
        with self.assertRaises(decisions.ValidationError):
            decisions.journal_decision(self.path, **_valid(review_by="soonish"))

    def test_bad_game_source_rejected(self):
        with self.assertRaises(decisions.ValidationError):
            decisions.journal_decision(self.path, **_valid(game_source="vibes"))

    def test_atoms_source_requires_atom_ids(self):
        with self.assertRaises(decisions.ValidationError):
            decisions.journal_decision(self.path, **_valid(atoms_applied=[]))

    def test_model_knowledge_allows_empty_atoms(self):
        rec = decisions.journal_decision(
            self.path, **_valid(game_source="model-knowledge", atoms_applied=[]))
        self.assertEqual(rec["game_source"], "model-knowledge")


class TestDueDecisions(LedgerBase):
    def test_due_only_lists_open_past_review(self):
        past = decisions.journal_decision(self.path, **_valid(review_by="2026-01-01"))
        decisions.journal_decision(self.path, **_valid(review_by="2099-01-01"))
        due = decisions.list_due_decisions(self.path, today="2026-07-08")
        self.assertEqual([d["id"] for d in due], [past["id"]])

    def test_scored_decisions_are_not_due(self):
        rec = decisions.journal_decision(self.path, **_valid(review_by="2026-01-01"))
        decisions.score_decision(self.path, rec["id"], outcome="accepted teardown",
                                 hit="yes")
        self.assertEqual(decisions.list_due_decisions(self.path, today="2026-07-08"), [])

    def test_due_on_the_review_date_itself(self):
        rec = decisions.journal_decision(self.path, **_valid(review_by="2026-07-08"))
        due = decisions.list_due_decisions(self.path, today="2026-07-08")
        self.assertEqual([d["id"] for d in due], [rec["id"]])


class TestScoreDecision(LedgerBase):
    def test_score_closes_record(self):
        rec = decisions.journal_decision(self.path, **_valid())
        scored = decisions.score_decision(self.path, rec["id"],
                                          outcome="accepted teardown", hit="yes")
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["outcome"], "accepted teardown")
        self.assertEqual(scored["hit"], "yes")

    def test_double_score_refused(self):
        rec = decisions.journal_decision(self.path, **_valid())
        decisions.score_decision(self.path, rec["id"], outcome="x", hit="no")
        with self.assertRaises(decisions.ValidationError):
            decisions.score_decision(self.path, rec["id"], outcome="y", hit="yes")
        # first scoring must survive untouched
        self.assertEqual(decisions.get_decision(self.path, rec["id"])["outcome"], "x")

    def test_bad_hit_rejected(self):
        rec = decisions.journal_decision(self.path, **_valid())
        with self.assertRaises(decisions.ValidationError):
            decisions.score_decision(self.path, rec["id"], outcome="x", hit="maybe")

    def test_unknown_id_rejected(self):
        with self.assertRaises(decisions.ValidationError):
            decisions.score_decision(self.path, 999, outcome="x", hit="yes")


class TestHookInjection(LedgerBase):
    def _open_loops(self):
        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
        sys.path.insert(0, hooks_dir)
        try:
            import psyche_session_start as ss
            return ss.open_loops(ledger_path=self.path)
        finally:
            sys.path.remove(hooks_dir)

    def test_due_decision_appears_in_open_loops(self):
        decisions.journal_decision(self.path, **_valid(review_by="2020-01-01"))
        out = self._open_loops()
        self.assertIn("Decisions due for scoring", out)
        self.assertIn("TidyMyData prospect", out)

    def test_no_due_decisions_no_section(self):
        decisions.journal_decision(self.path, **_valid(review_by="2099-01-01"))
        out = self._open_loops()
        self.assertNotIn("Decisions due for scoring", out)


if __name__ == "__main__":
    unittest.main()

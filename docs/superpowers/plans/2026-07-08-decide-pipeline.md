# /decide Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (subagent-driven-development is excluded — this user has a hard no-subagents rule). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic decision pipeline: `/decide` classifies a real decision against the atom corpus, journals a scorable prediction into a fail-closed SQLite ledger, and the session-start hook surfaces due reviews automatically; `/ideas` gives the existing collision engine a guaranteed trigger.

**Architecture:** Hybrid (spec approach C). Silent-failure steps (journal write, review recall) live in Python — a new `decisions.py` ledger module mirroring the hypothesis-ledger conventions in `brainstorm.py`, three new MCP tools in `mcp_server.py`, one block added to `hooks/psyche_session_start.py`. Loud-failure steps (classification, recommendation) live in two Claude Code skills.

**Tech Stack:** Python 3 stdlib (sqlite3, json, datetime), unittest (matching `tests/test_brainstorm.py`), hand-rolled JSON-RPC MCP server (existing pattern), Claude Code SKILL.md files.

**Working branch:** `feat/decide-pipeline` (already exists, spec committed).

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `decisions.py` | Create | Decision ledger: validated insert, due listing, scoring. No LLM, no MCP — pure storage. |
| `tests/test_decisions.py` | Create | Unit tests for the ledger + the hook block. Temp SQLite, no LLM. |
| `mcp_server.py` | Modify | 3 tool schemas in `tools/list`, 3 `elif` dispatch branches. Thin wiring only. |
| `hooks/psyche_session_start.py` | Modify | One extra try-block in `open_loops()`: due decisions. |
| `skills/decide/SKILL.md` | Create | The /decide judgment procedure (repo canonical copy). |
| `skills/ideas/SKILL.md` | Create | The /ideas trigger for the existing brainstorm tools (repo canonical copy). |
| `~/.claude/skills/decide/SKILL.md`, `~/.claude/skills/ideas/SKILL.md` | Install (cp) | Live copies Claude Code actually loads. |

Conventions to follow (from `brainstorm.py`): `path` is the FIRST parameter of every ledger function (None → `db.resolve_db_path("brainstorm.db")`); `CREATE TABLE IF NOT EXISTS` inside the connection helper; a module-level `_COLS` list; `_now()` returns UTC ISO; rows returned as plain dicts.

---

### Task 1: Ledger — `journal_decision` (validated insert + read-back)

**Files:**
- Create: `decisions.py`
- Create: `tests/test_decisions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_decisions.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decisions'`

- [ ] **Step 3: Write the implementation**

Create `decisions.py`:

```python
"""Decision journal ledger — the deterministic core of the /decide pipeline.

Same storage pattern and database FILE as the hypothesis ledger in
brainstorm.py (db.resolve_db_path("brainstorm.db")): one file, one backup path.

Status model: rows are 'open' or 'scored'. "Due" is computed (open AND
review_by <= today) by list_due_decisions rather than stored, so no background
job is needed to flip states.

Fail-closed: journal_decision validates every field and raises ValidationError
without writing anything. A journal entry without a scorable prediction would
defeat the journal's purpose (calibration), so prediction/confidence/review_by
are hard requirements.
"""
import json
import sqlite3
from datetime import date, datetime, timezone

import db


class ValidationError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ledger_path():
    return db.resolve_db_path("brainstorm.db")


def _conn(path=None):
    conn = sqlite3.connect(path or _ledger_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            situation TEXT NOT NULL,
            game TEXT NOT NULL,
            game_source TEXT NOT NULL,
            atoms_applied TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            falsifier TEXT NOT NULL,
            prediction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            review_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            outcome TEXT,
            hit TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


_COLS = ["id", "situation", "game", "game_source", "atoms_applied",
         "recommendation", "falsifier", "prediction", "confidence", "review_by",
         "status", "outcome", "hit", "created_at", "updated_at"]


def _get(conn, did):
    r = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM decisions WHERE id=?", (did,)).fetchone()
    if r is None:
        return None
    rec = dict(zip(_COLS, r))
    rec["atoms_applied"] = json.loads(rec["atoms_applied"])
    return rec


def get_decision(path, did):
    conn = _conn(path)
    rec = _get(conn, did)
    conn.close()
    return rec


def list_decisions(path=None, status=None):
    conn = _conn(path)
    q = f"SELECT {', '.join(_COLS)} FROM decisions"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY created_at DESC"
    rows = []
    for r in conn.execute(q, args).fetchall():
        rec = dict(zip(_COLS, r))
        rec["atoms_applied"] = json.loads(rec["atoms_applied"])
        rows.append(rec)
    conn.close()
    return rows


def journal_decision(path=None, *, situation, game, game_source, atoms_applied,
                     recommendation, falsifier, prediction, confidence, review_by):
    """Validated insert. Any invalid field -> ValidationError, no row written."""
    for name, val in [("situation", situation), ("game", game),
                      ("recommendation", recommendation),
                      ("falsifier", falsifier), ("prediction", prediction)]:
        if not (isinstance(val, str) and val.strip()):
            raise ValidationError(f"{name} must be a non-empty string")
    if game_source not in ("atoms", "model-knowledge"):
        raise ValidationError("game_source must be 'atoms' or 'model-knowledge'")
    if not isinstance(atoms_applied, list) or \
            not all(isinstance(a, str) and a.strip() for a in atoms_applied):
        raise ValidationError("atoms_applied must be a list of atom-id strings")
    if game_source == "atoms" and not atoms_applied:
        raise ValidationError("game_source='atoms' requires at least one atom id")
    # bool is an int subclass in Python; confidence=True must not pass as 1
    if isinstance(confidence, bool) or not isinstance(confidence, int) \
            or not (0 <= confidence <= 100):
        raise ValidationError("confidence must be an integer 0-100")
    try:
        date.fromisoformat(review_by)
    except (TypeError, ValueError):
        raise ValidationError("review_by must be an ISO date (YYYY-MM-DD)")

    conn = _conn(path)
    now = _now()
    cur = conn.execute(
        """INSERT INTO decisions
           (situation, game, game_source, atoms_applied, recommendation,
            falsifier, prediction, confidence, review_by, status,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'open',?,?)""",
        (situation, game, game_source, json.dumps(atoms_applied),
         recommendation, falsifier, prediction, confidence, review_by,
         now, now))
    conn.commit()
    rec = _get(conn, cur.lastrowid)
    conn.close()
    return rec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add decisions.py tests/test_decisions.py
git commit -m "feat: decision ledger with fail-closed journal_decision"
```

---

### Task 2: Ledger — `list_due_decisions`

**Files:**
- Modify: `decisions.py` (append function)
- Modify: `tests/test_decisions.py` (append test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decisions.py` (before the `__main__` block):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: the 3 new tests FAIL with `AttributeError: module 'decisions' has no attribute 'list_due_decisions'` (test 2 fails on missing `score_decision` — implemented in Task 3; that one test stays red until then)

- [ ] **Step 3: Write the implementation**

Append to `decisions.py`:

```python
def list_due_decisions(path=None, today=None):
    """Open decisions whose review_by date has arrived. 'Due' is computed,
    never stored. `today` is injectable for tests; defaults to the real date."""
    today = today or date.today().isoformat()
    return [d for d in list_decisions(path, status="open")
            if d["review_by"] <= today]
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: 9 passed, 1 failed (`test_scored_decisions_are_not_due` — needs Task 3's `score_decision`)

- [ ] **Step 5: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add decisions.py tests/test_decisions.py
git commit -m "feat: list_due_decisions (computed due state)"
```

---

### Task 3: Ledger — `score_decision` (close the loop, refuse double-scoring)

**Files:**
- Modify: `decisions.py` (append function)
- Modify: `tests/test_decisions.py` (append test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decisions.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: the 4 new tests + `test_scored_decisions_are_not_due` FAIL with `AttributeError: ... no attribute 'score_decision'`

- [ ] **Step 3: Write the implementation**

Append to `decisions.py`:

```python
def score_decision(path, did, *, outcome, hit):
    """Close a decision with its real-world result. Refuses to score twice —
    the first prediction record is the calibration data; overwriting it would
    let hindsight rewrite history."""
    if not (isinstance(outcome, str) and outcome.strip()):
        raise ValidationError("outcome must be a non-empty string")
    if hit not in ("yes", "no", "partial"):
        raise ValidationError("hit must be 'yes', 'no', or 'partial'")
    conn = _conn(path)
    try:
        rec = _get(conn, did)
        if rec is None:
            raise ValidationError(f"no decision with id {did}")
        if rec["status"] == "scored":
            raise ValidationError(f"decision {did} is already scored")
        conn.execute(
            "UPDATE decisions SET status='scored', outcome=?, hit=?, updated_at=? "
            "WHERE id=?", (outcome, hit, _now(), did))
        conn.commit()
        return _get(conn, did)
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: 14 passed (all — including Task 2's previously-red test)

- [ ] **Step 5: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add decisions.py tests/test_decisions.py
git commit -m "feat: score_decision closes the calibration loop, refuses double-scoring"
```

---

### Task 4: MCP server wiring (3 tools)

**Files:**
- Modify: `mcp_server.py` — two places: the `tools/list` array (add 3 schema dicts after the `brainstorm` entry, near line 737) and the dispatch `elif` chain (add 3 branches after the `brainstorm` branch, near line 961).

The server is a hand-rolled JSON-RPC loop; there is no unit-test harness for it. The ledger logic is fully tested in Tasks 1-3; this task is thin wiring, verified by a smoke test.

- [ ] **Step 1: Add the 3 tool schemas to `tools/list`**

Insert into the `"tools": [...]` array (same level as the existing `brainstorm` entry):

```python
                        {
                            "name": "journal_decision",
                            "description": "Journal a decision with a scorable prediction into the decision ledger. Fail-closed: rejects records missing prediction, confidence, or review_by. Call this as the final, mandatory step of the /decide pipeline.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "situation": {"type": "string", "description": "The decision, one line"},
                                    "game": {"type": "string", "description": "Named game/structure (e.g. 'lemons market', 'iterated cooperation')"},
                                    "game_source": {"type": "string", "enum": ["atoms", "model-knowledge"], "description": "'atoms' when trigger conditions from the corpus matched; 'model-knowledge' when classified from general knowledge"},
                                    "atoms_applied": {"type": "array", "items": {"type": "string"}, "description": "Atom IDs applied (e.g. ['coop-01']). Required non-empty when game_source='atoms'."},
                                    "recommendation": {"type": "string", "description": "The recommended move"},
                                    "falsifier": {"type": "string", "description": "What evidence would prove this framing wrong"},
                                    "prediction": {"type": "string", "description": "What the user expects to happen"},
                                    "confidence": {"type": "integer", "description": "0-100"},
                                    "review_by": {"type": "string", "description": "ISO date (YYYY-MM-DD) when the prediction gets scored"}
                                },
                                "required": ["situation", "game", "game_source", "atoms_applied", "recommendation", "falsifier", "prediction", "confidence", "review_by"]
                            }
                        },
                        {
                            "name": "list_due_decisions",
                            "description": "List open decisions whose review_by date has arrived — predictions waiting to be scored.",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "score_decision",
                            "description": "Close a journaled decision with its real-world outcome. Refuses to score twice.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer", "description": "Decision id from journal_decision / list_due_decisions"},
                                    "outcome": {"type": "string", "description": "What actually happened"},
                                    "hit": {"type": "string", "enum": ["yes", "no", "partial"], "description": "Did the prediction hold?"}
                                },
                                "required": ["id", "outcome", "hit"]
                            }
                        },
```

- [ ] **Step 2: Add the 3 dispatch branches**

Insert after the existing `elif tool_name == "brainstorm":` branch, matching its style (errors returned as JSON text, never raised out of the loop):

```python
                    elif tool_name == "journal_decision":
                        import decisions
                        try:
                            rec = decisions.journal_decision(
                                None,
                                situation=arguments.get("situation"),
                                game=arguments.get("game"),
                                game_source=arguments.get("game_source"),
                                atoms_applied=arguments.get("atoms_applied") or [],
                                recommendation=arguments.get("recommendation"),
                                falsifier=arguments.get("falsifier"),
                                prediction=arguments.get("prediction"),
                                confidence=arguments.get("confidence"),
                                review_by=arguments.get("review_by"),
                            )
                            text_result = json.dumps(rec, indent=2)
                        except decisions.ValidationError as e:
                            text_result = json.dumps({"error": str(e)})
                        resp["result"] = {"content": [{"type": "text", "text": text_result}]}
                    elif tool_name == "list_due_decisions":
                        import decisions
                        text_result = json.dumps(decisions.list_due_decisions(None), indent=2)
                        resp["result"] = {"content": [{"type": "text", "text": text_result}]}
                    elif tool_name == "score_decision":
                        import decisions
                        try:
                            rec = decisions.score_decision(
                                None, arguments.get("id"),
                                outcome=arguments.get("outcome"),
                                hit=arguments.get("hit"))
                            text_result = json.dumps(rec, indent=2)
                        except decisions.ValidationError as e:
                            text_result = json.dumps({"error": str(e)})
                        resp["result"] = {"content": [{"type": "text", "text": text_result}]}
```

- [ ] **Step 3: Smoke-test the wiring**

Run:
```bash
cd /Users/aniketnamjoshi/knowledge-project
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n' \
  | python mcp_server.py 2>/dev/null \
  | grep -o "journal_decision\|list_due_decisions\|score_decision" | sort -u
```
Expected output (all three names, one per line — the JSON may be a single line, so count occurrences, not lines):
```
journal_decision
list_due_decisions
score_decision
``` Also run the full suite to confirm nothing broke: `python -m pytest tests/ -q` — expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add mcp_server.py
git commit -m "feat: expose journal_decision, list_due_decisions, score_decision via MCP"
```

---

### Task 5: Session-start hook — inject due decisions

**Files:**
- Modify: `hooks/psyche_session_start.py` — inside `open_loops()`, add a third try-block after the experiments block; bump `cap=400` to `cap=600` (three sections now share the budget).
- Modify: `tests/test_decisions.py` (append test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_decisions.py`:

```python
class TestHookInjection(LedgerBase):
    def test_due_decision_appears_in_open_loops(self):
        decisions.journal_decision(self.path, **_valid(review_by="2020-01-01"))
        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
        sys.path.insert(0, hooks_dir)
        try:
            import psyche_session_start as ss
            out = ss.open_loops(ledger_path=self.path)
        finally:
            sys.path.remove(hooks_dir)
        self.assertIn("Decisions due for scoring", out)
        self.assertIn("TidyMyData prospect", out)

    def test_no_due_decisions_no_section(self):
        decisions.journal_decision(self.path, **_valid(review_by="2099-01-01"))
        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
        sys.path.insert(0, hooks_dir)
        try:
            import psyche_session_start as ss
            out = ss.open_loops(ledger_path=self.path)
        finally:
            sys.path.remove(hooks_dir)
        self.assertNotIn("Decisions due for scoring", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: the 2 new tests FAIL (`assertIn` — the section is never emitted)

- [ ] **Step 3: Modify the hook**

In `hooks/psyche_session_start.py`, change the signature default `cap=400` to `cap=600`, and insert this block inside `open_loops()` after the experiments try-block, before `return`:

```python
    try:
        import decisions
        due = decisions.list_due_decisions(ledger_path)
        if due:
            lines.append(f"Decisions due for scoring ({len(due)}):")
            for r in due[:2]:
                lines.append(f"- [#{r['id']}] {r['situation'][:100]} | predicted: "
                             f"{r['prediction'][:80]} (conf {r['confidence']}, "
                             f"due {r['review_by']})")
    except Exception:
        pass
```

Note: `decisions` imports cleanly here for the same reason `brainstorm` does — the hook process runs with the repo on sys.path (`import brainstorm` already works two blocks up). The never-raises try/except convention is preserved.

- [ ] **Step 4: Run tests**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/test_decisions.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add hooks/psyche_session_start.py tests/test_decisions.py
git commit -m "feat: session-start hook surfaces due decisions for scoring"
```

---

### Task 6: The two skills (/decide and /ideas)

**Files:**
- Create: `skills/decide/SKILL.md` (repo canonical copy)
- Create: `skills/ideas/SKILL.md` (repo canonical copy)
- Install: copy both to `~/.claude/skills/`

No unit tests — these are prompt artifacts; the acceptance test is Task 7.

- [ ] **Step 1: Create `skills/decide/SKILL.md`**

```markdown
---
name: decide
description: Use when the user invokes /decide or asks to run a decision through the decision framework. Classifies which game the situation is, retrieves atom decision rules, recommends a move with a falsifier, and journals a scorable prediction via Psyche. Also use to score decisions flagged as due at session start.
---

# /decide — deterministic decision pipeline

Run EVERY step in order. The run is NOT complete until step 6's tool call
succeeds — a verdict without a journaled prediction is a failed run.

1. **Restate** the decision in one line. If the prompt contains no concrete
   decision, ask for it and stop.
2. **Retrieve** relevant atoms: call `mcp__psyche__search_knowledge` with the
   decision text (topic: naval) and `mcp__psyche__search_memories` with the
   same query.
3. **Classify the game.** Name the game/structure and cite which atom trigger
   conditions matched, by ID (e.g. coop-01, persf-06). If no atom matched and
   the classification comes from general knowledge, set game_source to
   `model-knowledge` and say so explicitly — NEVER present an unmatched
   classification as atom-grounded.
4. **Recommend the move** and state the **falsifier**: what evidence would
   prove this framing wrong.
5. **Elicit the prediction:** ask the user what they expect to happen, their
   confidence (0-100), and agree a review_by date (default: 14 days out).
6. **HARD GATE:** call `mcp__psyche__journal_decision` with all fields, then
   display the returned record verbatim as the receipt. If validation fails,
   fix the fields and retry. Do not end the run without a saved record.

## Scoring due decisions

When session start shows "Decisions due for scoring", offer to score each:
ask what actually happened, then call `mcp__psyche__score_decision` with
id, outcome, and hit (yes / no / partial). Never overwrite a scored decision.
```

- [ ] **Step 2: Create `skills/ideas/SKILL.md`**

```markdown
---
name: ideas
description: Use when the user invokes /ideas or asks for idea collisions, cross-domain hypotheses, or knowledge-gap exploration. Explicit deterministic trigger for Psyche's existing brainstorm/collision engine and hypothesis ledger.
---

# /ideas — idea-collision engine, explicit trigger

The server-side pipeline already exists and is deterministic (seeded). This
skill's only job is to guarantee it actually runs when asked.

1. Parse intent from the prompt:
   - Default → collisions: call `mcp__psyche__brainstorm` (pass `topics` if
     the user named domains, `count` if they named a number; otherwise
     defaults).
   - "gaps", "what's disconnected" → call `mcp__psyche__report_gaps` instead.
   - "hypotheses", "what's in flight" → call `mcp__psyche__list_hypotheses`.
2. Present what came back: each collision pair / hypothesis in one short
   block — the two snippets, why they might connect, and a kill test.
3. **Keep or drop:** for any pair worth keeping, call
   `mcp__psyche__update_hypothesis` with text + kill_test so it enters the
   ledger instead of evaporating in chat. Say which were dropped and why.
4. If the user combined this with /decide in one prompt, run /decide's
   pipeline separately — the two skills compose in the prompt, never couple
   in code.
```

- [ ] **Step 3: Install both skills**

```bash
mkdir -p ~/.claude/skills/decide ~/.claude/skills/ideas
cp /Users/aniketnamjoshi/knowledge-project/skills/decide/SKILL.md ~/.claude/skills/decide/SKILL.md
cp /Users/aniketnamjoshi/knowledge-project/skills/ideas/SKILL.md ~/.claude/skills/ideas/SKILL.md
```

- [ ] **Step 4: Verify installation**

Run: `ls ~/.claude/skills/decide/SKILL.md ~/.claude/skills/ideas/SKILL.md`
Expected: both paths print (no "No such file")

- [ ] **Step 5: Commit**

```bash
cd /Users/aniketnamjoshi/knowledge-project
git add skills/
git commit -m "feat: /decide and /ideas skills (repo canonical copies)"
```

---

### Task 7: Full suite + acceptance

- [ ] **Step 1: Run the entire test suite**

Run: `cd /Users/aniketnamjoshi/knowledge-project && python -m pytest tests/ -q`
Expected: all tests pass (existing suites + 16 new)

- [ ] **Step 2: Acceptance — one real decision through the loop**

In a NEW Claude Code session (so the restarted MCP server picks up the new
tools): invoke `/decide` with a real live decision. Verify:
1. The skill cites atom IDs or labels the classification `model-knowledge`.
2. The journaled record is displayed as a receipt.
3. `mcp__psyche__list_due_decisions` returns `[]` (nothing due yet).

The loop fully closes only at the review date — note the review_by date and
expect the session-start injection then.

- [ ] **Step 3: Report back**

Present the acceptance evidence to the user before any merge decision
(approval-gate preference). Branch stays `feat/decide-pipeline`; merging is
the user's call.
```

# /decide — deterministic decision pipeline (v1 design)

Date: 2026-07-08
Status: approved 2026-07-08 (with /ideas addition)

## Problem

Psyche's judgment tools (brainstorm, collision, naval retrieval) are deterministic
Python inside, but their *invocation* depends on the model choosing to call them —
so they sometimes run and sometimes don't. For a decision framework this is fatal:
a skipped journal write or a forgotten review is a silent failure, and silent
failures are how the system quietly stops learning.

Design rule adopted: **anything that must happen every time lives in code (tool,
hook, hard gate); the model only does judgment inside fixed stages** — the
"workflows vs agents" split. Determinism lives in the process; outcomes stay
probabilistic and compounding closes the gap.

## Requirements (from the 2026-07-08 interview)

1. **Trigger:** explicit command only (`/decide <situation>`). Auto-detection is
   a possible later phase, not v1.
2. **One run produces:** game classification + applicable atom rules + recommended
   move + falsifier + **a journaled prediction with confidence and a review date**.
3. **Corpus:** runs against the existing foundational/canonical atoms now
   (Axelrod, Cialdini, Munger, Taleb, Popper, Deutsch, Naval — 16 foundational
   sources). The games map (Dixit & Nalebuff, Schelling, Akerlof) is a separate
   ingestion track; the pipeline does not wait for it.
4. **Pipeline before corpus:** build now so the journal accumulates real decisions
   immediately; the pipeline is the forcing function that keeps reading applied.

## Architecture: hybrid (approach C)

Chosen over A (pure skill — journal write rides on model obedience, the exact bug
we're fixing) and B (full server pipeline — maximal determinism but moves
classification into a weaker server-side model and needs the most new code).

Split by the silent-failure test: *would I notice if this step got skipped?*
- Loud failures (bad classification, weak recommendation) → model-side skill.
- Silent failures (missed write, forgotten review) → server-side code.

## Components

### 1. Decisions ledger (SQLite)

Same storage pattern as the existing hypothesis ledger in `brainstorm.py`
(sqlite3 at `_ledger_path()`); the decisions table lives in the same database —
one file, one backup path. Rejected: a separate JSONL file (second convention,
nothing gained).

Fields:

| field | type | notes |
|---|---|---|
| id | integer pk | |
| created_at | text (ISO) | |
| situation | text | the decision as stated |
| game | text | named game/structure |
| game_source | text | `atoms` (trigger-matched) or `model-knowledge` (unsourced, labeled) |
| atoms_applied | text (JSON list) | atom IDs, e.g. `["coop-01","persf-06"]` |
| recommendation | text | the move |
| falsifier | text | what evidence would kill this framing |
| prediction | text | what the user expects to happen |
| confidence | integer 0–100 | |
| review_by | text (ISO date) | |
| status | text | `open` → `scored` (`due` is computed by list_due_decisions — open AND review_by passed — not stored, so no background job is needed to flip states) |
| outcome | text nullable | filled at scoring |
| hit | text nullable | `yes` / `no` / `partial` |

### 2. Three MCP tools (mcp_server.py, fail-closed)

- **journal_decision** — validated insert. Rejects records missing prediction,
  confidence, or review_by: an entry without a scorable prediction defeats the
  journal's purpose.
- **list_due_decisions** — open decisions whose review_by has passed.
- **score_decision(id, outcome, hit)** — closes a record; refuses to score twice.

No chat-model dependency in any of the three (pure storage/retrieval), so
`NoChatModelError` cannot affect this pipeline.

### 3. /decide skill (model-side judgment)

Fixed numbered steps:
1. Restate the decision in one line.
2. Retrieve relevant atoms (existing search tools, naval topic).
3. Classify the game, citing which atom trigger conditions matched. Any
   classification from general knowledge (no atom matched) is explicitly labeled
   `model-knowledge` — the corpus's invention-prevention rule, preserved.
4. Recommend the move, state the falsifier.
5. Ask the user for their prediction and confidence.
6. **Hard gate: call `journal_decision` and show the saved record.** The skill is
   not done until the tool call succeeds.

### 4. /ideas skill (idea collision, explicit trigger)

The collision engine needs no new server code: `brainstorm_tool(count, drift,
topics, seed)` in `brainstorm.py` is already a deterministic pipeline, and the
hypothesis ledger already handles lifecycle (new → researching → testing →
killed/survived). What it lacked was a guaranteed trigger — invocation depended
on the model choosing to call it. Fix: a thin `/ideas` skill whose only job is
to ALWAYS call the existing tools:

1. Parse optional args (topic focus, count) from the prompt; call `brainstorm`.
2. Present the collision pairs / hypotheses returned.
3. For any pair worth keeping, call `update_hypothesis` to write it up
   (text + kill_test) so it enters the ledger rather than evaporating in chat.
4. If the user asked for gaps, call `report_gaps` instead.

Composability: `/decide` and `/ideas` are both skills, so one prompt can invoke
both ("/decide … and /ideas on the losing options"). No coupling is built
between them — composition happens in the prompt, not the code. Rejected: a
combined mega-command (couples two pipelines that change at different speeds).

### 5. Recall loop (hook)

`hooks/psyche_session_start.py` already fires deterministically every session.
It gains one check: call `list_due_decisions`; if any, inject "decision #N due
for scoring" into session start. Scoring happens *to* the user rather than
depending on anyone remembering.

## Flow

```
user: /decide <situation>
  → skill: retrieve atoms → classify (cite atoms or label model-knowledge)
  → skill: recommendation + falsifier
  → user: prediction + confidence
  → tool:  journal_decision (validated write)          [deterministic]
...time passes...
session start → hook: list_due_decisions → inject due  [deterministic]
  → user + model: score_decision(outcome, hit)          [closes the loop]
```

## Error handling

- All three tools validate input and fail closed (reject, never partially write).
- journal_decision returns the full saved record so the skill can display it —
  a human-visible receipt that the write happened.
- score_decision on an already-scored id returns an error, not an overwrite.

## Testing (TDD, failing tests first)

- Ledger round-trip: journal → read back identical.
- Rejection: missing prediction/confidence/review_by → error, no row.
- Double-score refusal.
- Due-date logic: open + past review_by appears in list_due_decisions; scored
  does not.
- Hook injection: due decision present → session-start context contains it.
- Acceptance: one real decision through the full loop (journal today, score at
  review).

## Out of scope for v1 (rejected for now, with reasons)

- **Auto-detect trigger** — revisit only after the explicit command proves itself.
- **Games foundational map** — separate Tier-1 ingestion track (Art of Strategy,
  Strategy of Conflict, Akerlof's lemons paper); classification quality improves
  when it lands, pipeline doesn't wait.
- **Calibration analytics** (hit rates by framework, confidence calibration) —
  needs scored data to exist first.
- **Cockpit integration** — different system, different trigger surface.

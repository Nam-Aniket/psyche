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

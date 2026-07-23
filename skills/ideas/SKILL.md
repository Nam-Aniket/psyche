---
name: ideas
description: Use when the user invokes /ideas or asks for idea collisions, cross-domain hypotheses, or knowledge-gap exploration. Explicit deterministic trigger for Psyche's existing brainstorm/collision engine and hypothesis ledger.
---

# /ideas — idea-collision engine, explicit trigger

The server-side pipeline already exists and is deterministic (seeded). This
skill's only job is to guarantee it actually runs when asked.

1. Parse intent from the prompt:
   - Default → collisions: call `mcp__psyche__brainstorm` (pass `seed` if the
     user named a topic/problem to anchor on, `topics` if they named domains,
     `count` if they named a number; otherwise defaults).
   - "gaps", "what's disconnected" → call `mcp__psyche__report_gaps` instead.
   - "hypotheses", "what's in flight" → call `mcp__psyche__list_hypotheses`.
2. Present what came back: each collision pair / hypothesis in one short
   block — the two snippets, why they might connect, and a kill test.
3. **Keep or drop:** for any pair worth keeping (or any item marked
   `needs_hypothesis`), call `mcp__psyche__update_hypothesis` with text +
   kill_test so it enters the ledger instead of evaporating in chat. Say
   which were dropped and why.
4. If the user combined this with /decide in one prompt, run /decide's
   pipeline separately — the two skills compose in the prompt, never couple
   in code.

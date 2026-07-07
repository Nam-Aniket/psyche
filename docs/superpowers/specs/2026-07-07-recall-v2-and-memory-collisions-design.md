# Recall v2 (freshness, time sense, open loops) + memories in the collision pool

Date: 2026-07-07
Status: approved by Aniket (chat, 2026-07-07; split-block chosen for freshness)
Scope: hooks/psyche_session_start.py, hooks/psyche_prompt_submit.py, memzero.py,
brainstorm.py, mcp_server.py (tool param), tests. No breaking schema changes.

## 1. Why

Three verified gaps in memory recall (see chat review of memzero.py, 2026-07-07):

1. Session start injects the 12 *oldest* standing facts (`standing_fact_rows`,
   stable=True, id ASC): deliberate for prompt-cache stability, but past 12 standing
   facts, new decisions never appear at session start.
2. Recency is only a tiebreak in `search_memories`, so temporal questions ("what did
   we do recently?") rank on pure relevance and can surface stale facts.
3. Per-prompt retrieval uses the lone prompt text (skipped entirely under 30 chars),
   and nothing surfaces open loops (hypotheses in researching/testing, active
   experiments).

Plus one confirmed miss in the brainstorm layer: `build_pool` reads only chunk
embeddings; atomic memories are invisible to idea collision.

## 2. Freshness: split standing block (chosen over auto-curation)

`standing_fact_rows` gains a split mode used by the SessionStart hook:

- Slots 1-8: unchanged semantics, oldest-first (id ASC), byte-stable across sessions
  (prompt-cache-friendly prefix).
- Slots 9-12: "recent" tail, newest-first by updated_at, excluding ids already in
  the stable slots. Allowed to change between sessions; sits at the END of the
  injected block so earlier bytes still cache.
- Constants STABLE_SLOTS = 8, RECENT_SLOTS = 4 next to the existing caps.

Rejected: auto-curation LLM pass (new failure mode; edits decision history on its
own judgment). `psyche mem compact` remains the manual curation seam.

## 3. Time sense: temporal-intent re-rank (chosen over global recency decay)

In the UserPromptSubmit path, if the prompt matches temporal wording
(TEMPORAL_RE: recent(ly), yesterday, today, last week/session/time, latest, newest,
"what did we do", "what have we"), re-rank the retrieved candidate set by
updated_at DESC before the top-6 cut. Non-temporal prompts are byte-for-byte
unchanged. Rejected: always-on recency decay in RRF scoring (regresses ordinary
queries to fix a minority).

## 4. Context fallback + open loops

- **Context fallback (prompt hook):** when the prompt is under 30 chars OR the
  search returns nothing, read the last 2 user + 2 assistant messages from
  `transcript_path` (payload already carries it), build "context: <snippets>
  question: <prompt>" and retry the search once. Normal prompts keep today's
  single-query path. Transcript read failures degrade silently to current behavior.
- **Open loops (session-start hook):** after the standing block, inject a compact
  tail (cap 400 chars): count + newest two one-line titles of hypotheses in
  researching/testing (brainstorm.db), and active experiments (experiments table,
  status running/active). Placed last for cache reasons. Emits nothing when there
  are no open loops.

## 5. Memories join the collision pool

`build_pool(kept, include_memories=True)` appends live atomic memories
(`superseded_by IS NULL`) from knowledge.db as pseudo-topic `__memory__`:
index entries {topic: "__memory__", chunk_id: memory_id, source: category}.

Guards:

- **Length floor 60 chars** for memories (MIN_MEMORY_CHARS), prose check skipped
  (a memory is one sentence by design; the 200-char chunk floor would reject most).
- **No memory x memory pairs** in v1: if the anchor is a memory, partner search
  excludes `__memory__`; two one-line facts are too thin to bridge.
- **Dimension check** against the pool's majority embedding signature, same rule as
  topic DBs (compare stored blob dim; drop + report on mismatch).

Text fetch routes `__memory__` to `memzero.get_memory(id)["fact"]`. Pair dedup,
drift bands, seeded mode, and the approved bandit (spec 2026-07-07-brainstorm-
feedback-loop) compose unchanged; `__memory__ x <topic>` is just another arm.
`brainstorm` MCP tool + CLI gain `include_memories` (default ON).

## 6. Packaging (3 PRs, merged in order)

1. **PR1** brainstorm defect fixes + feedback loop (already-approved spec).
2. **PR2** recall v2 (sections 2-4 here).
3. **PR3** memories-in-pool (section 5), stacked on PR1 (same functions).

## 7. Testing (pytest, synthetic, no LLM calls)

- split block: >12 standing facts -> newest decision appears in tail; stable slots
  byte-identical across two calls; tail excluded ids not duplicated
- temporal re-rank: same candidates, temporal prompt -> newest first; non-temporal
  prompt -> ranking unchanged
- context fallback: short prompt + stub transcript -> search called with combined
  query; unreadable transcript -> silent no-op
- open loops: seeded brainstorm.db + experiments rows -> tail rendered under cap;
  empty -> no output
- memory pool: memory rows appear with `__memory__` tag; 60-char floor enforced;
  memory anchor never pairs with memory partner; dim mismatch dropped + reported
- acceptance: manual smoke run on the real corpus (session start shows recent
  decisions + open loops; brainstorm returns at least one memory-anchored pair)

## 8. Success criterion

A new session's opening context contains at least one decision made in the last
48 hours and any open hypotheses; "what did we do recently on psyche" surfaces
current-week facts first; a real brainstorm run produces memory x book hypotheses
Aniket judges worth engaging (feeding the PR1 bandit its reward signal).

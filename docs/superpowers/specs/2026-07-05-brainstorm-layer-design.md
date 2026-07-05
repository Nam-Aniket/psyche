# Psyche Brainstorm Layer — Design Spec

**Date:** 2026-07-05
**Status:** Approved design, pending spec review
**Author:** Aniket + Claude (Opus 4.8)
**Branch:** `feature/brainstorm-layer`

---

## 1. Purpose (plain English first)

Psyche today is a **retrieval** system: you ask a question, it finds the notes
that are closest in meaning and hands them back. This feature points the same
machinery in the opposite direction. Instead of finding notes that are *close*
to your question, it deliberately pulls together two notes that are *far apart
but not unrelated* and asks an LLM to invent a testable idea that bridges them.

That is the whole trick behind GBrain's "Brainstorm with LSD" (lateral synaptic
drift, Garry Tan's term). The novelty does not come from the model being clever
— it comes from **your corpus being unique**. Run collisions on the open web and
you get what everyone gets; run them on your private observations and the
collisions literally cannot occur in anyone else's system.

This spec is inspired by GBrain (open-sourced by Garry Tan, April 2026, MIT,
`github.com/garrytan/gbrain` — verified this session). It does **not** copy
GBrain's code; it re-implements the two idea-generation ideas on top of Psyche's
existing retrieval stack.

### What "idea generation" actually is here

Two halves, matching GBrain:

1. **Collision engine** — sample note pairs at a controlled semantic distance,
   force a *falsifiable hypothesis* (a claim that could be proven wrong) plus a
   *cheap kill-test* out of each pair.
2. **Gap reporter** — show which regions of the corpus never connect, so the
   empty space becomes a list of things worth deliberately brainstorming or
   researching. In a rich corpus, a gap is a candidate opportunity.

### The discipline this serves (non-negotiable)

Per Aniket's three-node discovery loop (Psyche memory #2282): **every output of
this engine is a hypothesis, not an idea.** It is not validated until it has
touched reality once (a scraped dataset, ten cold emails, a prototype). The
metric this feature optimizes is **cost per killed hypothesis**, not hypotheses
generated. The lifecycle tracking (Section 4) exists specifically to make that
metric queryable. The engine widens the top of the funnel; reality is still the
bottom.

---

## 2. Hard constraints (from Aniket, this session)

These are requirements, not preferences:

- **C1 — Purely additive. Nothing in current Psyche functionality changes.**
  No edits to the retrieval path, the memory tools, ingestion, or the graph
  build. New module, new table, new tool registrations only. If any existing
  behavior would change, the design is wrong.
- **C2 — MCP-first, and that is enough for v1.** The value comes *because* an
  LLM is calling it: the calling model (Claude, GPT, etc.) can immediately
  distill a returned hypothesis and research it in real time. The engine
  produces the raw collision; the calling LLM refines and reality-checks it.
  No standalone reasoning model is needed inside Psyche.
- **C3 — Personal-grade v1.** Built for Aniket's own discovery loop first, not
  as a polished multi-user release. The "Psyche does what GBrain does,
  local-first" positioning story comes *after* the mechanism is proven on his
  corpus.
- **C4 — Web UI is explicitly future work, not v1.** A button/screen for idea
  generation in the Psyche web app is desirable later (see Section 9). v1 ships
  MCP tools + CLI only. The module functions are designed as a clean seam the
  future web layer can call without change.

---

## 3. Architecture

One new file at repo root, following the existing flat-module convention
(`build_graph.py`, `synthesis.py`, `query.py`):

```
brainstorm.py          # all new logic lives here
```

It imports **only existing helpers** — it adds capability, it does not fork the
stack:

| Needs | Reuses (existing) |
|---|---|
| chunk ids + embedding matrix | `db.get_all_embeddings_only`, `db.get_chunks_by_ids` |
| cosine similarity (vectorized) | `query.calculate_similarities_vectorized` |
| clustering for gaps | `build_graph.kmeans` |
| chat + embedding calls | `llm_client.LLMClient` (`.get_embedding`, chat method) |
| typed edges for isolated-concept bonus | existing `concept_links` table |

Wiring points (all additive):

- `db.py` — add one `CREATE TABLE IF NOT EXISTS hypotheses` in the existing
  schema-init function. No `ALTER` on any existing table.
- `mcp_server.py` — register 4 new tools alongside the current ones, same
  pattern as `search_knowledge_tool`.
- `cli.py` — add `brainstorm` and `gaps` subcommands to the dispatcher and the
  usage string.

**Confirmation of C1:** the retrieval code path (`perform_hybrid_search` and
everything it calls) is not touched. `brainstorm.py` is a read-only consumer of
the embeddings it already produces, plus a writer to its own new table.

---

## 4. Data model

One new table. Same `embedding_blob` BLOB format as the existing `embeddings`
table, so dedup reuses the exact serialization already in `db.py`.

```sql
CREATE TABLE IF NOT EXISTS hypotheses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,          -- the falsifiable hypothesis
    kill_test     TEXT,                   -- cheapest suggested reality test
    chunk_a       INTEGER,                -- provenance: first collided chunk
    chunk_b       INTEGER,                -- provenance: second collided chunk
    drift         REAL,                   -- drift knob value that produced it
    embedding_blob BLOB,                  -- for cross-run dedup
    status        TEXT NOT NULL DEFAULT 'new',
    notes         TEXT,                   -- freeform: research findings, why killed
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (chunk_a) REFERENCES chunks (id) ON DELETE SET NULL,
    FOREIGN KEY (chunk_b) REFERENCES chunks (id) ON DELETE SET NULL
);
```

**Status lifecycle** (the discovery loop, made queryable):

```
new  →  researching  →  testing  →  killed
                                 ↘  survived
```

- `new` — generated, not yet looked at
- `researching` — Claude/you are doing the kill-research (node 2 of the loop)
- `testing` — survived research, now getting a cheap reality test (node 3)
- `killed` — reality (or research) said no. **Kept forever** — this is the
  engine's negative memory; killed rows still block near-duplicates from being
  re-pitched.
- `survived` — passed a reality test. A real candidate.

Scoreboard becomes one query, e.g. killed-per-week or
`count(killed) / elapsed` = cost per killed hypothesis.

`chunk_*` use `ON DELETE SET NULL` (not CASCADE) deliberately: if a source is
re-ingested and old chunks disappear, the hypothesis and its kill-verdict must
survive — losing the provenance link is acceptable, losing the negative memory
is not.

---

## 5. Collision engine

Function: `generate_hypotheses(db_path, count=5, drift=0.5, topic=None, llm=...)`

Step by step:

1. **Load** chunk ids + embeddings matrix via existing helpers. If corpus has
   **< 50 chunks**, refuse with a clear "corpus too sparse to collide —
   ingest more first" message. (Collisions on a tiny corpus are just noise.)
2. **Pick anchors.** Sample random anchor chunks. Skip chunks under ~200 chars
   (fragments make bad collision fuel). If `topic` is given, restrict anchors to
   that topic DB (Psyche is already multi-topic).
3. **Find a partner inside the drift band.** Vectorized cosine of the anchor
   against all chunks, then keep candidates whose similarity falls in:

   ```
   band = [0.60 - 0.45*drift,  0.75 - 0.45*drift]
   ```

   - drift 0.0 → band 0.60–0.75  (mild, "these are clearly related")
   - drift 0.5 → band 0.375–0.525 (the sweet spot, default)
   - drift 1.0 → band 0.15–0.30  (wild, "how are these even connected")

   **Prefer a partner with a different `source_id`** than the anchor — the
   whole point is "sci-fi concept × TidyMyData call note", not two paragraphs of
   the same doc. If the band is empty, widen once by ±0.05; still empty → skip
   this anchor and try another.

   > Note: 0.45 is a starting coefficient chosen so the band stays inside a
   > sane similarity range across drift 0–1. It is a **calibration knob**, not a
   > law — first real runs may show the sweet spot sits elsewhere for Aniket's
   > embedding model (Psyche's embeddings are provider-configurable). The
   > implementation must keep the band coefficients as named constants at the
   > top of the module so they are trivial to tune. `# ponytail:` comment marks
   > them as the tuning point.

4. **Generate.** Send both chunk texts to the chat model with a prompt that
   **requires** JSON `{"hypothesis": "...", "kill_test": "..."}` where:
   - the hypothesis must be *falsifiable* — the prompt explicitly rejects
     "these two things relate interestingly" and demands a claim that could be
     shown false;
   - the kill_test is the single cheapest way to find out if it's wrong.
5. **Dedup before showing (cross-run).** Embed the hypothesis text. Cosine
   against **all** stored hypotheses including `killed`. If ≥ **0.85** to any,
   silently drop and resample another pair. Dead ideas never resurface.
6. **Store** survivors as `status='new'` with provenance + drift, return them to
   the caller with the two source snippets that collided (so the calling LLM has
   the raw material to distill/research immediately — satisfies C2).

Returns a list of `{id, hypothesis, kill_test, source_a, source_b, drift}`.

---

## 6. Gap reporter

Function: `report_gaps(db_path, topic=None, top=10, llm=...)`

1. Run existing `kmeans()` over the chunk embeddings **on demand** (clusters are
   not persisted anywhere today, and recomputing avoids stale clusters after new
   ingests — same "recompute the view, keep the raw data" logic as GBrain's
   compiled-truth model).
2. Label each cluster cheaply: the source names of its most central chunks +
   top keywords. (No LLM call needed for labels in v1; can upgrade later.)
3. Rank **cluster pairs** by *lowest* cross-cluster average similarity that also
   have **zero stored hypotheses bridging them** — i.e. regions of your thinking
   that have never been connected and that you have not already tried to
   connect. Return the top `top` as "these never touch."
4. **Bonus (cheap, uses existing typed edges):** list `concepts` rows that have
   no `concept_links` at all — isolated ideas floating unconnected in the graph.
5. Each gap is **directly actionable**: a returned gap names its two clusters,
   and a follow-up `brainstorm` call can use those two clusters as the sampling
   pools to force a collision across the gap.

Returns `{cluster_gaps: [...], isolated_concepts: [...]}`.

---

## 7. Surface (MCP + CLI, per C2/C4)

**MCP tools** (registered in `mcp_server.py`, same pattern as existing):

| Tool | Args | Does |
|---|---|---|
| `brainstorm` | `count?`, `drift?`, `topic?` | generate + store hypotheses, return them |
| `report_gaps` | `topic?`, `top?` | return corpus gaps |
| `list_hypotheses` | `status?` | read the ledger (filter by status) |
| `update_hypothesis` | `id`, `status`, `notes?` | move a hypothesis along the lifecycle |

The calling LLM is the reasoning engine (C2): `brainstorm` hands it raw
collisions, the LLM distills/researches, then calls `update_hypothesis` to
record the verdict. No reasoning model lives inside Psyche.

**CLI** (thin wrappers, same functions):

```
psyche brainstorm [--drift 0.5] [--count 5] [--topic NAME]
psyche gaps [--top 10] [--topic NAME]
```

---

## 8. Error handling

- **No chat model configured** (the `local`/`none` provider path in
  `llm_client.py` where `chat_model = "none"`): `brainstorm` returns a clear
  message that it needs a chat model — embeddings alone can find the pairs but
  cannot write the hypothesis. `report_gaps` still works (no chat model needed).
- **Malformed LLM JSON:** one retry with a stricter reminder; on second failure,
  skip that pair, continue the run, and note "1 pair skipped (bad output)" in
  the response. One bad collision never fails the whole run.
- **Sparse corpus** (< 50 chunks): refuse `brainstorm` with guidance; `gaps`
  needs at least ~2 viable clusters or it says "not enough material yet."
- **Empty drift band repeatedly:** after N anchor attempts with no partner,
  return however many hypotheses were made plus "corpus may be too
  homogeneous at this drift — try a lower drift."

---

## 9. Future work (named, explicitly out of v1 scope)

- **Web app idea-generation screen (C4).** A button/screen in the Psyche web app
  that calls `generate_hypotheses` / `report_gaps` and shows hypotheses as cards
  with lifecycle buttons (kill / survived / add note). The v1 module functions
  are the clean seam this will call — no rework expected, just a new caller.
- **Score-first candidate ranking** (Approach B from brainstorming): generate
  many pairs, rank cheaply, LLM only the top few. Revisit if v1 hit-rate is low.
- **Query-log gap analysis:** GBrain's other gap signal is "questions retrieval
  returned nothing for." Needs query logging Psyche doesn't have yet. Deferred.
- **Dream cycle:** scheduled overnight auto-brainstorm + digest. Deliberately
  deferred until the mechanism is validated — no automation around an unproven
  engine.

---

## 10. Testing

`tests/test_brainstorm.py` (pytest, matches existing test dir), synthetic
deterministic vectors — no LLM calls in unit tests:

- drift band math: drift 0 / 0.5 / 1.0 produce the expected similarity windows
- different-source preference actually prefers cross-source partners
- dedup rejects a hypothesis embedded 0.9-similar to a stored (and to a
  `killed`) one
- lifecycle: `update_hypothesis` transitions persist and round-trip
- sparse-corpus guard fires under 50 chunks

Plus a **manual smoke run** against Aniket's real DB as the acceptance gate.

---

## 11. Success criterion (the feature is itself a hypothesis)

v1 succeeds if, across the first real runs at 2–3 drift settings, the engine
produces **at least one hypothesis Aniket judges non-obvious and worth
kill-researching.** If several runs produce only noise or the obvious, the
mechanism or the corpus density needs rework *before* any v2 investment — which
is exactly the three-node loop applied to this feature itself.

---

## Appendix — provenance of external claims

- GBrain existence, license, author, date: verified via web search this session
  (`github.com/garrytan/gbrain`, MarkTechPost, Vectorize, 2026).
- GBrain graph layer "+31.4 P@5" and production stats (146,646 pages etc.):
  from secondary sources fetched this session; not independently reproduced.
- "Brainstorm with LSD" mechanism and the ~0.3–0.6 similarity sweet spot: from
  Aniket's source chat relaying Garry Tan's description; **not independently
  verified.** Treated as a starting calibration point, not a proven constant.

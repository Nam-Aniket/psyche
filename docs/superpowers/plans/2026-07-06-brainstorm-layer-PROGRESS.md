# Brainstorm Layer — Build Progress / Resume File

**Purpose:** jump-right-back state. If context is cleared mid-build, read THIS file
first, then the plan, then run the verify command to confirm where we are.

- **Branch:** `feature/brainstorm-layer` (in `~/knowledge-project`)
- **Spec:** `docs/superpowers/specs/2026-07-05-brainstorm-layer-design.md`
- **Plan (full task detail + code):** `docs/superpowers/plans/2026-07-06-brainstorm-layer.md`
- **Verify current state anytime:** `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py -q && git log --oneline feature/brainstorm-layer ^main`

## Design in one paragraph
Additive feature. New root module `brainstorm.py` reads embeddings from the existing
per-topic SQLite DBs under `~/.psyche/` (`knowledge.db`=topic "default", `topic_*.db`),
pools them across topics into one matrix tagged `(topic, chunk_id)`, and writes
hypotheses to its OWN new `~/.psyche/brainstorm.db`. Collision = pick anchor, find a
partner inside a drift-controlled cosine band, prefer a DIFFERENT topic, ask the LLM for
a falsifiable hypothesis + kill-test, dedup against all stored (incl. killed) hypotheses,
store with lifecycle `new→researching→testing→killed/survived`. Zero changes to existing
retrieval/memory/graph code. Surface = 4 MCP tools + `psyche brainstorm`/`gaps` CLI.

## Task status
- [x] **T1** Topic discovery + embedding-compatibility gate ✅ committed, 2 tests green
- [x] **T2** Cross-topic embedding pool ✅ committed, 3 tests green
- [x] **T3** Hypotheses ledger (brainstorm.db) + lifecycle CRUD ✅ committed, 4 tests green
- [x] **T4** Cross-run dedup (incl killed) ✅ committed, 6 tests green
- [x] **T5** Drift band + tiered cross-topic partner selection ✅ committed, 9 tests green
- [x] **T6** LLM collision (falsifiable prompt + retry) ✅ committed, 12 tests green
- [x] **T7** generate_hypotheses orchestration + guards ✅ committed, 15 tests green — COLLISION ENGINE COMPLETE
- [x] **T8** Gap reporter (on-demand kmeans) ✅ committed, 16 tests green
- [x] **T9** Register 4 MCP tools ✅ committed, tools/list verified (all 4 present), server imports clean
- [x] **T10** CLI subcommands ✅ committed, `psyche gaps` verified on real corpus (cross-topic gaps appear)
- [x] **T11** Full-suite regression + real-corpus acceptance run ✅ ALL DONE — 352 tests green, real cross-topic collisions verified

## 🎉 BUILD COMPLETE — all 11 tasks done, feature works on the real 64k-chunk corpus.

## Resume pointer
- **Next task:** NONE — build complete. Remaining: merge decision (finishing-a-development-branch), optional push. Runtime data: ~/.psyche/brainstorm.db now holds 11 real generated pairs (reset with `rm ~/.psyche/brainstorm.db` for a clean slate).
- **Two design outcomes locked in T11:** (1) drift band recalibrated to the real embedding distribution; (2) raw-pairs mode — with no chat model, brainstorm returns collided pairs and the CALLING llm writes the hypothesis via update_hypothesis(id, text=, kill_test=). Internal-LLM mode kicks in automatically if a chat model is ever configured.
- **SIGNAL from T10:** real bge-small-en-v1.5 embeddings are compressed high — even the MOST distant cluster pairs sit at ~0.80 cosine. The drift band [0.15–0.75] may find few/no partners on the real corpus, so T5 band constants (BAND_HI/BAND_SPAN/BAND_SLOPE in brainstorm.py) likely need recalibration upward. Verify in T11.
- **Flagged (out of scope):** existing `build_graph.kmeans` can collapse to one cluster when `np.random.seed(42)` init picks two near-identical points + the convergence check trips on iter 0. Affects the topic-graph feature too. report_gaps now degrades gracefully; kmeans itself left untouched (spawned as separate task).
- **RUN TESTS WITH:** `cd ~/knowledge-project && .venv/bin/python tests/test_brainstorm.py -v` — pytest is NOT installed in the venv; tests are `unittest`, run the file directly (append a class name to run one, e.g. `... test_brainstorm.py TestPool`).
- **Notes / deviations:**
  - Test fixture must call `db.init_db(path)` BEFORE `db.get_connection(path)` — get_connection only connects, it does not create the schema.
  - `sources` table requires `checksum` (UNIQUE NOT NULL) + `created_at` (NOT NULL) — fixture inserts must include them.
  - `python` is not on PATH; use `.venv/bin/python`.

## Key facts pinned against real code (so we don't re-derive)
- `db.get_connection(path)` runs schema init; `db.resolve_db_path(name)` → `~/.psyche/<name>`
- `db.get_all_embeddings_only(conn)` → `[{"chunk_id","embedding": np.float32 array}]`
- `db.get_chunks_by_ids(conn, ids)` → `[{"id","text","location","title","author"}]`
- `build_graph.kmeans(matrix, k, max_iter=20)` → `(labels, centroids)` (centroids unit-normalized)
- `build_graph.clean_json_text(s)` strips ```json fences
- `query.calculate_similarities_vectorized(q, ids_arr, matrix)` → sorted `[(id, sim)]`
- `llm_client.LLMClient()`: `.get_embedding(t)`, `.generate_completion(system, prompt)`, `.chat_model` (=="none" if none)
- embedding-model metadata key = `embed_model`; Aniket's 4 DBs all `BAAI/bge-small-en-v1.5` @384d (compatible)
- tests use `unittest` + temp SQLite DBs; run with pytest

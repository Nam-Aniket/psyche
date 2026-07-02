# Naval Decision Engine — Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox (`- [ ]`) syntax. NO SUBAGENTS (hard user rule) — Fable executes inline. Explicit user approval required before any change inside `~/knowledge-project` (repo) or before writing the `topic_naval` DB.

**Goal:** Turn the fully-ingested `naval` corpus (310 sources / 29,797 chunks) into a queryable decision engine that reasons with Naval's *mental maps* — surfacing only the relevant maps per decision, organized as "Map X says…, Map Y says…, they're in tension here."

**Architecture:** Two levels. **Maps** (named frameworks) are an explicit layer; **rule-atoms** (sharp decision rules) live under each map, stored in the existing `rules` table tagged by `map` + `source_tier` + `source_date`. Tensions and evolution are first-class `rule_links`. Naval's own words (Tier-1 canonical) and the foundational books (Tier-1 foundational) MINT rules; podcasts (Tier-2) attach as dated evidence and can flag evolution but never mint a rival. At query time, always-on core lenses + semantic retrieval + map activation + the user's personal graph (combined agent-side) select the relevant subset. Every Naval-session conversation is auto-persisted via `record_interaction`.

**Tech Stack:** Python 3.12, SQLite (per-topic `topic_naval.db`), existing Psyche modules (`db.py`, `guidance.py`, `mcp_server.py`, `ingest.py`, `hooks/`), YAML domain packs, `pytest`.

---

## Scope note

This is one subsystem (the `naval` topic) built on existing Psyche primitives. It does NOT modify the general auto-memory pipeline except to opt the `naval` topic into default conversation capture. Personal-graph combination is **agent-side** (Claude queries `topic=naval` for frameworks and the personal topic for context, then synthesizes) — no cross-topic DB query is introduced.

## File structure

- **Create** `psyche/domain_packs/naval.yaml` — maps registry, 6 always-on core lenses, 6 query templates, diagnostic questions per map.
- **Modify** `db.py` — additive v-next migration: new columns on `rules` (`map`, `source_date`, `source_tier`, `principle_type`, `current_stance`); new table `rule_links`; helper `add_rule_link`, extend `add_rule` kwargs, `get_rules_by_map`.
- **Create** `naval_extract/` (repo-local tool dir, NOT shipped) — `schema.py` (atom validator), `writer.py` (atom → rules row + links), and per-pass driver notebooks/scripts. Atoms authored as reviewable YAML in `~/Downloads/NAVAL/_brain/atoms/` first, then written to DB after per-map approval.
- **Modify** `guidance.py` — `naval`-aware grouping: render `personal_rules` grouped by `map` with tensions surfaced (only when domain == 'naval').
- **Modify** `hooks/psyche_stop.py` (+ `hooks/_hook_common.py`) — opt the `naval` topic into default `record_interaction` capture.
- **Create** `~/Downloads/NAVAL/_brain/atoms/<map>.yaml` — the reviewable extraction artifacts (one file per map).
- **Test** `tests/test_naval_rules_schema.py`, `tests/test_naval_pack.py`, `tests/test_naval_extract.py`, `tests/test_naval_guidance_grouping.py`, `tests/test_naval_autosave.py`.

## Locked schema additions (real DDL)

```sql
-- additive, idempotent (wrapped in try/except per existing _migrate_* pattern)
ALTER TABLE rules ADD COLUMN map TEXT;             -- leverage|judgment|antifragility|epistemology|cooperation|persuasion|equanimity
ALTER TABLE rules ADD COLUMN source_date TEXT;     -- ISO date of the source utterance
ALTER TABLE rules ADD COLUMN source_tier TEXT;     -- canonical | foundational | evidence
ALTER TABLE rules ADD COLUMN principle_type TEXT;  -- axiom | derived
ALTER TABLE rules ADD COLUMN current_stance TEXT;  -- nullable; set when the principle evolved

CREATE TABLE IF NOT EXISTS rule_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_a INTEGER NOT NULL,          -- earlier / source principle
    rule_b INTEGER NOT NULL,          -- later / target principle
    link_type TEXT NOT NULL,          -- tension | evolution | supports | depends
    as_of TEXT,                       -- date the shift/tension applies
    why TEXT,                         -- one-line rationale
    source TEXT,                      -- citation
    created_at TEXT NOT NULL
);
```
Maps are NOT a table — they live in `naval.yaml` (human-editable registry) and rules join to them via the `map` column. `get_rules_by_map(domain='naval')` groups at read time.

## naval.yaml skeleton (real structure)

```yaml
domain: naval
display_name: "Naval — Decision Engine"
# 6 always-on lenses — fire on EVERY decision (rendered as diagnostic_questions)
core_lenses:
  - "Leverage type: labor / capital / code / media / none?"
  - "Specific-knowledge fit: does this use what is play to me, work to others?"
  - "Accountability: is my name and real downside on the line?"
  - "Compounding: iterated game that compounds, or one-shot reset?"
  - "Risk shape: symmetric / asymmetric upside / asymmetric downside (antifragile)?"
  - "Equanimity: am I pricing an internal result into an external achievement?"
maps:
  leverage:       {name: "Wealth & Leverage",      thinkers: [Naval]}
  judgment:       {name: "Judgment / Clear Thinking", thinkers: [Munger, Feynman]}
  antifragility:  {name: "Risk & Optionality",      thinkers: [Taleb]}
  cooperation:    {name: "Accountability & Cooperation", thinkers: [Naval, Axelrod]}
  epistemology:   {name: "Epistemology",            thinkers: [Popper, Deutsch]}
  persuasion:     {name: "Persuasion",              thinkers: [Cialdini]}
  equanimity:     {name: "Happiness & Desire",      thinkers: [Naval, Nisargadatta, Kapil]}
query_templates:   # agent-side output shapes
  - opportunity_evaluation
  - focus_arbitration       # "doing 4 things → which 1 high-leverage × high-probability?"
  - decision_audit
  - pattern_recognition
  - mental_model_lookup
  - life_habit              # exercise / equanimity / energy decisions
diagnostic_questions:       # consumed by generate_guidance today
  - "Which maps does this decision activate, and what does each say?"
  - "Where do the activated maps conflict, and how does Naval resolve it?"
```

---

## Phase 0 — Schema & pack scaffolding

### Task 1: Additive `rules` migration + `rule_links`

**Files:** Modify `db.py` (near existing `_migrate_v3_*`); Test `tests/test_naval_rules_schema.py`

- [x] **Step 1 — failing test**
```python
# tests/test_naval_rules_schema.py
import sqlite3, db
def test_naval_columns_and_links(tmp_path):
    p = tmp_path/"t.db"; db.init_db(str(p))
    conn = sqlite3.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
    assert {"map","source_date","source_tier","principle_type","current_stance"} <= cols
    assert conn.execute("SELECT count(*) FROM rule_links").fetchone()[0] == 0
```
- [x] **Step 2 — run, expect FAIL** (`pytest tests/test_naval_rules_schema.py -v`) — missing columns / table.
- [x] **Step 3 — implement** the additive ALTERs (idempotent try/except) + `CREATE TABLE rule_links` in the migration path; bump `SCHEMA_VERSION`. Add `add_rule_link(conn, a, b, link_type, as_of, why, source)` and `get_rules_by_map(conn, domain)`.
- [x] **Step 4 — run, expect PASS.**
- [x] **Step 5 — migrate the live DB:** `python -c "import db; db.init_db(os.path.expanduser('~/.psyche/topic_naval.db'))"` (additive — safe on populated DB). *(Approval gate: writes topic DB.)*
- [x] **Step 6 — commit** `feat(naval): add map/tier/date columns + rule_links`.

### Task 2: `naval.yaml` domain pack

**Files:** Create `psyche/domain_packs/naval.yaml`; Test `tests/test_naval_pack.py`

- [x] **Step 1 — failing test:** `guidance.load_domain_pack('naval')` returns dict with `maps` (7 keys) and `core_lenses` (6 items).
- [x] **Step 2 — run, expect FAIL.**
- [x] **Step 3 — write `naval.yaml`** per the skeleton above (all 7 maps, 6 lenses, 6 templates).
- [x] **Step 4 — run, expect PASS.**
- [x] **Step 5 — commit** `feat(naval): domain pack with maps + core lenses`.

## Phase 1 — Extraction tooling + the three passes

### Task 3: Atom validator + writer

**Files:** Create `naval_extract/schema.py`, `naval_extract/writer.py`; Test `tests/test_naval_extract.py`

- [x] **Step 1 — failing test:** `validate(atom)` rejects an atom with no `decision_rule` (returns error); `write_atom(conn, atom)` inserts one `rules` row with `map`/`source_tier`/`source_date` populated.
- [x] **Step 2 — run, expect FAIL.**
- [x] **Step 3 — implement** `validate` (required: `statement, map, decision_rule, source, source_date, source_tier, principle_type`; `map` ∈ pack maps; `source_tier` ∈ {canonical,foundational,evidence}) and `write_atom` (calls `db.add_rule(...)` + optional `add_rule_link`). Decision-rule-first invariant: an atom with empty `decision_rule` is stored as a principle-note (archival), NOT a rule.
- [x] **Step 4 — run, expect PASS.**
- [x] **Step 5 — commit** `feat(naval): atom validator + writer (decision-rule-first)`.

### Task 4: Tier-1 canonical pass (Naval's own words) — sets `current_stance`

**Sources:** `naval-essays/2019-12-28-rich.md` (HTGR/tweetstorm), Almanack (source 264), nav.al essays.
**Artifact:** `~/Downloads/NAVAL/_brain/atoms/*.yaml` (per map), reviewed before DB write.

- [x] **Step 1** — extract atoms map-by-map from canonical text; every atom: verbatim-grounded `statement`, sharp `decision_rule`, `source_date`, `source_tier: canonical`, `principle_type`, `map`.
- [x] **Step 2 — verification gate (not unit test):** run `naval_extract/report.py` → per-map counts + assert every atom passes `validate` + 100% have a `decision_rule` + dates present. **Show the per-map table to the user for approval.**
- [x] **Step 3** — on approval, `write_atom` all canonical atoms to `topic_naval.db`. *(Approval gate.)*
- [x] **Step 4 — accuracy spot-check:** sample 15 atoms, re-open the cited source line, confirm faithful. Log results.
- [x] **Step 5 — commit** the atom YAMLs + report.

### Task 5: Tier-1 foundational pass (the source books)

**Sources:** Munger, Taleb (×3), Deutsch, Popper, Feynman, Cialdini, Ridley, Darwin, happiness texts (all ingested).

- [x] **Step 1** — for each map, mint the foundational model-atoms attributed to the author (`source_tier: foundational`), `supports`-linked to the Naval principle they ground (e.g., Taleb antifragility → Naval "asymmetric upside").
- [x] **Step 2 — verification gate:** per-map coverage table vs the corpus probe counts (inversion 20 src, antifragile 332 chunks, etc.); flag any map under-covered. Show user.
- [x] **Step 3** — write on approval. *(Approval gate.)*
- [x] **Step 4 — commit.**

### Task 6: Tier-2 evidence pass (podcasts, chronological) — evolution links

**Sources:** `naval-talks/` (tfs-097 2015 → … → modern-wisdom-922 2025, jre-1309, ranveer-444, network-state-2024, tkp-018, tfs-136/473).

- [x] **Step 1** — process chronologically; each podcast quote either (a) attaches as dated evidence (archival note linked to an existing rule) or (b) creates an `evolution` `rule_link` when Naval refines/reverses a stance — **never mints a rival rule.** Set `current_stance` on the evolved principle (e.g., happiness → peace/equanimity, the MW-922 self-disavowal).
- [x] **Step 2 — verification gate:** every `evolution` link has `as_of` + `why` + both endpoints; assert zero Tier-2 rows in `rules` (podcasts don't mint). Show user the evolution-link list.
- [x] **Step 3** — write on approval. *(Approval gate.)*
- [x] **Step 4 — commit.**

## Phase 2 — Query templates (agent-side, maps-organized output)

### Task 7: Maps-grouped guidance rendering

**Files:** Modify `guidance.py` (`generate_guidance`); Test `tests/test_naval_guidance_grouping.py`

- [x] **Step 1 — failing test:** with domain='naval' and 3 seeded rules across 2 maps + 1 tension link, output groups rules under map headings and includes a "Tension:" line.
- [x] **Step 2 — run, expect FAIL.**
- [x] **Step 3 — implement** a `naval`-branch in `generate_guidance` that calls `get_rules_by_map`, renders "**<Map name> says:** …" blocks, and appends surfaced `rule_links(type=tension)`. Default returns `current_stance`; lineage only when material/asked.
- [x] **Step 4 — run, expect PASS.**
- [x] **Step 5 — commit.**

### Task 8: The 6 query templates (prompt specs)

**Files:** Create `~/Downloads/NAVAL/_brain/query_templates.md` (the agent-side shapes).

- [x] **Step 1** — write each template's input→output contract: `opportunity_evaluation` (PURSUE/MODIFY/ELIMINATE), `focus_arbitration` (rank N options by leverage×probability×compounding, recommend the ONE), `decision_audit`, `pattern_recognition`, `mental_model_lookup`, `life_habit`. Each output cites activated maps + rules + personal-graph context.
- [x] **Step 2 — validate** by running `focus_arbitration` against the user's real "4 things at once" situation (dry run, no write). Show output.
- [x] **Step 3 — commit.**

## Phase 3 — Auto-save conversations by default

### Task 9: Opt `naval` topic into default capture

**Files:** Modify `hooks/psyche_stop.py`, `hooks/_hook_common.py`; Test `tests/test_naval_autosave.py`

- [x] **Step 1 — failing test:** a simulated stop with `topic=naval` persists a row via `record_interaction` into `topic_naval`'s store; with capture flag off it does not.
- [x] **Step 2 — run, expect FAIL.**
- [x] **Step 3 — implement** default-on capture for `topic=naval` (config key `auto_capture_topics: [naval]`), calling `record_interaction_tool(session_id, role, content, tool_calls, topic='naval')`.
- [x] **Step 4 — run, expect PASS.**
- [x] **Step 5 — commit.**

## Phase 4 — Validation

### Task 10: Real-decision validation + coverage sign-off

- [x] **Step 1** — run all 6 query types against 3 live decisions (the focus problem; one service-vs-product call; one habit/equanimity call). Confirm each output: groups by map, surfaces tensions, cites personal context, returns current_stance with lineage-on-tap.
- [x] **Step 2** — final per-map coverage table + 20-atom accuracy audit; record in `_brain/validation.md`.
- [ ] **Step 3** — present results; decide promotion (move plan + tooling into repo `docs/` and tag `feat/psyche-naval`).

---

## Self-review

- **Spec coverage:** maps-as-explicit-layer → Tasks 1,2,7 (column + pack + grouped render). No pruning → all 7 maps in pack + Tasks 4–6. Auto-save default → Task 9. Focus/life decisions → Task 8 templates. Two-tier minting → Tasks 4/5/6 gates. Evolution/current-stance → Task 6 + `current_stance` column. Personal-graph combine → agent-side (Scope note). ✓
- **Placeholders:** real DDL, real YAML, real test code, exact paths/functions given; extraction tasks are verification-gated (data, not unit-testable for faithfulness) with explicit accuracy audits. ✓
- **Type consistency:** `add_rule_link`, `get_rules_by_map`, `validate`, `write_atom`, `source_tier`, `map`, `current_stance` used consistently across tasks. ✓

## Execution handoff

NO SUBAGENTS (hard rule) → **inline execution only** (Fable runs every task in this session, via `superpowers:executing-plans`, pausing for approval before each repo change and each `topic_naval` DB write). Start at Task 1.

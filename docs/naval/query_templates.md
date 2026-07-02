# Naval Decision Engine — the 6 query templates (agent-side)

These are output contracts for the synthesizing agent, not code. On any invocation the agent:
1. Pulls the maps render (`format_rules_by_map` via `generate_guidance` / synthesis pack, topic=naval) — all 7 maps, tier-tagged, tensions + current stances included.
2. Runs the 6 **always-on core lenses** from `naval.yaml` against the decision (leverage type / specific-knowledge fit / accountability / compounding / risk shape / equanimity).
3. Pulls **personal-graph context** (Psyche personal topic: active projects, constraints, past decisions) — combined agent-side, never by cross-topic query.
4. Answers in the template's output shape, citing rules by atom id, surfacing tensions rather than averaging them, and using `current_stance` where a rule evolved (lineage only if asked).

Evidence quotes (`rule_evidence`) are on tap for "how did he get there" follow-ups.

---

## 1. opportunity_evaluation

**Input:** one concrete opportunity (offer, deal, project, collaboration).
**Output:** verdict **PURSUE / MODIFY / ELIMINATE**, then:
- Core-lens table (the 6 lenses, one line each, pass/flag).
- "Map X says" — only the 2–4 maps this decision activates, each with the specific firing rules.
- Tensions between activated maps and how Naval resolves them (e.g. anti-02 bounded-bleed vs lev-14 salary-dependence).
- Ruin check (antf-04): any absorbing barrier → automatic ELIMINATE regardless of upside.
- One-line personal-graph fit (constraints: time, money, location, existing commitments).

## 2. focus_arbitration

**Input:** N concurrent pursuits (the "doing 4 things at once" case).
**Output:** a ranked table — each option scored qualitatively on **leverage × probability × compounding** — then **ONE** recommendation:
- The pick, with the rules that force it (judf-05 fat pitch; equ-07: stress = undecided importance; jud-02 doing beats watching).
- What each non-picked option becomes: *absorbed* (served by the pick), *background* (maintenance only), or *dropped* (a desire being returned — equ-01).
- Optionality note (antf-03): prefer the option that keeps the others alive as options.
- Explicit statement of what unhappiness contract is being kept and which are torn up (equ-01).

## 3. decision_audit

**Input:** a decision already made (good or bad outcome) + what happened.
**Output:** process-vs-outcome verdict (antf-07 alternative histories — a lucky win is not a good decision):
- Which maps were consulted at decision time, which were ignored, what the ignored ones would have said.
- Incentive audit (judf-03): whose incentives shaped the inputs?
- Fooling-yourself check (judf-07): what disconfirming evidence was never sought?
- One rule to add/sharpen in the personal graph from this (candidate for `add_rule`, personal topic — not naval; the canon is closed).

## 4. pattern_recognition

**Input:** a recurring situation ("this keeps happening").
**Output:** the named pattern if the maps carry it (turkey problem, lollapalooza, tit-for-tat invasion, hedonic reset, monkey-mind loop):
- The matching rules with their decision procedures.
- The evidence arc if the pattern has one (e.g. the meditation arc on equ-05 — 2015→2024 method drift, stable goal).
- The break-the-loop move each rule prescribes.

## 5. mental_model_lookup

**Input:** a named model or a "what would X say about..." question.
**Output:** the model's rules verbatim from the brain (statement + decision rule + tier + source):
- Canonical (Naval's words) vs foundational (the source thinker) clearly separated.
- `supports` links shown: which Naval principle the model grounds.
- If evolved: current_stance first, then lineage (evolution link + dated evidence quotes) — the only template where lineage renders by default.

## 6. life_habit

**Input:** a habit/health/equanimity decision (exercise, meditation, information diet, sleep, anger).
**Output:**
- Ranked by equ-10 (health > happiness > wealth — reverse of pursuit order).
- The equanimity rules that fire, with current stances (equ-03's 2025 downgrade matters here).
- Via negativa first (antf-02): what to remove before what to add.
- Peace-of-body ordering (tfs097-03 evidence): physical layer before mental.
- One concrete practice framed as understanding, not prescription (equf-04: prescriptions imprison — re-derive it or drop it).

---

*Validation (Task 8 Step 2): focus_arbitration dry-run against the real 4-pursuits situation — see `_brain/task8-focus-arbitration-dryrun.md`.*

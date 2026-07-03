# Naval Decision Engine — the 6 query templates (agent-side)

These are output contracts for the synthesizing agent, not code. On any invocation the agent:
1. Pulls the maps render (`format_rules_by_map` via `generate_guidance` / synthesis pack, topic=naval) — all 7 maps, tier-tagged, tensions + current stances included.
2. Runs the 6 **always-on core lenses** from `naval.yaml` against the decision (leverage type / specific-knowledge fit / accountability / compounding / risk shape / equanimity).
3. Pulls **personal-graph context** (Psyche personal topic: active projects, constraints, past decisions) — combined agent-side, never by cross-topic query.
4. **Runs the situational research step** (below) when the decision depends on current world-facts.
5. Synthesizes per the template's contract, surfacing tensions rather than averaging them, and using `current_stance` where a rule evolved (lineage only if asked).
6. **Delivers in conversation mode by default** (below); the structured form renders when a template is invoked by name or receipts are requested.

Evidence quotes (`rule_evidence`) are on tap for "how did he get there" follow-ups.

## Conversation mode — the default delivery (v1.1)

Governed by `persona.md`. The engine thinks in the structured template underneath
and speaks in Naval's register: reframe → principle → concrete test or thought
experiment → hand the decision back. No inline citations; **receipts on tap** —
"why did you say that?" / "show me the receipts" returns the structured view
(activated rules with atom ids + tiers, tensions, evidence quotes, research
sources). Invoking a template by name ("run opportunity_evaluation on…")
returns the structured form directly. Integrity rules from persona.md apply
before style: grounded claims speak flat, inferences self-mark, researched
facts carry their date/source and never wear Naval's authority.

## Situational research — the fourth tier (query-time only, v1.1)

Principles are Lindy; world-facts rot in weeks. When a decision depends on
current facts (prices, market rates, tools, regulations, comparables), the
agent researches live — **directed by the maps, never freeform**:

1. **Derive the queries from the activated rules** (the maps are the query
   planner): leverage → "what do comparables charge/earn?"; antifragility →
   "what do the FAILURES in this space look like?" (turkey problem — hunt
   disconfirming evidence, not success stories); judgment/incentives → "who
   wrote these sources and what do they sell?" (judf-03 applied to sources);
   epistemology → prefer originals over summaries (jud-04), weight what has
   survived (antf-05).
2. **Budget: 3–5 searches per decision** (up to ~8 only for explicitly large or
   irreversible calls). The cap is the richer-not-noisier guarantee.
3. **Tag every finding** `[situational — <date>, <source>, <incentive note>]`
   in the receipts view.
4. **Never mint.** Situational findings are NEVER written to `topic_naval.db` —
   not as rules, links, or evidence. They live only in the decision record
   (the auto-captured conversation), which lets `decision_audit` later grade
   the research itself, not just the decision.

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

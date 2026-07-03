# Naval Decision Engine

A queryable decision engine over Naval Ravikant's mental maps, built on Psyche's
`topic_naval` database (311 sources / ~31k chunks). It reasons the way Naval
organizes his thinking — "Map X says…, Map Y says…, they're in tension here" —
instead of averaging conflicting advice into mush.

## Architecture (two levels + evidence)

- **Maps** — 7 named frameworks declared in `psyche/domain_packs/naval.yaml`
  (leverage, judgment, antifragility, cooperation, epistemology, persuasion,
  equanimity), each with registered thinkers and 6 always-on core lenses.
- **Rule-atoms** — 82 sharp decision rules in the `rules` table, tagged by
  `map`, `source_tier`, `source_date`. Two minting tiers only:
  **canonical** (Naval's own words, 45) and **foundational** (the source
  thinkers — Munger, Taleb, Popper, Deutsch, Cialdini, Axelrod et al., 37).
- **Evidence** — podcasts/interviews NEVER mint rules. 46 dated verbatim quotes
  live in `rule_evidence`, each attached to an existing rule with a stance
  (origin/confirms/refines/strains). Stance shifts become `evolution` links in
  `rule_links` with `current_stance` set on the evolved rule (see the
  equ-03 → equ-09 happiness→peace reframe, MW #922, 2025).
- **Links** — `rule_links`: 29 supports (foundational → the Naval principle it
  grounds), 5 cross-map tensions, 1 evolution.

## Pipeline

Atom YAMLs (this dir, hand-authored + verbatim-verified) → `naval_extract/`
loaders (two-phase, fail-closed, idempotent) → `topic_naval.db` →
`guidance.format_rules_by_map` renders maps-grouped guidance with tensions and
current stances → the host agent synthesizes per the six
[query template contracts](query_templates.md).

Naval-topic sessions are auto-captured into `memory_recall`
(`hooks/psyche_stop.py`, config key `auto_capture_topics`, default `["naval"]`).

**v1.1 — conversation mode + situational research.** The default delivery is
now conversational in Naval's register, governed by [persona.md](persona.md)
(voice moves distilled verbatim from the corpus; hard integrity rules:
channeling not impersonation, inferences self-mark, receipts on tap). When a
decision depends on current world-facts, the agent runs a **situational
research step** — 3–5 live searches derived from the activated maps, findings
date/source/incentive-tagged, never minted into the DB (query_templates.md §
Situational research). Validated live in [v1.1-validation.md](v1.1-validation.md).

## Documents

- [plan.md](plan.md) — the original 10-task implementation plan (all complete)
- [task5-foundational-report.md](task5-foundational-report.md) — foundational pass + accuracy audit
- [task6-evidence-report.md](task6-evidence-report.md) — chronological evidence pass + the evolution link
- [task8-focus-arbitration-dryrun.md](task8-focus-arbitration-dryrun.md) — live focus_arbitration run
- [validation.md](validation.md) — final validation: 6 templates × 3 live decisions, coverage, 75-item audit
- [atoms/](atoms/) — the source-of-truth YAMLs (canonical, foundational/, evidence/)

Rebuild from scratch: `db.init_db(topic_naval.db)` → `load_atoms_dir(atoms)` →
`load_atoms_dir(atoms/foundational)` → `link_atoms_dirs([both])` →
`load_evidence_dir(atoms/evidence, [both])`.

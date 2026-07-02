# Task 6 — Tier-2 evidence pass: load report

Date: 2026-07-02 · Branch: `feat/psyche-naval` (commit `7644e17`) · DB backup: `topic_naval.db.bak-before-task6`

## What was loaded

**46 evidence items** (dated verbatim quotes) + **1 evolution link**, from all 8 podcasts processed chronologically. Every quote strictly verbatim-verified against its corpus chunk (normalized full-substring match). Zero rules minted — asserted post-load: `rules WHERE source_tier='evidence'` = 0.

| Podcast | Date | Items |
|---|---|---|
| TFS #97 (Evolutionary Angel) | 2015-08 | 6 |
| TFS #136 (Happiness Hacks) | 2016-02 | 3 |
| TKP #18 (Angel Philosopher) | 2017-02 | 7 |
| JRE #1309 | 2019-06 | 6 |
| TFS #473 | 2020-10 | 7 |
| Network State Conf | 2024-09 | 3 |
| Ranveer #444 | 2024 | 3 |
| Modern Wisdom #922 | 2025 | 11 |

Stances: 6 origin · 17 confirms · 22 refines · 1 strains. Most-evidenced rules: equ-03, equ-01, jud-01 (5 each), equ-05 (4), equ-09/coop-01 (3).

## The evolution link

`equ-03 → equ-09` (rules 15 → 21), as_of 2025, source MW #922. **Precision note:** the verbatim disavowal ("I'm not sure that statement is true anymore… notes to myself, highly contextual") targets the happiness-is-satisfaction aphorism the host read back; the choice element *survives* in the same episode (mw922-03). The evolution is a **downgrade-and-reframe**: aphorisms → contextual notes, "happiness" → overloaded word avoided, operative frame → "being okay with where you are" (equ-09). `current_stance` on equ-03 records this. Lineage: peace-primacy on record 2016 (tfs136-01); exact peace-at-rest/motion formulation spoken June 2019 (jre1309-01), 9 months before the essay.

## Notable finds

- "Specific knowledge" in use as an evaluation criterion in **2015** (tfs097-05) — 3 years pre-HTGR.
- The meditation arc: formal practice (2015) → "habit of being meditative" (2017) → self-examination (2020) → "understanding is probably a better route" (2024) — all attached to equ-05.
- tkp018-02 (2017): "My definition keeps evolving" — Naval self-flagged aphorism instability 8 years before MW #922.
- trs444-02: desires corrupt *reasoning*, not just peace — new equanimity×judgment coupling.
- Machine-transcribed episodes (JRE, NS, Ranveer) carry provenance flags; "tweet story" (ns2024-01) kept as transcribed with a sic-note.

## Schema/code delta (commit 7644e17)

- `db.py`: SCHEMA_VERSION 5; `rule_evidence(rule_id, quote, note, stance, source, as_of, created_at)`; `add_rule_evidence`.
- `naval_extract/load.py`: `load_evidence_dir` — two-phase fail-closed; writes evidence rows, evolution links, and `current_stance`; idempotent.
- DB totals: 82 rules · 35 rule_links (29 supports, 5 tension, 1 evolution) · 46 rule_evidence.

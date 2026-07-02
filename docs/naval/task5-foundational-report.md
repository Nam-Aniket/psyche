# Task 5 — Tier-1 foundational pass: load report & accuracy audit

Date: 2026-07-02 · Branch: `feat/psyche-naval` · DB: `~/.psyche/topic_naval.db` (backup: `topic_naval.db.bak-before-task5`)

## Load summary

37 foundational atoms written (all validated, two-phase fail-closed load), 34 rule_links materialized.

| Map | Foundational | Canonical | Authors |
|---|---|---|---|
| judgment | 8 | 8 | Munger 6, Feynman 2 |
| antifragility | 7 | 3 | Taleb 7 (Antifragile, Black Swan, FbR, SITG) |
| persuasion | 8 | 0 (by design) | Cialdini (Influence 2021 ×7, Pre-Suasion ×1) |
| epistemology | 5 | 2 | Popper 2, Deutsch 3 |
| cooperation | 5 | 7 | Axelrod 3, Dawkins/Ridley 2 |
| equanimity | 4 | 11 | Nisargadatta 3, Kapil 1 |
| leverage | 0 (by design) | 14 | Naval-only map; PG/Sovereign Individual noted as future candidates |

Rules table: 45 canonical + 37 foundational = 82.
rule_links: 29 `supports` (foundational → canonical) + 5 `tension` (canonical cross-map, symmetric-deduped).
Unresolved (expected, by design): `lev-02 → svc-income` — references the personal graph, combined agent-side per the plan's scope note.

## Accuracy spot-check — 17/17 faithful

Each sampled atom's evidence re-opened at its cited chunk/page and the verbatim phrase confirmed present:

judf-01 (src280#130 "Invert, always invert") · judf-02 (#244 latticework) · judf-03 (#771 incentives) · judf-05 (#107 fat pitch) · judf-07 (src278#575 fool yourself) · antf-01 (src292#9 barbell) · antf-04 (src307#400 ruin) · antf-06 (src297#128 turkey) · antf-07 (src294#97 alternative histories) · coopf-01 (src6#255 nice/provocable/forgiving/clear) · coopf-02 (#189 shadow of the future) · coopf-05 (src4#61 division of labour) · epif-01 (src279#113 falsifiability) · epif-04 (src282#132 problems are soluble) · equf-02 (src16#459 search for happiness) · persf-03 (Influence p125 social proof) · persf-06 (Influence p225 scarcity) — all FAITHFUL.

## Corpus change

English **Influence: New and Expanded** (Cialdini, 2021, 530 pp) ingested into `topic_naval.db` — replaces the gap left by the excluded French edition. Persuasion evidence quotes are now retrievable in-corpus.

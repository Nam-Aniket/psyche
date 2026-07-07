# Brainstorm v2: defect fixes + engagement-based feedback loop

Date: 2026-07-07
Status: approved by Aniket (chat, 2026-07-07)
Scope: brainstorm.py, small touch in mcp_server.py, tests. Purely additive schema change.

## 1. Why

Code review of the shipped brainstorm layer (spec: 2026-07-05-brainstorm-layer-design.md)
found four concrete defects and one coherence gap. Separately, the hypotheses ledger
already records lifecycle verdicts (new -> researching -> testing -> killed/survived)
but nothing ever reads them back: the generator cannot learn. This design fixes the
defects and closes the loop, per the system's own success test ("eliminate bad ideas
faster, commit harder to good ones").

## 2. Part 1 - defect fixes

1. **Pair dedup in both modes.** `_pair_exists` is checked only in raw mode today
   (brainstorm.py:270); chat mode can re-collide the same pair and burn LLM calls.
   Move the check above the mode branch.
2. **Sample the band, don't max it.** `pick_partner` (brainstorm.py:428) always takes
   the most similar in-band candidate: deterministic, and biased to the band's tame
   upper edge. Replace with uniform random choice within the winning tier.
3. **Record realized similarity.** New nullable REAL column `realized_sim` on
   `hypotheses` (safe additive ALTER TABLE inside `_ledger_conn`). Store the actual
   anchor-partner cosine; the requested `drift` column stays as-is.
4. **Pass the seed to the LLM.** `collide()` gains `seed=None`; when set, the prompt
   states what the user is exploring. Raw mode includes `seed` in the returned item
   so the calling agent writes on-topic. Update the MCP `brainstorm` tool description
   accordingly.
5. **Embed hypotheses written via update_hypothesis.** AMENDED after code check:
   the MCP path already does this (mcp_server.update_hypothesis_tool embeds `text`
   when set). Scope reduced to a regression test locking the behavior in.

Deferred, named: stratified anchor sampling against big-book dominance (partially
superseded by bandit topic selection below).

## 3. Part 2 - feedback loop

**Reward (decided by Aniket): engagement-based.** A hypothesis wins when its status
leaves `new` (researching, testing, killed, survived all count: a clean kill means
the system produced something worth testing). A hypothesis still `new` after 14 days
counts as a loss. Younger `new` rows are pending and count as neither.

**Learner: epsilon-greedy bandit over unordered topic pairs.**

- Arm = unordered topic pair, e.g. `physics x wealth`. Stats computed at generation
  time straight from the ledger (no new tables): wins = rows with status != 'new';
  losses = rows still 'new' older than 14 days.
- Arm score = Laplace-smoothed win rate `(wins + 1) / (wins + losses + 2)`.
- Exploit (70%): pick the best-scoring pair, random anchor chunk from one side,
  partner search constrained to the other side, same drift band as today.
- Explore (30%): exactly today's behavior (random anchor, tiered partner).
- Cold start: under 10 decided hypotheses total, run 100% explore (identical to
  current behavior; the loop switches itself on as data arrives).
- Seeded runs: the seed always owns anchor selection (relevance order unchanged);
  the bandit only biases which topic the partner comes from, among in-band options.

**Bridge score (ranking, not rejection).** For each generated hypothesis (we now have
embeddings for it and both parents):

- paraphrase flag: cosine(hypothesis, either parent) >= 0.92
- balance = 1 - |cos(h, A) - cos(h, B)|
- novelty = 1 - max cosine to stored hypotheses
- score = mean(balance, novelty); results returned sorted best-first with the fields
  attached. Nothing is auto-rejected; no LLM retries are spent on flags.

Constants (`EPSILON = 0.3`, `IGNORE_DAYS = 14`, `PARAPHRASE_SIM = 0.92`,
`MIN_DECIDED = 10`) live in brainstorm.py next to the existing calibration knobs,
each with a ponytail-style comment marking them as retunable from real ledger data.

## 4. Rejected alternatives

- Survived-only or graded lifecycle rewards: sparser signal; punishes falsifiability.
- Learning the distance sweet spot now: needs the realized_sim history this change
  only starts collecting. Bandit arms stay topic pairs; distance learning is v3.
- New stats table / config file: ledger already holds everything at this scale.
- Auto-reject + retry on paraphrase flag: burns calls on a judgment call.

## 5. Testing (pytest, synthetic vectors, no LLM calls)

- chat mode: second run over the same pair is blocked by pair dedup
- partner choice varies across runs (seeded rng) and stays inside the band
- realized_sim persists and round-trips
- collide prompt contains the seed when seeded
- bandit: with epsilon=0 and a synthetic ledger favoring pair (X,Y), next collision
  uses that pair; under 10 decided rows the path is pure explore
- bridge score: hypothesis embedded ~= parent A raises the paraphrase flag; results
  sort by score
- update_hypothesis with text stores an embedding

Acceptance gate: manual smoke run against Aniket's real corpus.

## 6. Success criterion

After ~2 weeks of real use with verdicts flowing: the exploit arm's engagement rate
visibly exceeds the explore arm's (the loop is learning), and no regression in
per-run cost (pair dedup should reduce wasted calls). If engagement volume stays too
low to move the bandit, the bottleneck is verdict capture UX, not the algorithm, and
that becomes the next design question.

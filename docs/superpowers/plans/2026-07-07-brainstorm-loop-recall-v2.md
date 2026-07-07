# Brainstorm feedback loop + Recall v2 + Memory collisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (subagent-driven excluded by user rule). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the two approved specs as three reviewable PRs: PR1 brainstorm defect fixes + engagement bandit + bridge score; PR2 recall freshness/time-sense/context/open-loops; PR3 atomic memories in the collision pool.

**Architecture:** All PR1/PR3 logic lives in brainstorm.py (ledger-derived bandit stats, no new tables; one additive column realized_sim). PR2 touches memzero.py (split standing block) and the two injection hooks (temporal re-rank, transcript-context fallback reusing psyche_extract.transcript_text, open-loops tail). Tests are synthetic-vector unittest, no LLM calls, matching tests/test_brainstorm.py style.

**Tech Stack:** Python 3, sqlite3, numpy, unittest/pytest. Branch: feature/brainstorm-feedback-loop. PR2 branches from PR1's branch? No: PR2 = new branch feature/recall-v2 off PR1 branch after PR1 tasks complete; PR3 = feature/memory-collisions off PR2 head (merge order 1→2→3).

**Specs:** docs/superpowers/specs/2026-07-07-brainstorm-feedback-loop-design.md, docs/superpowers/specs/2026-07-07-recall-v2-and-memory-collisions-design.md

---

## PR1 — brainstorm fixes + feedback loop (branch: feature/brainstorm-feedback-loop)

### Task 1: realized_sim column + pick_partner returns similarity

**Files:**
- Modify: `brainstorm.py` (_ledger_conn, _HYP_COLS, insert_hypothesis, pick_partner, generate_hypotheses)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

Append to tests/test_brainstorm.py:

```python
class TestRealizedSim(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_ledger_gains_realized_sim_column(self):
        conn = brainstorm._ledger_conn(self.ledger)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hypotheses)")}
        conn.close()
        self.assertIn("realized_sim", cols)

    def test_insert_and_list_roundtrip_realized_sim(self):
        hid = brainstorm.insert_hypothesis(
            self.ledger, text="h", kill_test="k", topic_a="a", chunk_a=1,
            snippet_a="sa", topic_b="b", chunk_b=2, snippet_b="sb",
            drift=0.5, embedding=None, realized_sim=0.44)
        row = brainstorm.list_hypotheses(self.ledger)[0]
        self.assertEqual(row["id"], hid)
        self.assertAlmostEqual(row["realized_sim"], 0.44, places=6)


class TestPartnerReturnsSim(unittest.TestCase):
    def test_pick_partner_returns_index_and_similarity(self):
        # anchor row 0; rows 1..3 at varying cosine to it
        matrix = np.array([[1, 0, 0, 0], [0.7, 0.7, 0, 0], [0.5, 0.86, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        index = [{"topic": "t1", "chunk_id": 1, "source": "s"},
                 {"topic": "t2", "chunk_id": 2, "source": "s"},
                 {"topic": "t2", "chunk_id": 3, "source": "s"},
                 {"topic": "t2", "chunk_id": 4, "source": "s"}]
        got = brainstorm.pick_partner(0, matrix, index, (0.4, 0.8))
        self.assertIsNotNone(got)
        j, sim = got
        self.assertIn(j, (1, 2))
        self.assertTrue(0.4 <= sim <= 0.8)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestRealizedSim tests/test_brainstorm.py::TestPartnerReturnsSim -v`
Expected: FAIL (`realized_sim` not in cols; unpack error on pick_partner int return)

- [ ] **Step 3: Implement**

In `_ledger_conn`, after the CREATE TABLE + before `conn.commit()`:

```python
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hypotheses)")}
    if "realized_sim" not in cols:
        conn.execute("ALTER TABLE hypotheses ADD COLUMN realized_sim REAL")
```

`_HYP_COLS`: append `"realized_sim"` to the list.

`insert_hypothesis`: add keyword `realized_sim=None`; add the column and value to the INSERT:

```python
def insert_hypothesis(path, *, text, kill_test, topic_a, chunk_a, snippet_a,
                      topic_b, chunk_b, snippet_b, drift, embedding=None, realized_sim=None):
    ...
    cur = conn.execute(
        """INSERT INTO hypotheses
           (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
            drift, embedding_blob, status, notes, created_at, updated_at, realized_sim)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'new', NULL, ?, ?, ?)""",
        (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
         drift, blob, now, now, realized_sim))
```

`pick_partner`: change the return to a tuple (keep tier logic; random choice comes in Task 2):

```python
    for key in ("diff_topic", "diff_source", "same"):
        if tiers[key]:
            j, s = max(tiers[key], key=lambda t: t[1])
            return (j, float(s))
    return None
```

`generate_hypotheses`: adapt both call sites and thread the similarity through:

```python
        p = pick_partner(anchor, matrix, index, band)
        if p is None:
            wlo, whi = band
            p = pick_partner(anchor, matrix, index, (wlo - 0.05, whi + 0.05))  # widen once
        if p is None:
            continue
        p_idx, realized = p
```

Replace subsequent uses of `p` with `p_idx` (`index[p_idx]`, `matrix[p_idx]`), and pass `realized_sim=realized` to BOTH insert_hypothesis calls (raw mode and chat mode).

- [ ] **Step 4: Run the full brainstorm test file**

Run: `python3 -m pytest tests/test_brainstorm.py -v`
Expected: all PASS (existing pick_partner tests may need the tuple unpack; update any old assertion `pick_partner(...) == int` to unpack `(j, sim)`)

- [ ] **Step 5: Commit**

```bash
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): record realized anchor-partner cosine (realized_sim)"
```

### Task 2: pair dedup in both modes + sampled (non-deterministic) partner

**Files:**
- Modify: `brainstorm.py` (generate_hypotheses, pick_partner)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
class _StubLLM:
    """Chat-capable stub: returns valid collide JSON; distinct embedding per call."""
    chat_model = "stub"
    provider = "stub"

    def __init__(self):
        self.calls = 0

    def generate_completion(self, system, prompt):
        self.calls += 1
        return '{"hypothesis": "h%d", "kill_test": "k"}' % self.calls

    def get_embedding(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.standard_normal(4).astype(np.float32).tolist()


class TestPairDedupBothModes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=30)
        _make_topic_db(os.path.join(self.dir, "topic_two.db"), n=30)
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_chat_mode_never_recollides_a_stored_pair(self):
        llm = _StubLLM()
        first = brainstorm.generate_hypotheses(count=3, drift=0.5, llm=llm,
                                               base_dir=self.dir, ledger_path=self.ledger)
        pairs = {(h["source_a"]["topic"], h["source_b"]["topic"], h["id"]) for h in first}
        self.assertTrue(first)
        second = brainstorm.generate_hypotheses(count=3, drift=0.5, llm=llm,
                                                base_dir=self.dir, ledger_path=self.ledger)
        stored = brainstorm.list_hypotheses(self.ledger)
        keys = [(r["topic_a"], r["chunk_a"], r["topic_b"], r["chunk_b"]) for r in stored]
        canon = [tuple(sorted([(ta, ca), (tb, cb)])) for ta, ca, tb, cb in keys]
        self.assertEqual(len(canon), len(set(canon)), "same pair collided twice")


class TestPartnerSampling(unittest.TestCase):
    def test_partner_varies_across_runs_within_band(self):
        rng_matrix = np.random.default_rng(7).standard_normal((40, 8)).astype(np.float32)
        rng_matrix /= np.linalg.norm(rng_matrix, axis=1, keepdims=True)
        index = [{"topic": "t%d" % (i % 2), "chunk_id": i, "source": "s%d" % (i % 5)}
                 for i in range(40)]
        seen = set()
        for _ in range(30):
            got = brainstorm.pick_partner(0, rng_matrix, index, (-1.0, 1.0))
            self.assertIsNotNone(got)
            seen.add(got[0])
        self.assertGreater(len(seen), 1, "partner choice is deterministic")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestPairDedupBothModes tests/test_brainstorm.py::TestPartnerSampling -v`
Expected: TestPartnerSampling FAILS (max() is deterministic). TestPairDedupBothModes may pass by luck on tiny runs; verify it exercises the dedup path by checking it still passes after Step 3.

- [ ] **Step 3: Implement**

`pick_partner` final selection: replace the `max(...)` with a uniform sample:

```python
    for key in ("diff_topic", "diff_source", "same"):
        if tiers[key]:
            j, s = random.choice(tiers[key])
            return (j, float(s))
    return None
```

`generate_hypotheses`: hoist the pair-existence check above the mode branch (and delete the raw-mode-only check):

```python
        ta, ca = index[anchor]["topic"], index[anchor]["chunk_id"]
        tb, cb = index[p_idx]["topic"], index[p_idx]["chunk_id"]
        if _pair_exists(ledger_path, ta, ca, tb, cb):
            continue

        if raw_mode:
            hid = insert_hypothesis(...)   # unchanged, minus its old _pair_exists guard
```

- [ ] **Step 4: Run the full file**

Run: `python3 -m pytest tests/test_brainstorm.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add brainstorm.py tests/test_brainstorm.py
git commit -m "fix(brainstorm): pair dedup in chat mode; sample the band instead of max"
```

### Task 3: seed reaches the LLM (and raw-mode callers)

**Files:**
- Modify: `brainstorm.py` (collide, generate_hypotheses), `mcp_server.py` (brainstorm tool description)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestSeedInPrompt(unittest.TestCase):
    def test_collide_prompt_contains_seed(self):
        captured = {}

        class Cap(_StubLLM):
            def generate_completion(self, system, prompt):
                captured["prompt"] = prompt
                return super().generate_completion(system, prompt)

        out = brainstorm.collide("note a text", "note b text", Cap(), seed="reducing inference cost")
        self.assertIsNotNone(out)
        self.assertIn("reducing inference cost", captured["prompt"])

    def test_raw_mode_items_carry_seed(self):
        d = tempfile.mkdtemp()
        _make_topic_db(os.path.join(d, "knowledge.db"), n=30)
        _make_topic_db(os.path.join(d, "topic_two.db"), n=30)

        class NoChat(_StubLLM):
            chat_model = "none"

        out = brainstorm.generate_hypotheses(count=2, drift=0.5, llm=NoChat(), base_dir=d,
                                             ledger_path=os.path.join(d, "b.db"),
                                             seed="agent memory")
        self.assertTrue(out)
        for h in out:
            self.assertEqual(h.get("seed"), "agent memory")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestSeedInPrompt -v`
Expected: FAIL (collide() has no seed param / KeyError seed)

- [ ] **Step 3: Implement**

`collide` signature + prompt (system string stays untouched for cache stability):

```python
def collide(text_a, text_b, llm, seed=None):
    """Return {'hypothesis','kill_test'} bridging the two texts, or None after one retry."""
    from build_graph import clean_json_text
    lead = f"THE USER IS EXPLORING: {seed}\nThe hypothesis must be relevant to that exploration.\n\n" if seed else ""
    prompt = f"{lead}NOTE A:\n{text_a}\n\nNOTE B:\n{text_b}"
```

`generate_hypotheses` chat path: `out = collide(text_a, text_b, llm, seed=seed)`.
Raw path: add `"seed": seed` to the appended dict when seed is set:

```python
            item = {
                "id": hid, "needs_hypothesis": True, "drift": drift,
                "source_a": {"topic": ta, "snippet": text_a[:300]},
                "source_b": {"topic": tb, "snippet": text_b[:300]},
            }
            if seed:
                item["seed"] = seed
            results.append(item)
```

`mcp_server.py` brainstorm tool description: after "...spark ideas about THAT topic;" insert "seeded items echo the seed so you write the hypothesis about it;".

- [ ] **Step 4: Run and commit**

Run: `python3 -m pytest tests/test_brainstorm.py -v` — Expected: PASS

```bash
git add brainstorm.py mcp_server.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): thread the seed into the collide prompt and raw-mode items"
```

### Task 4: regression test — update_hypothesis embedding path (already implemented)

**Files:**
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the test (expected to pass; guards the existing behavior)**

```python
class TestUpdateEmbedsText(unittest.TestCase):
    def test_update_hypothesis_stores_embedding_blob(self):
        d = tempfile.mkdtemp()
        ledger = os.path.join(d, "b.db")
        hid = brainstorm.insert_hypothesis(
            ledger, text="(raw)", kill_test=None, topic_a="a", chunk_a=1, snippet_a="s",
            topic_b="b", chunk_b=2, snippet_b="s", drift=0.5, embedding=None)
        vec = np.ones(4, dtype=np.float32)
        brainstorm.update_hypothesis(ledger, hid, text="real hypothesis", embedding=vec)
        conn = sqlite3.connect(ledger)
        blob = conn.execute("SELECT embedding_blob FROM hypotheses WHERE id=?", (hid,)).fetchone()[0]
        conn.close()
        self.assertIsNotNone(blob)
        self.assertEqual(len(np.frombuffer(blob, dtype=np.float32)), 4)
```

- [ ] **Step 2: Run (expect PASS), commit**

Run: `python3 -m pytest tests/test_brainstorm.py::TestUpdateEmbedsText -v`

```bash
git add tests/test_brainstorm.py
git commit -m "test(brainstorm): lock in embedding storage on update_hypothesis"
```

### Task 5: bandit — ledger stats + epsilon-greedy pair choice

**Files:**
- Modify: `brainstorm.py` (new constants + pair_stats, arm_score, choose_topic_pair)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestBanditStats(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ledger = os.path.join(self.dir, "b.db")

    def _seed(self, topic_a, topic_b, status, created_at):
        hid = brainstorm.insert_hypothesis(
            self.ledger, text="h", kill_test="k", topic_a=topic_a, chunk_a=1, snippet_a="s",
            topic_b=topic_b, chunk_b=2, snippet_b="s", drift=0.5, embedding=None)
        conn = sqlite3.connect(self.ledger)
        conn.execute("UPDATE hypotheses SET status=?, created_at=? WHERE id=?",
                     (status, created_at, hid))
        conn.commit(); conn.close()

    def test_engagement_wins_and_stale_losses(self):
        self._seed("x", "y", "killed", "2026-01-01T00:00:00+00:00")     # win (engaged)
        self._seed("x", "y", "survived", "2026-01-01T00:00:00+00:00")   # win
        self._seed("x", "y", "new", "2026-01-01T00:00:00+00:00")        # old new -> loss
        self._seed("a", "b", "new", "2099-01-01T00:00:00+00:00")        # young new -> pending
        stats = brainstorm.pair_stats(self.ledger)
        xy = stats[frozenset(("x", "y"))]
        self.assertEqual((xy["wins"], xy["losses"]), (2, 1))
        ab = stats.get(frozenset(("a", "b")), {"wins": 0, "losses": 0})
        self.assertEqual((ab["wins"], ab["losses"]), (0, 0))

    def test_choose_pair_exploits_best_arm_and_respects_cold_start(self):
        import random as _r
        for _ in range(6):
            self._seed("x", "y", "survived", "2026-01-01T00:00:00+00:00")
        for _ in range(6):
            self._seed("a", "b", "new", "2026-01-01T00:00:00+00:00")
        stats = brainstorm.pair_stats(self.ledger)
        rng = _r.Random(1)
        # epsilon=0 -> always exploit -> best arm is (x, y)
        arm = brainstorm.choose_topic_pair(stats, ["x", "y", "a", "b"], rng=rng, epsilon=0.0)
        self.assertEqual(arm, frozenset(("x", "y")))
        # cold start: fewer decided than MIN_DECIDED -> None (pure explore)
        cold = {frozenset(("x", "y")): {"wins": 1, "losses": 0}}
        self.assertIsNone(brainstorm.choose_topic_pair(cold, ["x", "y"], rng=rng, epsilon=0.0))
        # epsilon=1 -> always explore
        self.assertIsNone(brainstorm.choose_topic_pair(stats, ["x", "y", "a", "b"], rng=rng, epsilon=1.0))
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestBanditStats -v`
Expected: FAIL (no pair_stats attribute)

- [ ] **Step 3: Implement (place under the drift-band constants)**

```python
# ponytail: bandit knobs, all retunable from real ledger data once verdicts flow.
EPSILON = 0.3        # explore share
IGNORE_DAYS = 14     # a 'new' older than this counts as ignored (a loss)
MIN_DECIDED = 10     # below this many decided rows, run pure explore


def pair_stats(ledger_path, now=None):
    """Engagement stats per unordered topic pair, straight from the ledger.
    Win = status left 'new'. Loss = still 'new' and older than IGNORE_DAYS.
    Young 'new' rows are pending and count as neither."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=IGNORE_DAYS)).isoformat()
    conn = _ledger_conn(ledger_path)
    rows = conn.execute("SELECT topic_a, topic_b, status, created_at FROM hypotheses").fetchall()
    conn.close()
    stats = {}
    for ta, tb, status, created in rows:
        d = stats.setdefault(frozenset((ta, tb)), {"wins": 0, "losses": 0})
        if status != "new":
            d["wins"] += 1
        elif (created or "") < cutoff:
            d["losses"] += 1
    return stats


def arm_score(d):
    """Laplace-smoothed engagement rate: unseen arms score 0.5."""
    return (d["wins"] + 1) / (d["wins"] + d["losses"] + 2)


def choose_topic_pair(stats, topics, rng=random, epsilon=None):
    """Epsilon-greedy over cross-topic arms. None = explore (caller keeps
    today's random behavior). Only arms whose both topics are in the pool count."""
    epsilon = EPSILON if epsilon is None else epsilon
    decided = sum(d["wins"] + d["losses"] for d in stats.values())
    if decided < MIN_DECIDED or rng.random() < epsilon:
        return None
    topics = set(topics)
    arms = [a for a in stats if len(a) == 2 and a <= topics]
    if not arms:
        return None
    return max(arms, key=lambda a: arm_score(stats[a]))
```

- [ ] **Step 4: Run and commit**

Run: `python3 -m pytest tests/test_brainstorm.py -v` — Expected: PASS

```bash
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): engagement-based epsilon-greedy bandit over topic pairs"
```

### Task 6: wire the bandit into generate_hypotheses (+ constrained partner)

**Files:**
- Modify: `brainstorm.py` (pick_partner filter params, generate_hypotheses loop)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestBanditWiring(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=40)
        _make_topic_db(os.path.join(self.dir, "topic_hot.db"), n=40)
        _make_topic_db(os.path.join(self.dir, "topic_cold.db"), n=40)
        self.ledger = os.path.join(self.dir, "b.db")
        conn = brainstorm._ledger_conn(self.ledger)
        for i in range(12):  # make (default, hot) the clear winning arm, past cold start
            conn.execute(
                "INSERT INTO hypotheses (text, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,"
                " drift, status, created_at, updated_at) VALUES ('h', 'default', ?, 'hot', ?, 's', 's',"
                " 0.5, 'survived', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (900 + i, 950 + i))
        conn.commit(); conn.close()

    def test_exploit_run_collides_the_winning_arm(self):
        llm = _StubLLM()
        out = brainstorm.generate_hypotheses(count=3, drift=1.0, llm=llm, base_dir=self.dir,
                                             ledger_path=self.ledger, epsilon=0.0)
        self.assertTrue(out)
        for h in out:
            arm = frozenset((h["source_a"]["topic"], h["source_b"]["topic"]))
            self.assertEqual(arm, frozenset(("default", "hot")))

    def test_pick_partner_only_topic_filter(self):
        matrix = np.random.default_rng(3).standard_normal((30, 8)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        index = [{"topic": ("hot" if i % 3 == 0 else "cold"), "chunk_id": i, "source": "s"}
                 for i in range(30)]
        got = brainstorm.pick_partner(1, matrix, index, (-1.0, 1.0), only_topic="hot")
        self.assertIsNotNone(got)
        self.assertEqual(index[got[0]]["topic"], "hot")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestBanditWiring -v`
Expected: FAIL (unexpected keyword epsilon / only_topic)

- [ ] **Step 3: Implement**

`pick_partner` gains filters (exclude_topic used by PR3; harmless now):

```python
def pick_partner(anchor_idx, matrix, index, band, only_topic=None, exclude_topic=None):
    """Pick a partner (index, similarity) for the anchor within the band.
    only_topic restricts candidates to one topic (bandit exploit mode);
    exclude_topic drops a topic entirely. Tier preference: diff topic, then
    diff source, then same source; uniform random within the winning tier."""
    lo, hi = band
    sims = _cosims(matrix[anchor_idx], matrix)
    a = index[anchor_idx]
    tiers = {"diff_topic": [], "diff_source": [], "same": []}
    for j, s in enumerate(sims):
        if j == anchor_idx:
            continue
        t = index[j]["topic"]
        if only_topic is not None and t != only_topic:
            continue
        if exclude_topic is not None and t == exclude_topic:
            continue
        if lo <= s <= hi:
            if t != a["topic"]:
                tiers["diff_topic"].append((j, s))
            elif index[j]["source"] != a["source"]:
                tiers["diff_source"].append((j, s))
            else:
                tiers["same"].append((j, s))
    for key in ("diff_topic", "diff_source", "same"):
        if tiers[key]:
            j, s = random.choice(tiers[key])
            return (j, float(s))
    return None
```

`generate_hypotheses` signature gains `epsilon=None` (None = module EPSILON; tests pass 0.0/1.0). Before the loop:

```python
    stats = pair_stats(ledger_path)
    by_topic = {}
    for i, e in enumerate(index):
        by_topic.setdefault(e["topic"], []).append(i)
```

Inside the loop, replace the anchor/partner selection block:

```python
    while len(results) < count and attempts < max_attempts and order:
        attempts += 1
        arm = choose_topic_pair(stats, by_topic, epsilon=epsilon)
        if arm is not None and not seed:
            side_a, side_b = sorted(arm)
            if random.random() < 0.5:
                side_a, side_b = side_b, side_a
            anchor = random.choice(by_topic[side_a])
            partner_topic = side_b
        else:
            anchor = order.pop(0)   # explore: front = seed-relevance or shuffled
            partner_topic = None
            if seed and arm is not None:
                # seeded exploit: seed owns the anchor; bandit nudges partner topic
                cands = [a for a in stats if len(a) == 2 and index[anchor]["topic"] in a]
                if cands:
                    best = max(cands, key=lambda a: arm_score(stats[a]))
                    partner_topic = next(iter(best - {index[anchor]["topic"]}), None)
        text_a = _fetch_text(base_dir, index[anchor]["topic"], index[anchor]["chunk_id"])
        if len(text_a) < MIN_CHUNK_CHARS or not _is_prose(text_a):
            continue
        p = pick_partner(anchor, matrix, index, band, only_topic=partner_topic)
        if p is None:
            wlo, whi = band
            p = pick_partner(anchor, matrix, index, (wlo - 0.05, whi + 0.05), only_topic=partner_topic)
        if p is None and partner_topic is not None:
            p = pick_partner(anchor, matrix, index, band)   # fall back to normal tiers
        if p is None:
            continue
        p_idx, realized = p
```

Note the loop guard still requires `order` non-empty; exploit iterations don't pop it, so `attempts < max_attempts` is the effective bound (already present).

- [ ] **Step 4: Run full file + commit**

Run: `python3 -m pytest tests/test_brainstorm.py -v` — Expected: PASS

```bash
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): epsilon-greedy exploit wiring; seeded runs bias partner topic only"
```

### Task 7: bridge score + sorted results

**Files:**
- Modify: `brainstorm.py` (max_ledger_similarity refactor, bridge fields, sort)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
class TestBridgeScore(unittest.TestCase):
    def test_paraphrase_of_parent_is_flagged(self):
        a = np.array([1, 0, 0, 0], dtype=np.float32)
        b = np.array([0, 1, 0, 0], dtype=np.float32)
        hyp_near_a = np.array([0.99, 0.1, 0, 0], dtype=np.float32)
        d = tempfile.mkdtemp()
        s = brainstorm.bridge_score(hyp_near_a, a, b, os.path.join(d, "b.db"))
        self.assertTrue(s["paraphrase"])
        bridge = np.array([0.7, 0.7, 0, 0], dtype=np.float32)
        s2 = brainstorm.bridge_score(bridge, a, b, os.path.join(d, "b.db"))
        self.assertFalse(s2["paraphrase"])
        self.assertGreater(s2["balance"], s["balance"])

    def test_results_sorted_best_first(self):
        d = tempfile.mkdtemp()
        _make_topic_db(os.path.join(d, "knowledge.db"), n=40)
        _make_topic_db(os.path.join(d, "topic_two.db"), n=40)
        out = brainstorm.generate_hypotheses(count=4, drift=0.5, llm=_StubLLM(),
                                             base_dir=d, ledger_path=os.path.join(d, "b.db"))
        scores = [h["score"] for h in out]
        self.assertEqual(scores, sorted(scores, reverse=True))
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_brainstorm.py::TestBridgeScore -v`
Expected: FAIL (no bridge_score)

- [ ] **Step 3: Implement**

Refactor `is_duplicate` into a max-similarity primitive plus the old predicate:

```python
def max_ledger_similarity(path, embedding):
    """Highest cosine between `embedding` and any stored hypothesis embedding (0.0 if none)."""
    conn = _ledger_conn(path)
    rows = conn.execute("SELECT embedding_blob FROM hypotheses WHERE embedding_blob IS NOT NULL").fetchall()
    conn.close()
    q = np.asarray(embedding, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return 0.0
    best = 0.0
    for (blob,) in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        vn = np.linalg.norm(v)
        if vn == 0:
            continue
        best = max(best, float(np.dot(q, v) / (qn * vn)))
    return best


def is_duplicate(path, embedding, threshold=0.85):
    """True if `embedding` cosine >= threshold to ANY stored hypothesis (including killed)."""
    return max_ledger_similarity(path, embedding) >= threshold


PARAPHRASE_SIM = 0.92   # ponytail: hypothesis this close to a parent is a restatement


def _cos(u, v):
    un, vn = np.linalg.norm(u), np.linalg.norm(v)
    if un == 0 or vn == 0:
        return 0.0
    return float(np.dot(u, v) / (un * vn))


def bridge_score(hyp_vec, vec_a, vec_b, ledger_path):
    """Score a hypothesis: balance (sits between its parents, not on one),
    novelty (far from everything stored). paraphrase flags a restatement."""
    ca_, cb_ = _cos(hyp_vec, vec_a), _cos(hyp_vec, vec_b)
    balance = 1.0 - abs(ca_ - cb_)
    novelty = 1.0 - max_ledger_similarity(ledger_path, hyp_vec)
    return {"balance": round(balance, 3), "novelty": round(novelty, 3),
            "paraphrase": max(ca_, cb_) >= PARAPHRASE_SIM,
            "score": round((balance + novelty) / 2.0, 3)}
```

In `generate_hypotheses` chat path, compute the score BEFORE inserting (novelty must not see the row itself), then attach:

```python
        emb = np.asarray(llm.get_embedding(out["hypothesis"]), dtype=np.float32)
        if is_duplicate(ledger_path, emb, DEDUP_THRESHOLD):
            continue
        bridge = bridge_score(emb, matrix[anchor], matrix[p_idx], ledger_path)
        hid = insert_hypothesis(...)
        results.append({
            "id": hid, "hypothesis": out["hypothesis"], "kill_test": out["kill_test"],
            "drift": drift, "realized_sim": realized, **bridge,
            "source_a": {"topic": ta, "snippet": text_a[:300]},
            "source_b": {"topic": tb, "snippet": text_b[:300]},
        })
```

End of function, before `return results`:

```python
    results.sort(key=lambda h: h.get("score", 0.0), reverse=True)
```

- [ ] **Step 4: Run everything + commit**

Run: `python3 -m pytest tests/ -v --timeout=120 2>/dev/null || python3 -m pytest tests/ -v`
Expected: full suite PASS

```bash
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): bridge score (balance+novelty, paraphrase flag), sorted results"
```

### Task 8: PR1 — push and open

- [ ] Run the full suite one final time: `python3 -m pytest tests/ -q` — Expected: PASS
- [ ] `git push -u origin feature/brainstorm-feedback-loop`
- [ ] `gh pr create --title "brainstorm: defect fixes + engagement bandit + bridge score" --body "Implements docs/superpowers/specs/2026-07-07-brainstorm-feedback-loop-design.md ..."` (summarize the 4 fixes + loop; note Aniket merges)

---

## PR2 — recall v2 (branch: feature/recall-v2 off PR1 head)

### Task 9: split standing block in memzero

**Files:**
- Modify: `memzero.py` (constants + standing_fact_rows_split)
- Test: `tests/test_cache_stable_injection.py`

- [ ] **Step 1: Write the failing test** (this file already builds a memory DB fixture; follow its setUp pattern — it seeds atomic_memories via memzero.add_memory with a stub llm)

```python
class TestSplitStandingBlock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "knowledge.db")
        for i in range(15):
            memzero.add_memory(f"decision number {i} about something durable",
                               category="decision", db_path=self.db, llm=_StubEmbedLLM())

    def test_stable_slots_plus_recent_tail(self):
        stable, tail = memzero.standing_fact_rows_split(db_path=self.db)
        self.assertEqual(len(stable), memzero.STABLE_SLOTS)
        self.assertEqual(len(tail), memzero.RECENT_SLOTS)
        stable_ids = [r["id"] for r in stable]
        self.assertEqual(stable_ids, sorted(stable_ids))          # oldest-first, byte-stable
        tail_ids = {r["id"] for r in tail}
        self.assertFalse(tail_ids & set(stable_ids))              # no duplicates
        newest_id = max(r["id"] for r in stable + tail)
        self.assertIn(newest_id, tail_ids)                        # newest decision surfaces

    def test_stable_prefix_identical_across_calls(self):
        s1, _ = memzero.standing_fact_rows_split(db_path=self.db)
        memzero.add_memory("decision sixteen just landed", category="decision",
                           db_path=self.db, llm=_StubEmbedLLM())
        s2, t2 = memzero.standing_fact_rows_split(db_path=self.db)
        self.assertEqual([r["id"] for r in s1], [r["id"] for r in s2])
        self.assertIn("decision sixteen just landed", [r["fact"] for r in t2])
```

(If the file has no `_StubEmbedLLM`, add one returning a seeded random vector, mirroring `_StubLLM.get_embedding` from tests/test_brainstorm.py.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_cache_stable_injection.py -v -k Split`
Expected: FAIL (no standing_fact_rows_split)

- [ ] **Step 3: Implement in memzero.py (below standing_fact_rows)**

```python
STABLE_SLOTS = 8    # byte-stable oldest slots (prompt-cache-friendly prefix)
RECENT_SLOTS = 4    # newest-first tail; changes as decisions land


def standing_fact_rows_split(db_path: str = None, project: str = None):
    """(stable, tail): stable = oldest standing facts, byte-identical across
    sessions; tail = newest standing facts not already in stable. The tail sits
    at the END of the injected block so the stable prefix still prompt-caches."""
    stable = standing_fact_rows(top=STABLE_SLOTS, db_path=db_path, project=project, stable=True)
    seen = {r["id"] for r in stable}
    recent = standing_fact_rows(top=STABLE_SLOTS + RECENT_SLOTS, db_path=db_path,
                                project=project, stable=False)
    tail = [r for r in recent if r["id"] not in seen][:RECENT_SLOTS]
    return stable, tail
```

- [ ] **Step 4: Run + commit**

Run: `python3 -m pytest tests/test_cache_stable_injection.py -v` — Expected: PASS

```bash
git checkout -b feature/recall-v2
git add memzero.py tests/test_cache_stable_injection.py
git commit -m "feat(recall): split standing block - stable prefix + recent tail"
```

### Task 10: session-start hook uses the split + open-loops tail

**Files:**
- Modify: `hooks/psyche_session_start.py`
- Test: `tests/test_cache_stable_injection.py` (open-loops rendering is pure-function tested)

- [ ] **Step 1: Write the failing test**

```python
class TestOpenLoops(unittest.TestCase):
    def test_open_loops_renders_active_items_under_cap(self):
        import brainstorm
        from hooks import psyche_session_start  # if hooks lacks __init__.py, load via importlib below
        d = tempfile.mkdtemp()
        ledger = os.path.join(d, "b.db")
        hid = brainstorm.insert_hypothesis(
            ledger, text="testing this bold idea", kill_test="k", topic_a="a", chunk_a=1,
            snippet_a="s", topic_b="b", chunk_b=2, snippet_b="s", drift=0.5, embedding=None)
        brainstorm.update_hypothesis(ledger, hid, status="testing")
        out = psyche_session_start.open_loops(ledger_path=ledger, knowledge_db=None)
        self.assertIn("testing this bold idea", out)
        self.assertLessEqual(len(out), 400)

    def test_open_loops_empty_when_nothing_active(self):
        from hooks import psyche_session_start
        d = tempfile.mkdtemp()
        out = psyche_session_start.open_loops(ledger_path=os.path.join(d, "b.db"), knowledge_db=None)
        self.assertEqual(out, "")
```

(hooks/ has no `__init__.py`; in the test file import via `importlib.util.spec_from_file_location("psyche_session_start", os.path.join(ROOT, "hooks", "psyche_session_start.py"))` — mirror how tests/test_connect.py loads hook scripts if it does; otherwise use the importlib pattern.)

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

In `hooks/psyche_session_start.py` add:

```python
def open_loops(ledger_path=None, knowledge_db=None, cap=400):
    """Compact 'what to explore' tail: in-flight hypotheses + active experiments.
    Empty string when there is nothing open. Never raises."""
    lines = []
    try:
        import brainstorm
        rows = brainstorm.list_hypotheses(ledger_path or brainstorm._ledger_path())
        active = [r for r in rows if r.get("status") in ("researching", "testing")]
        if active:
            lines.append(f"Open hypotheses ({len(active)} in flight):")
            for r in active[:2]:
                lines.append(f"- [{r['id']}] ({r['status']}) {r['text'][:120]}")
    except Exception:
        pass
    try:
        import db as _db
        path = knowledge_db or _db.resolve_db_path("knowledge.db")
        if path and os.path.exists(path):
            conn = _db.get_connection(path)
            rows = conn.execute("SELECT title FROM experiments WHERE status='active' "
                                "ORDER BY created_at DESC LIMIT 2").fetchall()
            conn.close()
            if rows:
                lines.append("Active experiments: " + "; ".join(r[0] for r in rows))
    except Exception:
        pass
    return "\n".join(lines)[:cap]
```

(add `import os` to the hook's imports.)

And rewire `main()`'s injection body:

```python
    import memzero
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    stable, tail = memzero.standing_fact_rows_split(project=project)
    if not stable and not tail:
        return
    text = memzero.format_facts(stable, max_chars=1500, include_date=False)
    print("Known durable facts about this user/project (Psyche memory):")
    print(text)
    if tail:
        print("Recent additions (newest first):")
        print(memzero.format_facts(tail, max_chars=600, include_date=True))
    loops = open_loops()
    if loops:
        print("Open loops (Psyche):")
        print(loops)
    rows = stable + tail
    hc.write_ledger(session_id, hc.read_ledger(session_id) | {r["id"] for r in rows})
    h = hc.stable_block_hash(text)
    hc.append_ledger("session_start", session_id, len(rows), len(text), block_hash=h,
                     cwd=hc.cwd_from_payload(payload))
    hc.log(f"session_start {session_id}: injected {len(rows)} facts")
```

(`stable_block_hash` stays over the stable text only — that is the cache-relevant prefix the existing ledger analytics track.)

- [ ] **Step 4: Run + commit**

Run: `python3 -m pytest tests/test_cache_stable_injection.py -v` — Expected: PASS

```bash
git add hooks/psyche_session_start.py tests/test_cache_stable_injection.py
git commit -m "feat(recall): session start injects recent-decisions tail + open loops"
```

### Task 11: temporal re-rank + context fallback in the prompt hook

**Files:**
- Modify: `hooks/psyche_prompt_submit.py`
- Test: `tests/test_cache_stable_injection.py`

- [ ] **Step 1: Write the failing tests** (pure helpers, no hook process spin-up)

```python
class TestTemporalRerank(unittest.TestCase):
    def test_temporal_prompt_reorders_by_updated_at(self):
        mod = _load_hook("psyche_prompt_submit")
        rows = [{"id": 1, "fact": "old", "updated_at": "2026-01-01"},
                {"id": 2, "fact": "new", "updated_at": "2026-07-07"}]
        out = mod.rank_for_prompt("what did we do recently on psyche?", list(rows), top=2)
        self.assertEqual([r["id"] for r in out], [2, 1])

    def test_non_temporal_prompt_keeps_relevance_order(self):
        mod = _load_hook("psyche_prompt_submit")
        rows = [{"id": 1, "fact": "old", "updated_at": "2026-01-01"},
                {"id": 2, "fact": "new", "updated_at": "2026-07-07"}]
        out = mod.rank_for_prompt("how does the graph clustering work?", list(rows), top=2)
        self.assertEqual([r["id"] for r in out], [1, 2])


class TestContextFallback(unittest.TestCase):
    def test_short_prompt_builds_query_from_transcript(self):
        mod = _load_hook("psyche_prompt_submit")
        d = tempfile.mkdtemp()
        t = os.path.join(d, "t.jsonl")
        with open(t, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user",
                    "content": [{"type": "text", "text": "tell me about psyche brainstorm"}]}}) + "\n")
        q = mod.build_query("and the graph?", t)
        self.assertIn("and the graph?", q)
        self.assertIn("brainstorm", q)

    def test_unreadable_transcript_degrades_to_prompt(self):
        mod = _load_hook("psyche_prompt_submit")
        self.assertEqual(mod.build_query("and the graph?", "/nope/missing.jsonl"), "and the graph?")
```

(`_load_hook` = the importlib helper from Task 10; define once at module top of the test file.)

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

In `hooks/psyche_prompt_submit.py`:

```python
TEMPORAL_RE = re.compile(
    r"(?i)\b(recent(ly)?|yesterday|today|last (week|session|time|night)|latest|newest"
    r"|what (did|have) we)\b")
CANDIDATES_WHEN_TEMPORAL = 18


def rank_for_prompt(prompt, results, top=6):
    """Temporal prompts rank candidates newest-first before the cut; everything
    else keeps relevance order."""
    if TEMPORAL_RE.search(prompt or ""):
        results = sorted(results, key=lambda r: r.get("updated_at") or "", reverse=True)
    return results[:top]


def build_query(prompt, transcript_path, max_ctx=700):
    """Short prompts borrow context from the transcript tail; failures degrade
    to the bare prompt."""
    try:
        from psyche_extract import transcript_text
        ctx = transcript_text(transcript_path)
        if ctx:
            return f"{ctx[-max_ctx:]}\n{prompt}"
    except Exception:
        pass
    return prompt
```

Rewire `main()`'s search section (the remember-capture block and the `/`/`#` guard stay):

```python
    if prompt.startswith("/") or prompt.startswith("#"):
        return
    import memzero
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    query = prompt
    if len(prompt) < 30:
        query = build_query(prompt, payload.get("transcript_path", ""))
        if query == prompt:
            return                      # no context to lean on; keep old behavior
    top_n = CANDIDATES_WHEN_TEMPORAL if TEMPORAL_RE.search(prompt) else 6
    results = memzero.search_memories(query, top=top_n, project=project)
    if not results and query is prompt:
        wide = build_query(prompt, payload.get("transcript_path", ""))
        if wide != prompt:
            results = memzero.search_memories(wide, top=top_n, project=project)
    results = rank_for_prompt(prompt, results, top=6)
    seen = hc.read_ledger(session_id)
    fresh = [r for r in results if r["id"] not in seen]
    if not fresh:
        return
```

(`import re` is already present in this hook for the remember-capture regex.)

- [ ] **Step 4: Run + commit**

Run: `python3 -m pytest tests/test_cache_stable_injection.py -v` — Expected: PASS

```bash
git add hooks/psyche_prompt_submit.py tests/test_cache_stable_injection.py
git commit -m "feat(recall): temporal re-rank + transcript-context fallback in prompt hook"
```

### Task 12: PR2 — full suite, push, open

- [ ] `python3 -m pytest tests/ -q` — Expected: PASS
- [ ] `git push -u origin feature/recall-v2`
- [ ] `gh pr create --base feature/brainstorm-feedback-loop --title "recall v2: freshness, time sense, context fallback, open loops" --body "Implements sections 2-4 of docs/superpowers/specs/2026-07-07-recall-v2-and-memory-collisions-design.md ..."`

---

## PR3 — memories in the collision pool (branch: feature/memory-collisions off PR2 head)

### Task 13: memory rows in build_pool + fetch + floors + no mem-x-mem

**Files:**
- Modify: `brainstorm.py` (MEMORY_TOPIC, MIN_MEMORY_CHARS, _memory_pool_rows, build_pool, _fetch_text, generate_hypotheses floors)
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing tests**

```python
def _seed_memories(db_path, dim=4, n=6):
    """Minimal atomic_memories rows with embeddings (schema per db.py)."""
    import db as _db
    _db.init_db(db_path)
    conn = sqlite3.connect(db_path)
    for i in range(1, n + 1):
        rng = np.random.default_rng(5000 + i)
        vec = rng.standard_normal(dim).astype(np.float32)
        conn.execute(
            "INSERT INTO atomic_memories (fact, category, embedding_blob, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (f"durable lesson number {i} learned from real project work sessions", "lesson",
             vec.tobytes(), "2026-01-01", "2026-01-01"))
    conn.commit(); conn.close()


class TestMemoryPool(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=30)
        _seed_memories(os.path.join(self.dir, "knowledge.db"))
        _make_topic_db(os.path.join(self.dir, "topic_two.db"), n=30)

    def test_pool_contains_memory_rows_tagged(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, _ = brainstorm.select_compatible_topics(found)
        matrix, index = brainstorm.build_pool(kept, include_memories=True)
        mems = [e for e in index if e["topic"] == brainstorm.MEMORY_TOPIC]
        self.assertEqual(len(mems), 6)
        self.assertEqual(matrix.shape[0], 66)

    def test_dim_mismatch_memories_dropped(self):
        conn = sqlite3.connect(os.path.join(self.dir, "knowledge.db"))
        bad = np.ones(9, dtype=np.float32)
        conn.execute("INSERT INTO atomic_memories (fact, category, embedding_blob, created_at, updated_at)"
                     " VALUES ('odd dim fact for testing dimension guard', 'fact', ?, '2026-01-01', '2026-01-01')",
                     (bad.tobytes(),))
        conn.commit(); conn.close()
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, _ = brainstorm.select_compatible_topics(found)
        matrix, index = brainstorm.build_pool(kept, include_memories=True)
        self.assertEqual(sum(1 for e in index if e["topic"] == brainstorm.MEMORY_TOPIC), 6)

    def test_memory_fetch_and_no_mem_x_mem(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, _ = brainstorm.select_compatible_topics(found)
        matrix, index = brainstorm.build_pool(kept, include_memories=True)
        m_idx = next(i for i, e in enumerate(index) if e["topic"] == brainstorm.MEMORY_TOPIC)
        txt = brainstorm._fetch_text(self.dir, brainstorm.MEMORY_TOPIC, index[m_idx]["chunk_id"])
        self.assertIn("durable lesson", txt)
        got = brainstorm.pick_partner(m_idx, matrix, index, (-1.0, 1.0),
                                      exclude_topic=brainstorm.MEMORY_TOPIC)
        self.assertIsNotNone(got)
        self.assertNotEqual(index[got[0]]["topic"], brainstorm.MEMORY_TOPIC)
```

**Note:** `_fetch_text` for memories must read from the SAME base_dir's knowledge.db (not the user's real one): thread `base_dir` through, using `discover_topics(base_dir)["default"]` as the memory DB path.

- [ ] **Step 2: Run to verify failure**, then **Step 3: Implement**

```python
MEMORY_TOPIC = "__memory__"
MIN_MEMORY_CHARS = 60   # a memory is one sentence; the 200-char chunk floor would reject most


def _memory_pool_rows(base_dir, dim):
    """Live atomic memories as pool entries; dim-mismatched rows are dropped."""
    found = discover_topics(base_dir)
    path = found.get("default")
    if not path:
        return [], []
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT id, category, embedding_blob FROM atomic_memories "
            "WHERE superseded_by IS NULL AND embedding_blob IS NOT NULL").fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return [], []
    vecs, index = [], []
    for mid, cat, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        if dim and len(v) != dim:
            continue
        vecs.append(v)
        index.append({"topic": MEMORY_TOPIC, "chunk_id": mid, "source": cat or "memory"})
    return vecs, index
```

`build_pool(kept, include_memories=False, base_dir=None)`: after the existing chunk loop:

```python
    if include_memories and vecs:
        m_vecs, m_index = _memory_pool_rows(base_dir, dim=len(vecs[0]))
        vecs.extend(m_vecs)
        index.extend(m_index)
```

(`vecs` empty → no chunks → memories are pointless because mem-x-mem is banned; skip.)

`_fetch_text`:

```python
def _fetch_text(base_dir, topic, chunk_id):
    if topic == MEMORY_TOPIC:
        found = discover_topics(base_dir)
        path = found.get("default")
        if not path:
            return ""
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT fact FROM atomic_memories WHERE id = ?", (chunk_id,)).fetchone()
        conn.close()
        return row[0] if row else ""
    found = discover_topics(base_dir)
    conn = db.get_connection(found[topic])
    recs = db.get_chunks_by_ids(conn, [chunk_id])
    conn.close()
    return recs[0]["text"] if recs else ""
```

`generate_hypotheses`: signature gains `include_memories=True`; `build_pool(kept, include_memories=include_memories, base_dir=base_dir)`; per-kind gates:

```python
        a_topic = index[anchor]["topic"]
        floor_a = MIN_MEMORY_CHARS if a_topic == MEMORY_TOPIC else MIN_CHUNK_CHARS
        if len(text_a) < floor_a or (a_topic != MEMORY_TOPIC and not _is_prose(text_a)):
            continue
        excl = MEMORY_TOPIC if a_topic == MEMORY_TOPIC else None
        p = pick_partner(anchor, matrix, index, band, only_topic=partner_topic, exclude_topic=excl)
```

(apply `exclude_topic=excl` to the widened retry + fallback calls too; partner text gate mirrors the same per-kind floor for `index[p_idx]["topic"]`.)

`report_gaps` keeps `include_memories=False` (build_pool default) — gaps stay chunks-only, per spec.

- [ ] **Step 4: Run + commit**

Run: `python3 -m pytest tests/test_brainstorm.py -v` — Expected: PASS

```bash
git checkout -b feature/memory-collisions
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): atomic memories join the collision pool as __memory__"
```

### Task 14: expose include_memories via MCP + CLI

**Files:**
- Modify: `mcp_server.py` (brainstorm_tool + schema), `cli.py` (--no-memories flag)
- Test: `tests/test_brainstorm.py` (signature-level test)

- [ ] **Step 1: Write the failing test**

```python
class TestIncludeMemoriesParam(unittest.TestCase):
    def test_generate_accepts_include_memories_false(self):
        d = tempfile.mkdtemp()
        _make_topic_db(os.path.join(d, "knowledge.db"), n=30)
        _seed_memories(os.path.join(d, "knowledge.db"))
        _make_topic_db(os.path.join(d, "topic_two.db"), n=30)
        out = brainstorm.generate_hypotheses(count=1, drift=0.5, llm=_StubLLM(), base_dir=d,
                                             ledger_path=os.path.join(d, "b.db"),
                                             include_memories=False)
        for h in out:
            self.assertNotEqual(h["source_a"]["topic"], brainstorm.MEMORY_TOPIC)
            self.assertNotEqual(h["source_b"]["topic"], brainstorm.MEMORY_TOPIC)
```

- [ ] **Step 2: Run (fails only if Task 13 skipped the param), then Step 3: Implement**

`mcp_server.py` `brainstorm_tool(count=5, drift=0.5, topics=None, seed=None, include_memories=True)`; pass through to `generate_hypotheses`; dispatch adds `include_memories=arguments.get("include_memories", True)`; inputSchema properties gain:

```python
"include_memories": {"type": "boolean", "default": True,
    "description": "Also collide stored atomic memories (your durable facts/lessons) with book/doc chunks."},
```

`cli.py` brainstorm block: add flag parse `elif a == "--no-memories": include_memories = False` (init `include_memories = True` beside the other defaults) and pass to the call.

- [ ] **Step 4: Full suite + commit + push + PR**

Run: `python3 -m pytest tests/ -q` — Expected: PASS

```bash
git add mcp_server.py cli.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): include_memories param (MCP + CLI --no-memories)"
git push -u origin feature/memory-collisions
gh pr create --base feature/recall-v2 --title "brainstorm: memories join the collision pool" --body "Implements section 5 of docs/superpowers/specs/2026-07-07-recall-v2-and-memory-collisions-design.md ..."
```

### Task 15: real-corpus acceptance (manual gate, after Aniket merges)

- [ ] New Claude Code session in the repo: opening context shows a recent (last-48h) decision in the tail + any open loops
- [ ] Prompt "what did we do recently on psyche?": newest facts first
- [ ] `psyche brainstorm --count 5`: at least one `__memory__ x <topic>` pair among results; scores present and sorted
- [ ] `psyche brainstorm --count 3 --seed "psyche recall improvements"`: raw items echo the seed
```

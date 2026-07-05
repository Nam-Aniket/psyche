# Brainstorm Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GBrain-inspired brainstorm layer to Psyche — a collision engine that pairs semantically distant notes *across topics* into falsifiable hypotheses, plus a gap reporter — without changing any existing functionality.

**Architecture:** One new root module `brainstorm.py` that reads embeddings from the existing per-topic SQLite DBs (`~/.psyche/knowledge.db`, `~/.psyche/topic_*.db`), pools them into one matrix tagged by origin topic, and writes hypotheses to its own new `~/.psyche/brainstorm.db`. Surfaced via 4 MCP tools and 2 CLI subcommands. No existing retrieval/memory/graph code is touched.

**Tech Stack:** Python 3, numpy, sqlite3, existing `db.py` / `query.py` / `build_graph.py` / `llm_client.py` helpers. Tests use `unittest` (run via pytest) with temp SQLite DBs, matching the existing `tests/` convention.

**Spec:** `docs/superpowers/specs/2026-07-05-brainstorm-layer-design.md`

**Grounded interfaces (verified against source this session):**
- `db.get_connection(db_path) -> sqlite3.Connection`
- `db.resolve_db_path(name) -> str` (resolves relative names under `~/.psyche/`)
- `db.get_all_embeddings_only(conn) -> list[{"chunk_id": int, "embedding": np.ndarray}]`
- `db.get_chunks_by_ids(conn, ids) -> list[{"id","text","location","title","author"}]`
- `build_graph.kmeans(embeddings: np.ndarray, num_clusters, max_iter=20) -> (labels, centroids)`
- `build_graph.clean_json_text(text) -> str` (strips ```json fences)
- `query.calculate_similarities_vectorized(q_vec, chunk_ids: np.ndarray, matrix) -> list[(id, sim)]` sorted desc
- `llm_client.LLMClient()` with `.get_embedding(text)->list[float]`, `.generate_completion(system, prompt)->str`, `.chat_model` (== `"none"` when no chat model configured)
- metadata table key for embedding model: `embed_model`

---

## File Structure

| File | Responsibility |
|---|---|
| Create: `brainstorm.py` | All brainstorm logic: topic discovery, pool build, ledger, dedup, drift/partner, collision, gaps |
| Modify: `mcp_server.py` | Register 4 MCP tools (thin wrappers) |
| Modify: `cli.py` | Add `brainstorm` + `gaps` subcommands |
| Create: `tests/test_brainstorm.py` | Unit tests with synthetic vectors + temp DBs |

`brainstorm.db` (the ledger) is created at runtime under `~/.psyche/`; not a source file.

---

## Task 1: Topic discovery + embedding-compatibility gate

**Files:**
- Create: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brainstorm.py
import os, sqlite3, tempfile, unittest
import numpy as np
import db
import brainstorm


def _make_topic_db(path, embed_model="BAAI/bge-small-en-v1.5", dim=4, n=0):
    """Create a minimal Psyche-shaped topic DB with n chunks + embeddings.

    Embeddings are deterministic-random *directions* (seeded), NOT parallel vectors,
    so cosine similarities spread across a range — otherwise every pair is 1.0 and no
    drift band would ever match.
    """
    conn = db.get_connection(path)  # runs schema init
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('embed_model', ?)", (embed_model,))
    conn.execute("INSERT INTO sources (id, title, author) VALUES (1, 'src', 'a')")
    for i in range(1, n + 1):
        conn.execute("INSERT INTO chunks (id, source_id, chunk_index, text) VALUES (?,1,?,?)",
                     (i, i, f"chunk text number {i} " * 20))
        rng = np.random.default_rng(1000 + i)               # varied directions
        vec = rng.standard_normal(dim).astype(np.float32)
        conn.execute("INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?,?)",
                     (i, vec.tobytes()))
    conn.commit()
    conn.close()


class TestTopicDiscovery(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=2)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=2)
        _make_topic_db(os.path.join(self.dir, "topic_odd.db"), embed_model="other-model", dim=8, n=2)

    def test_discover_maps_filenames_to_topic_names(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        self.assertEqual(found["default"], os.path.join(self.dir, "knowledge.db"))
        self.assertEqual(found["naval"], os.path.join(self.dir, "topic_naval.db"))
        self.assertEqual(found["odd"], os.path.join(self.dir, "topic_odd.db"))

    def test_compatible_set_drops_mismatched_model(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, skipped = brainstorm.select_compatible_topics(found, requested=None)
        self.assertIn("default", kept)
        self.assertIn("naval", kept)
        self.assertIn("odd", skipped)  # different embed_model -> dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py -x -q`
Expected: FAIL — `AttributeError: module 'brainstorm' has no attribute 'discover_topics'`

- [ ] **Step 3: Write minimal implementation**

```python
# brainstorm.py
"""GBrain-inspired brainstorm layer for Psyche: cross-topic collision engine + gap reporter.

Purely additive. Reads embeddings from existing per-topic DBs; writes hypotheses to its
own ~/.psyche/brainstorm.db. Does not touch any existing retrieval/memory/graph code.
"""
import glob
import os
import sqlite3

import db


def _base_dir():
    """The ~/.psyche directory where topic DBs live."""
    return os.path.dirname(db.resolve_db_path("knowledge.db"))


def _topic_name(path):
    name = os.path.basename(path)
    if name == "knowledge.db":
        return "default"
    if name.startswith("topic_") and name.endswith(".db"):
        return name[len("topic_"):-len(".db")]
    return None


def discover_topics(base_dir=None):
    """Map topic name -> db path for knowledge.db + topic_*.db under base_dir."""
    base_dir = base_dir or _base_dir()
    out = {}
    for path in [os.path.join(base_dir, "knowledge.db")] + sorted(glob.glob(os.path.join(base_dir, "topic_*.db"))):
        if os.path.exists(path):
            t = _topic_name(path)
            if t:
                out[t] = path
    return out


def _embed_signature(db_path):
    """Return (embed_model, dim) for a topic DB, or (None, None) if unreadable."""
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM metadata WHERE key='embed_model'").fetchone()
        model = row[0] if row else None
        blob = conn.execute("SELECT embedding_blob FROM embeddings LIMIT 1").fetchone()
        import numpy as np
        dim = len(np.frombuffer(blob[0], dtype=np.float32)) if blob else None
        conn.close()
        return (model, dim)
    except Exception:
        return (None, None)


def select_compatible_topics(found, requested=None):
    """Keep topics sharing the majority (embed_model, dim); return (kept: dict, skipped: dict).

    requested: optional list of topic names to restrict to (None = all discovered).
    """
    names = list(found) if requested is None else [t for t in requested if t in found]
    sigs = {t: _embed_signature(found[t]) for t in names}
    # majority signature among readable DBs
    counts = {}
    for sig in sigs.values():
        if sig[0] is not None:
            counts[sig] = counts.get(sig, 0) + 1
    if not counts:
        return ({}, {t: found[t] for t in names})
    majority = max(counts, key=counts.get)
    kept, skipped = {}, {}
    for t in names:
        (kept if sigs[t] == majority else skipped)[t] = found[t]
    return (kept, skipped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py -x -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): topic discovery + embedding-compatibility gate"
```

---

## Task 2: Cross-topic pool builder

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestPool(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=3)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=2)

    def test_pool_concatenates_and_tags_topic(self):
        found = brainstorm.discover_topics(base_dir=self.dir)
        kept, _ = brainstorm.select_compatible_topics(found, requested=None)
        matrix, index = brainstorm.build_pool(kept)
        self.assertEqual(matrix.shape[0], 5)         # 3 + 2 chunks
        self.assertEqual(len(index), 5)
        topics = {row["topic"] for row in index}
        self.assertEqual(topics, {"default", "naval"})
        # identity is (topic, chunk_id); chunk_id 1 exists in BOTH topics
        pairs = {(r["topic"], r["chunk_id"]) for r in index}
        self.assertIn(("default", 1), pairs)
        self.assertIn(("naval", 1), pairs)
        # each index row carries a source label
        self.assertTrue(all("source" in r for r in index))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestPool -x -q`
Expected: FAIL — `AttributeError: module 'brainstorm' has no attribute 'build_pool'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py (add `import numpy as np` near the top imports)
import numpy as np


def _source_titles(conn):
    """chunk_id -> source title map, cheaply (no chunk text loaded)."""
    rows = conn.execute(
        "SELECT c.id, s.title FROM chunks c JOIN sources s ON c.source_id = s.id"
    ).fetchall()
    return {cid: title for cid, title in rows}


def build_pool(kept):
    """Load embeddings from each kept topic DB into one matrix + parallel index.

    Returns (matrix: np.ndarray [N, dim], index: list[{"topic","chunk_id","source"}]).
    Global identity is (topic, chunk_id) because chunk ids are only unique within a file.
    """
    vecs, index = [], []
    for topic, path in kept.items():
        conn = db.get_connection(path)
        titles = _source_titles(conn)
        for rec in db.get_all_embeddings_only(conn):
            emb = rec["embedding"]
            if emb is None:
                continue
            vecs.append(emb)
            index.append({"topic": topic, "chunk_id": rec["chunk_id"],
                          "source": titles.get(rec["chunk_id"], "?")})
        conn.close()
    matrix = np.array(vecs, dtype=np.float32) if vecs else np.empty((0, 0), dtype=np.float32)
    return matrix, index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestPool -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): cross-topic embedding pool with (topic, chunk_id) identity"
```

---

## Task 3: Hypotheses ledger (brainstorm.db) + lifecycle CRUD

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = os.path.join(tempfile.mkdtemp(), "brainstorm.db")

    def test_insert_list_update_roundtrip(self):
        import numpy as np
        emb = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        hid = brainstorm.insert_hypothesis(
            self.ledger, text="H1", kill_test="ten emails",
            topic_a="default", chunk_a=1, snippet_a="a...",
            topic_b="naval", chunk_b=1, snippet_b="b...",
            drift=0.5, embedding=emb)
        self.assertIsInstance(hid, int)
        rows = brainstorm.list_hypotheses(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "new")
        self.assertEqual(rows[0]["topic_b"], "naval")

        brainstorm.update_hypothesis(self.ledger, hid, status="killed", notes="reality said no")
        killed = brainstorm.list_hypotheses(self.ledger, status="killed")
        self.assertEqual(len(killed), 1)
        self.assertEqual(killed[0]["notes"], "reality said no")
        self.assertEqual(brainstorm.list_hypotheses(self.ledger, status="new"), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestLedger -x -q`
Expected: FAIL — `AttributeError: ... 'insert_hypothesis'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py (add `from datetime import datetime, timezone` at top)
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ledger_path():
    return db.resolve_db_path("brainstorm.db")


def _ledger_conn(path=None):
    conn = sqlite3.connect(path or _ledger_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            kill_test TEXT,
            topic_a TEXT, chunk_a INTEGER,
            topic_b TEXT, chunk_b INTEGER,
            snippet_a TEXT, snippet_b TEXT,
            drift REAL,
            embedding_blob BLOB,
            status TEXT NOT NULL DEFAULT 'new',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


_HYP_COLS = ["id", "text", "kill_test", "topic_a", "chunk_a", "topic_b", "chunk_b",
             "snippet_a", "snippet_b", "drift", "status", "notes", "created_at", "updated_at"]


def insert_hypothesis(path, *, text, kill_test, topic_a, chunk_a, snippet_a,
                      topic_b, chunk_b, snippet_b, drift, embedding):
    conn = _ledger_conn(path)
    now = _now()
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    cur = conn.execute(
        """INSERT INTO hypotheses
           (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
            drift, embedding_blob, status, notes, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'new', NULL, ?, ?)""",
        (text, kill_test, topic_a, chunk_a, topic_b, chunk_b, snippet_a, snippet_b,
         drift, blob, now, now))
    conn.commit()
    hid = cur.lastrowid
    conn.close()
    return hid


def list_hypotheses(path, status=None):
    conn = _ledger_conn(path)
    q = f"SELECT {', '.join(_HYP_COLS)} FROM hypotheses"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY created_at DESC"
    rows = [dict(zip(_HYP_COLS, r)) for r in conn.execute(q, args).fetchall()]
    conn.close()
    return rows


def update_hypothesis(path, hid, status=None, notes=None):
    conn = _ledger_conn(path)
    sets, args = ["updated_at = ?"], [_now()]
    if status is not None:
        sets.append("status = ?"); args.append(status)
    if notes is not None:
        sets.append("notes = ?"); args.append(notes)
    args.append(hid)
    conn.execute(f"UPDATE hypotheses SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestLedger -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): hypotheses ledger in brainstorm.db with lifecycle CRUD"
```

---

## Task 4: Cross-run dedup (incl. killed)

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestDedup(unittest.TestCase):
    def setUp(self):
        self.ledger = os.path.join(tempfile.mkdtemp(), "brainstorm.db")
        self.base = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        brainstorm.insert_hypothesis(
            self.ledger, text="stored", kill_test="t",
            topic_a="default", chunk_a=1, snippet_a="a",
            topic_b="naval", chunk_b=1, snippet_b="b",
            drift=0.5, embedding=self.base)
        brainstorm.update_hypothesis(self.ledger, 1, status="killed")

    def test_near_duplicate_of_killed_is_rejected(self):
        near = np.array([0.99, 0.01, 0.0], dtype=np.float32)   # cosine ~1.0
        self.assertTrue(brainstorm.is_duplicate(self.ledger, near, threshold=0.85))

    def test_distinct_vector_is_allowed(self):
        far = np.array([0.0, 1.0, 0.0], dtype=np.float32)      # orthogonal
        self.assertFalse(brainstorm.is_duplicate(self.ledger, far, threshold=0.85))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestDedup -x -q`
Expected: FAIL — `AttributeError: ... 'is_duplicate'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py
def is_duplicate(path, embedding, threshold=0.85):
    """True if `embedding` cosine >= threshold to ANY stored hypothesis (including killed)."""
    conn = _ledger_conn(path)
    rows = conn.execute("SELECT embedding_blob FROM hypotheses WHERE embedding_blob IS NOT NULL").fetchall()
    conn.close()
    q = np.asarray(embedding, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return False
    for (blob,) in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        vn = np.linalg.norm(v)
        if vn == 0:
            continue
        if float(np.dot(q, v) / (qn * vn)) >= threshold:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestDedup -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): cross-run dedup against stored + killed hypotheses"
```

---

## Task 5: Drift band + cross-topic partner selection

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestDriftAndPartner(unittest.TestCase):
    def test_drift_band_endpoints(self):
        self.assertEqual(brainstorm.drift_band(0.0), (0.60, 0.75))
        lo, hi = brainstorm.drift_band(0.5)
        self.assertAlmostEqual(lo, 0.375, places=3)
        self.assertAlmostEqual(hi, 0.525, places=3)
        lo, hi = brainstorm.drift_band(1.0)
        self.assertAlmostEqual(lo, 0.15, places=3)
        self.assertAlmostEqual(hi, 0.30, places=3)

    def test_partner_prefers_different_topic_in_band(self):
        # anchor at index 0 (topic default). Two in-band candidates:
        # idx1 same topic, idx2 different topic -> must pick idx2.
        matrix = np.array([
            [1.0, 0.0],     # anchor
            [0.7, 0.71],    # same-topic, in band ~0.7
            [0.7, 0.71],    # diff-topic, in band ~0.7
        ], dtype=np.float32)
        index = [
            {"topic": "default", "chunk_id": 1, "source": "s1"},
            {"topic": "default", "chunk_id": 2, "source": "s1"},
            {"topic": "naval",   "chunk_id": 9, "source": "s2"},
        ]
        p = brainstorm.pick_partner(0, matrix, index, band=(0.5, 0.9))
        self.assertEqual(index[p]["topic"], "naval")

    def test_partner_none_when_band_empty(self):
        matrix = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)  # sim 1.0, outside band
        index = [{"topic": "default", "chunk_id": 1, "source": "s"},
                 {"topic": "naval", "chunk_id": 1, "source": "s"}]
        self.assertIsNone(brainstorm.pick_partner(0, matrix, index, band=(0.2, 0.5)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestDriftAndPartner -x -q`
Expected: FAIL — `AttributeError: ... 'drift_band'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py

# ponytail: band coefficients are the calibration knob. If first real runs on a given
# embedding model put the "interesting" collisions elsewhere, tune BAND_HI/BAND_SPAN here.
BAND_HI = 0.75      # upper edge at drift=0
BAND_SPAN = 0.15    # band width
BAND_SLOPE = 0.45   # how far the band slides down per unit drift


def drift_band(drift):
    """Return (low, high) cosine-similarity window for a drift in [0,1]."""
    hi = BAND_HI - BAND_SLOPE * drift
    return (hi - BAND_SPAN, hi)


def _cosims(anchor_vec, matrix):
    q = anchor_vec
    qn = np.linalg.norm(q)
    norms = np.linalg.norm(matrix, axis=1)
    norms = np.where(norms == 0, 1e-10, norms)
    if qn == 0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    return np.dot(matrix, q) / (qn * norms)


def pick_partner(anchor_idx, matrix, index, band):
    """Pick a partner row index for the anchor, preferring (a) different topic,
    (b) different source same topic, (c) same source. Returns index or None if band empty."""
    lo, hi = band
    sims = _cosims(matrix[anchor_idx], matrix)
    a = index[anchor_idx]
    tiers = {"diff_topic": [], "diff_source": [], "same": []}
    for j, s in enumerate(sims):
        if j == anchor_idx:
            continue
        if lo <= s <= hi:
            if index[j]["topic"] != a["topic"]:
                tiers["diff_topic"].append((j, s))
            elif index[j]["source"] != a["source"]:
                tiers["diff_source"].append((j, s))
            else:
                tiers["same"].append((j, s))
    for key in ("diff_topic", "diff_source", "same"):
        if tiers[key]:
            # highest similarity within the tier (closest that still respects the band)
            return max(tiers[key], key=lambda t: t[1])[0]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestDriftAndPartner -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): drift band math + tiered cross-topic partner selection"
```

---

## Task 6: Collision generation (LLM prompt + robust JSON parse)

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class _FakeLLM:
    """Stub with the LLMClient surface brainstorm uses."""
    def __init__(self, replies, chat_model="fake"):
        self.replies = list(replies)
        self.chat_model = chat_model
    def generate_completion(self, system, prompt):
        return self.replies.pop(0)
    def get_embedding(self, text):
        return [0.1, 0.2, 0.3]


class TestCollide(unittest.TestCase):
    def test_parses_valid_json(self):
        llm = _FakeLLM(['{"hypothesis": "H", "kill_test": "T"}'])
        out = brainstorm.collide("text a", "text b", llm)
        self.assertEqual(out, {"hypothesis": "H", "kill_test": "T"})

    def test_strips_markdown_fence(self):
        llm = _FakeLLM(['```json\n{"hypothesis": "H2", "kill_test": "T2"}\n```'])
        out = brainstorm.collide("a", "b", llm)
        self.assertEqual(out["hypothesis"], "H2")

    def test_retries_once_then_returns_none(self):
        llm = _FakeLLM(["not json", "still not json"])
        self.assertIsNone(brainstorm.collide("a", "b", llm))
        self.assertEqual(llm.replies, [])  # both attempts consumed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestCollide -x -q`
Expected: FAIL — `AttributeError: ... 'collide'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py (add `import json` at top; reuse clean_json_text from build_graph)
import json
from build_graph import clean_json_text

_COLLIDE_SYSTEM = (
    "You are a hypothesis generator. You are given two unrelated notes from the user's "
    "private knowledge base. Find a NON-OBVIOUS connection and state it as a single "
    "FALSIFIABLE hypothesis — a claim that could be proven wrong by evidence. Reject vague "
    "'these both relate to X' observations. Then give the single cheapest real-world test "
    "that could kill it. Respond ONLY with JSON: "
    '{"hypothesis": "...", "kill_test": "..."}'
)


def collide(text_a, text_b, llm):
    """Return {'hypothesis','kill_test'} bridging the two texts, or None after one retry."""
    prompt = f"NOTE A:\n{text_a}\n\nNOTE B:\n{text_b}"
    for attempt in range(2):
        raw = llm.generate_completion(_COLLIDE_SYSTEM, prompt)
        try:
            data = json.loads(clean_json_text(raw))
            if "hypothesis" in data and "kill_test" in data:
                return {"hypothesis": data["hypothesis"], "kill_test": data["kill_test"]}
        except (json.JSONDecodeError, TypeError):
            pass
        prompt = "Your last reply was not valid JSON. " + prompt  # stricter retry
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestCollide -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): LLM collision with falsifiable-hypothesis prompt + retry"
```

---

## Task 7: `generate_hypotheses` orchestration

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # 60 chunks across two topics to clear the <50 sparse guard
        _make_topic_db(os.path.join(self.dir, "knowledge.db"), n=40)
        _make_topic_db(os.path.join(self.dir, "topic_naval.db"), n=20)
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_generates_and_stores(self):
        llm = _FakeLLM(['{"hypothesis": "H%d", "kill_test": "T"}' % i for i in range(20)])
        out = brainstorm.generate_hypotheses(
            count=2, drift=0.9, topics=None, llm=llm,
            base_dir=self.dir, ledger_path=self.ledger)
        self.assertGreaterEqual(len(out), 1)
        self.assertIn("hypothesis", out[0])
        self.assertIn("source_a", out[0])
        self.assertIn("topic", out[0]["source_a"])
        stored = brainstorm.list_hypotheses(self.ledger)
        self.assertGreaterEqual(len(stored), 1)

    def test_sparse_corpus_refused(self):
        small = tempfile.mkdtemp()
        _make_topic_db(os.path.join(small, "knowledge.db"), n=5)
        llm = _FakeLLM([])
        with self.assertRaises(brainstorm.SparseCorpusError):
            brainstorm.generate_hypotheses(
                count=1, drift=0.5, topics=None, llm=llm,
                base_dir=small, ledger_path=os.path.join(small, "brainstorm.db"))

    def test_no_chat_model_refused(self):
        llm = _FakeLLM([], chat_model="none")
        with self.assertRaises(brainstorm.NoChatModelError):
            brainstorm.generate_hypotheses(
                count=1, drift=0.5, topics=None, llm=llm,
                base_dir=self.dir, ledger_path=self.ledger)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestGenerate -x -q`
Expected: FAIL — `AttributeError: ... 'generate_hypotheses'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py
import random

MIN_POOL = 50
MIN_CHUNK_CHARS = 200
DEDUP_THRESHOLD = 0.85


class SparseCorpusError(Exception):
    pass


class NoChatModelError(Exception):
    pass


class IncompatibleTopicsError(Exception):
    pass


def _fetch_text(base_dir, topic, chunk_id):
    found = discover_topics(base_dir)
    conn = db.get_connection(found[topic])
    recs = db.get_chunks_by_ids(conn, [chunk_id])
    conn.close()
    return recs[0]["text"] if recs else ""


def generate_hypotheses(count=5, drift=0.5, topics=None, llm=None,
                        base_dir=None, ledger_path=None):
    if llm is None:
        from llm_client import LLMClient
        llm = LLMClient()
    if getattr(llm, "chat_model", "none") == "none":
        raise NoChatModelError("brainstorm needs a chat model; embeddings alone can't write hypotheses.")

    ledger_path = ledger_path or _ledger_path()
    found = discover_topics(base_dir)
    kept, skipped = select_compatible_topics(found, requested=topics)
    if not kept:
        raise IncompatibleTopicsError("no embedding-compatible topics to collide.")

    matrix, index = build_pool(kept)
    if matrix.shape[0] < MIN_POOL:
        raise SparseCorpusError(f"pooled corpus has {matrix.shape[0]} chunks (< {MIN_POOL}); ingest more first.")

    band = drift_band(drift)
    results, skipped_pairs, attempts = [], 0, 0
    max_attempts = count * 12
    order = list(range(len(index)))
    random.shuffle(order)

    while len(results) < count and attempts < max_attempts and order:
        attempts += 1
        anchor = order.pop()
        text_a = _fetch_text(base_dir, index[anchor]["topic"], index[anchor]["chunk_id"])
        if len(text_a) < MIN_CHUNK_CHARS:
            continue
        p = pick_partner(anchor, matrix, index, band)
        if p is None:
            wlo, whi = band
            p = pick_partner(anchor, matrix, index, (wlo - 0.05, whi + 0.05))  # widen once
        if p is None:
            continue
        text_b = _fetch_text(base_dir, index[p]["topic"], index[p]["chunk_id"])
        if len(text_b) < MIN_CHUNK_CHARS:
            continue
        out = collide(text_a, text_b, llm)
        if out is None:
            skipped_pairs += 1
            continue
        emb = np.asarray(llm.get_embedding(out["hypothesis"]), dtype=np.float32)
        if is_duplicate(ledger_path, emb, DEDUP_THRESHOLD):
            continue
        hid = insert_hypothesis(
            ledger_path, text=out["hypothesis"], kill_test=out["kill_test"],
            topic_a=index[anchor]["topic"], chunk_a=index[anchor]["chunk_id"], snippet_a=text_a[:300],
            topic_b=index[p]["topic"], chunk_b=index[p]["chunk_id"], snippet_b=text_b[:300],
            drift=drift, embedding=emb)
        results.append({
            "id": hid, "hypothesis": out["hypothesis"], "kill_test": out["kill_test"], "drift": drift,
            "source_a": {"topic": index[anchor]["topic"], "snippet": text_a[:300]},
            "source_b": {"topic": index[p]["topic"], "snippet": text_b[:300]},
        })
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestGenerate -x -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): generate_hypotheses orchestration + sparse/no-chat guards"
```

---

## Task 8: Gap reporter

**Files:**
- Modify: `brainstorm.py`
- Test: `tests/test_brainstorm.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brainstorm.py
class TestGaps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Two clearly separated blobs so kmeans finds distant clusters.
        conn = db.get_connection(os.path.join(self.dir, "knowledge.db"))
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('embed_model','BAAI/bge-small-en-v1.5')")
        conn.execute("INSERT INTO sources (id, title, author) VALUES (1,'s','a')")
        vecs = [np.array([1.0, 0.0], np.float32)] * 30 + [np.array([0.0, 1.0], np.float32)] * 30
        for i, v in enumerate(vecs, start=1):
            conn.execute("INSERT INTO chunks (id, source_id, chunk_index, text) VALUES (?,1,?,?)",
                         (i, i, f"text chunk {i} " * 20))
            conn.execute("INSERT INTO embeddings (chunk_id, embedding_blob) VALUES (?,?)", (i, v.tobytes()))
        conn.commit(); conn.close()
        self.ledger = os.path.join(self.dir, "brainstorm.db")

    def test_reports_distant_cluster_pairs(self):
        out = brainstorm.report_gaps(topics=None, top=5, base_dir=self.dir,
                                     ledger_path=self.ledger, num_clusters=2)
        self.assertIn("cluster_gaps", out)
        self.assertGreaterEqual(len(out["cluster_gaps"]), 1)
        gap = out["cluster_gaps"][0]
        self.assertIn("cluster_a", gap)
        self.assertIn("cluster_b", gap)
        self.assertLess(gap["similarity"], 0.5)  # the two blobs are orthogonal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestGaps -x -q`
Expected: FAIL — `AttributeError: ... 'report_gaps'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to brainstorm.py (add `from build_graph import kmeans` to the build_graph import line)
from build_graph import clean_json_text, kmeans
from collections import Counter


def _cluster_label(members, index):
    """Label a cluster by its dominant topic + most common source."""
    topics = Counter(index[i]["topic"] for i in members)
    sources = Counter(index[i]["source"] for i in members)
    return {
        "topic": topics.most_common(1)[0][0],
        "source": sources.most_common(1)[0][0],
        "size": len(members),
    }


def report_gaps(topics=None, top=10, base_dir=None, ledger_path=None, num_clusters=None):
    """Report the most disconnected cluster pairs across the pooled corpus."""
    found = discover_topics(base_dir)
    kept, _ = select_compatible_topics(found, requested=topics)
    matrix, index = build_pool(kept)
    n = matrix.shape[0]
    if n < 2:
        return {"cluster_gaps": [], "isolated_concepts": [], "note": "not enough material yet"}

    k = num_clusters or max(2, min(12, int(np.sqrt(n))))
    labels, centroids = kmeans(matrix, k)

    members = {c: [i for i in range(n) if labels[i] == c] for c in range(k)}
    # cosine between centroids (already unit-normalized by kmeans)
    gaps = []
    for a in range(k):
        for b in range(a + 1, k):
            if not members[a] or not members[b]:
                continue
            sim = float(np.dot(centroids[a], centroids[b]))
            gaps.append({
                "cluster_a": _cluster_label(members[a], index),
                "cluster_b": _cluster_label(members[b], index),
                "similarity": sim,
            })
    gaps.sort(key=lambda g: g["similarity"])  # most distant first
    return {"cluster_gaps": gaps[:top], "isolated_concepts": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/knowledge-project && python -m pytest tests/test_brainstorm.py::TestGaps -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add brainstorm.py tests/test_brainstorm.py
git commit -m "feat(brainstorm): cross-topic gap reporter via on-demand kmeans"
```

---

## Task 9: MCP tool registration

**Files:**
- Modify: `mcp_server.py`
- Test: manual (MCP tools are thin wrappers; logic is covered by Tasks 1-8)

- [ ] **Step 1: Read the existing tool pattern**

Run: `cd ~/knowledge-project && sed -n '45,130p' mcp_server.py`
Note how `search_knowledge_tool` is defined and how tools are registered (decorator or a registry list near `main()`). Match that exact pattern.

- [ ] **Step 2: Add the four wrapper functions**

Add near the other `*_tool` functions in `mcp_server.py`:

```python
def brainstorm_tool(count: int = 5, drift: float = 0.5, topics: list = None):
    import brainstorm
    try:
        return {"hypotheses": brainstorm.generate_hypotheses(count=count, drift=drift, topics=topics)}
    except brainstorm.NoChatModelError as e:
        return {"error": str(e)}
    except brainstorm.SparseCorpusError as e:
        return {"error": str(e)}
    except brainstorm.IncompatibleTopicsError as e:
        return {"error": str(e)}


def report_gaps_tool(top: int = 10, topics: list = None):
    import brainstorm
    return brainstorm.report_gaps(top=top, topics=topics)


def list_hypotheses_tool(status: str = None):
    import brainstorm
    return {"hypotheses": brainstorm.list_hypotheses(brainstorm._ledger_path(), status=status)}


def update_hypothesis_tool(id: int, status: str = None, notes: str = None):
    import brainstorm
    brainstorm.update_hypothesis(brainstorm._ledger_path(), id, status=status, notes=notes)
    return {"ok": True, "id": id}
```

- [ ] **Step 3: Register the four tools**

Register `brainstorm`, `report_gaps`, `list_hypotheses`, `update_hypothesis` in the same place/way the existing tools are registered (mirror `search_knowledge`). Include short descriptions:
- `brainstorm`: "Collide notes across topics into falsifiable hypotheses. Args: count, drift (0-1, higher=wilder), topics (list or omit for all)."
- `report_gaps`: "Report disconnected regions of your knowledge. Args: top, topics."
- `list_hypotheses`: "List stored hypotheses. Args: status (new/researching/testing/killed/survived)."
- `update_hypothesis`: "Move a hypothesis along its lifecycle. Args: id, status, notes."

- [ ] **Step 4: Smoke-test the server imports**

Run: `cd ~/knowledge-project && python -c "import mcp_server; print('ok')"`
Expected: `ok` (no import/syntax errors)

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add mcp_server.py
git commit -m "feat(brainstorm): register 4 MCP tools (brainstorm, report_gaps, list/update_hypothesis)"
```

---

## Task 10: CLI subcommands

**Files:**
- Modify: `cli.py`
- Test: manual

- [ ] **Step 1: Read the existing dispatch pattern**

Run: `cd ~/knowledge-project && sed -n '25,115p' cli.py`
Note how subcommands are dispatched (the `elif subcommand == "..."` chain) and how args are parsed.

- [ ] **Step 2: Add `brainstorm` and `gaps` handlers**

In the dispatch chain in `cli.py`, following the existing style:

```python
    elif subcommand == "brainstorm":
        import brainstorm
        from llm_client import LLMClient
        drift = 0.5; count = 5; topics = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--drift" and i + 1 < len(args): drift = float(args[i + 1])
            elif a == "--count" and i + 1 < len(args): count = int(args[i + 1])
            elif a == "--topics" and i + 1 < len(args): topics = args[i + 1].split(",")
        try:
            out = brainstorm.generate_hypotheses(count=count, drift=drift, topics=topics, llm=LLMClient())
            for h in out:
                print(f"\n[{h['id']}] {h['hypothesis']}\n    kill-test: {h['kill_test']}"
                      f"\n    ({h['source_a']['topic']} x {h['source_b']['topic']}, drift {h['drift']})")
            if not out:
                print("No new hypotheses this run (all deduped or band empty). Try a different --drift.")
        except Exception as e:
            print(f"brainstorm: {e}")

    elif subcommand == "gaps":
        import brainstorm
        top = 10; topics = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--top" and i + 1 < len(args): top = int(args[i + 1])
            elif a == "--topics" and i + 1 < len(args): topics = args[i + 1].split(",")
        out = brainstorm.report_gaps(top=top, topics=topics)
        for g in out["cluster_gaps"]:
            a, b = g["cluster_a"], g["cluster_b"]
            print(f"GAP  {a['topic']}/{a['source']} <-x-> {b['topic']}/{b['source']}  (sim {g['similarity']:.2f})")
```

- [ ] **Step 3: Add both to the usage string**

Update the usage/help string (line ~32 and ~114) to include `brainstorm` and `gaps` in the command list.

- [ ] **Step 4: Smoke-test**

Run: `cd ~/knowledge-project && python cli.py gaps --top 3`
Expected: prints up to 3 GAP lines from the real `~/.psyche` corpus (or the "not enough material yet" note). No traceback.

- [ ] **Step 5: Commit**

```bash
cd ~/knowledge-project
git add cli.py
git commit -m "feat(brainstorm): psyche brainstorm + gaps CLI subcommands"
```

---

## Task 11: Full-suite regression + real-corpus acceptance run

**Files:** none (verification only)

- [ ] **Step 1: Run the whole existing test suite (prove C1 — nothing broke)**

Run: `cd ~/knowledge-project && python -m pytest tests/ -q`
Expected: all previously-passing tests still pass + the new `test_brainstorm.py` passes.

- [ ] **Step 2: Real gaps run**

Run: `cd ~/knowledge-project && python cli.py gaps --top 5`
Expected: real GAP lines across your topics (default/naval/…). Sanity-check they read plausibly.

- [ ] **Step 3: Real brainstorm run (the feature's own reality test — spec §11)**

Run: `cd ~/knowledge-project && python cli.py brainstorm --drift 0.6 --count 3`
Then again at `--drift 0.3` and `--drift 0.9`.
Expected: at least one hypothesis across the runs that Aniket judges **non-obvious and worth kill-researching**. If all three runs are pure noise/obvious → STOP and revisit band coefficients (Task 5) or corpus density before any v2 work.

- [ ] **Step 4: Verify the ledger persisted + lifecycle works**

Run: `cd ~/knowledge-project && python -c "import brainstorm; print(len(brainstorm.list_hypotheses(brainstorm._ledger_path())))"`
Expected: a count > 0. Then test one transition:
`python -c "import brainstorm as b; p=b._ledger_path(); b.update_hypothesis(p,1,status='researching'); print(b.list_hypotheses(p,'researching'))"`

- [ ] **Step 5: Final commit + branch summary**

```bash
cd ~/knowledge-project
git add -A && git commit -m "test(brainstorm): full-suite regression green + real-corpus acceptance notes" --allow-empty
git log --oneline feature/brainstorm-layer ^main
```

---

## Self-Review Notes (checked against spec 2026-07-05)

- **§2 C1 (additive):** Task 11 Step 1 runs the full suite to prove nothing broke; no existing file logic modified, only additions to `mcp_server.py`/`cli.py`. ✓
- **§2 C2 (MCP-first):** Tasks 9-10 surface via MCP + CLI; the calling LLM does distillation/research. ✓
- **§2 C5 (cross-topic):** Tasks 2, 5, 7, 8 all pool across topics and prefer cross-topic partners. ✓
- **§2 C6 (compat gate):** Task 1 `select_compatible_topics` + Task 7 wiring. ✓
- **§4 data model:** Task 3 creates the exact `hypotheses` schema in its own `brainstorm.db`. ✓
- **§5 collision:** Tasks 5-7 cover band math, tiered partner, falsifiable prompt, dedup incl. killed, sparse guard. ✓
- **§6 gaps:** Task 8. (Isolated-concepts bonus returns `[]` in v1 — deferred, noted; not a spec requirement blocker.) ✓
- **§8 error handling:** NoChatModelError / SparseCorpusError / IncompatibleTopicsError (Task 7), malformed-JSON retry (Task 6). ✓
- **§10 testing:** cross-topic pool, compat gate, dedup-incl-killed, drift math, lifecycle, sparse guard all have tests. ✓
- **§11 success criterion:** Task 11 Step 3 is the explicit reality-test gate. ✓

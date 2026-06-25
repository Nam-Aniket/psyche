# Psyche Local Web App — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a thin FastAPI layer + a `psyche web` launcher that exposes Psyche's existing engine (ingest, hybrid search, chat, knowledge graph, provider/connect) as a local HTTP API the web frontend will consume.

**Architecture:** Reuse the existing core (db.py, query.py, ingest.py, build_graph.py, llm_client.py, connect.py) untouched. Add a new `web/` package: a FastAPI app that loads LLMClient + embeddings/index once at startup (app.state) with a router-per-domain layout, plus a `web` CLI subcommand. Frontend wiring is a separate plan (after Claude Design).

**Tech Stack:** Python, FastAPI, uvicorn, unittest + fastapi.testclient.TestClient, existing SQLite/sqlite-vec/usearch stack.

**Scope:** Backend only — the API + launcher + tests. The Claude-Design frontend and its wiring are a follow-up plan.

**Spec:** docs/superpowers/specs/2026-06-19-psyche-web-app-design.md

---

## Execution order (IMPORTANT — read first; de-duplication)

The per-domain task clusters were authored in parallel, so a few re-state the shared
foundation. Execute in this order and honor these de-dup rules:

1. **SKEL-1 → SKEL-6 first.** These are the canonical foundation: deps (`web/`), `web/deps.py`
   (`AppState`, `get_state`), `web/state.py` (`build_state`), `web/app.py` (factory + lifespan),
   `tests/test_web_base.py` (the `WebTestCase` fixture), and `web/server.py` + the `psyche web`
   CLI subcommand. Do these once.
2. **SKIP `SEARCH-1` and `SEARCH-4` entirely** — they duplicate SKEL (deps, state, app factory,
   base fixture, CLI subcommand). Run only **SEARCH-2** (`POST /search`) and **SEARCH-3**
   (`POST /chat`).
3. For **INGEST-1, GRAPH-1, CONNECT-1** (router skeletons): the foundation is already in place,
   so do **only** the new-router-file creation + its single `include_router(...)` line in
   `web/app.py` + that router's Pydantic models. Skip any deps / state / app-factory steps they
   restate — those are idempotent no-ops after SKEL.
4. Then the remaining endpoint tasks in each cluster (INGEST-2..6, GRAPH-2..4, CONNECT-2..6).

Net real work: **SKEL-1..6**, then INGEST (router + /sources, /ingest/status, /ingest),
SEARCH-2/3, GRAPH-2..4, CONNECT-2..6. Each task stays TDD: failing test → run → minimal impl →
run → commit.

---

## Architecture & Shared Conventions (read before any task)

### File structure

Create a new `web/` package at repo root (`/Users/aniketnamjoshi/knowledge-project/web/`), router-per-domain so parallel endpoint tasks never edit the same file:

- `web/__init__.py` — empty package marker.
- `web/app.py` — owns ONLY: the `create_app()` factory, the lifespan/startup loader that builds shared state, the FastAPI() instance, CORS (none needed for now), top-level exception handler, and `include_router(...)` registration for every routes_* module. Endpoint authors do NOT edit this except to add exactly one `include_router` line each (kept alphabetical; merge-friendly). Also exposes `app = create_app()` at module bottom for uvicorn.
- `web/deps.py` — shared helpers every router imports: `get_state(request) -> AppState` (returns `request.app.state.psyche`), Pydantic request/response models that are cross-domain (e.g. a shared `ErrorResponse`), and the `AppState` dataclass holding `db_path: str`, `llm: LLMClient`, `chunk_ids: np.ndarray`, `embeddings_matrix: np.ndarray`, `usearch_index` (or None). Domain-specific models live in each router file.
- `web/state.py` — the loader logic: `build_state(db_path: str) -> AppState` (instantiates LLMClient once, runs check_and_migrate_embeddings, preloads usearch index + numpy fallback exactly like query.py main()). Kept separate from app.py so it is unit-testable and so the test fixture can build an AppState directly without HTTP.
- `web/routes_search.py` — `POST /search` (hybrid search, no LLM synthesis) and `POST /chat` (search → format_context → generate_completion).
- `web/routes_sources.py` — `GET /sources` (list ingested sources + chunk counts), `POST /ingest` (ingest an uploaded/identified file), `POST /dedup-check` (checksum lookup).
- `web/routes_graph.py` — `GET /graph/nodes` (concepts), `GET /graph/edges` (concept links), `POST /graph/build` (build concept graph).
- `web/routes_system.py` — `GET /provider` (provider/model info), `POST /connect` (wire a client).
- `web/server.py` — thin `main()` entry that reads db_path via `resolve_db_path`, then `uvicorn.run("web.app:app", ...)`; this is what the new CLI `web` subcommand calls.
- `tests/test_web_base.py` — the reusable fixture base class (see testFixture). Per-endpoint test files (`tests/test_web_search.py`, `tests/test_web_sources.py`, `tests/test_web_graph.py`, `tests/test_web_system.py`) each subclass it; one file per router so parallel test authors never collide.

CLI wiring: in `/Users/aniketnamjoshi/knowledge-project/cli.py`, add an `elif subcommand == "web":` branch that does `import web.server; web.server.main()`, and add `web` to the usage string and the "Available commands" list (the two print lines). This is the only edit to an existing file.

### Startup loading (app.state)

In `web/state.py`, `build_state(db_path)` mirrors `query.py:main()` lines 423-518 exactly, with NO rich/console/stdin interaction:

1. `db_path = resolve_db_path(db_path or os.getenv("DATABASE_PATH", "knowledge.db"))` (from db.py). Do NOT exit on missing file in the web layer — let endpoints 404/503; but DO call `init_db(db_path)` is NOT needed (query.py doesn't), just proceed.
2. `llm = LLMClient()` — constructed ONCE. Note LLMClient.__init__ calls `check_and_run_setup()`, which is a no-op when `TESTING=true` or `unittest` is imported or `PSYCHE_NONINTERACTIVE=1` or stdin is not a tty (see llm_client.py lines 48-63), so server startup never blocks.
3. `check_and_migrate_embeddings(db_path, llm)` (db.py) — same as query.py line 436. It internally skips heavy work in non-interactive/testing mode.
4. Preload the search structures EXACTLY as query.py lines 492-517:
   - `chunk_ids = np.array([], dtype=np.int32)`, `embeddings_matrix = np.array([], dtype=np.float32)`, `usearch_index = None`.
   - If `llm.provider != "none"`: compute `index_path = index_path_for(db_path)` (db.py); try `from usearch.index import Index; if os.path.exists(index_path): usearch_index = Index.restore(index_path)` (wrap in try/except → None).
   - If `usearch_index is None`: open `conn = get_connection(db_path)`, `records = get_all_embeddings_only(conn)` (db.py), close conn; build `chunk_ids = np.array([r["chunk_id"] for r in records if r["embedding"] is not None], dtype=np.int32)` and `embeddings_matrix = np.vstack([r["embedding"] for r in records if r["embedding"] is not None])` when non-empty.
5. Return `AppState(db_path=db_path, llm=llm, chunk_ids=chunk_ids, embeddings_matrix=embeddings_matrix, usearch_index=usearch_index)`.

In `web/app.py`, use a FastAPI lifespan context (modern style): `@asynccontextmanager async def lifespan(app): app.state.psyche = build_state(os.getenv("DATABASE_PATH")); yield`. Pass `lifespan=lifespan` to `FastAPI(...)`. This runs ONCE at process start (and once per TestClient context-enter in tests). Endpoints read via `web/deps.py:get_state(request)` → `request.app.state.psyche`. The LLMClient, embeddings_matrix, chunk_ids, and usearch_index are therefore loaded once and shared read-only across all requests. IMPORTANT: do NOT reload per-request — perform_hybrid_search itself opens short-lived `get_connection(db_path)` connections internally per call (query.py lines 158, 173, 195), so sharing one sqlite connection on app.state is NOT needed and must be avoided (sqlite connections are not thread-safe across FastAPI's threadpool).

### Core call sequences (glue)

- **Hybrid search (POST /search): query_text + optional limit -> ranked chunk records** — from query import perform_hybrid_search, format_context; st = get_state(request); results = perform_hybrid_search(st.db_path, query_text, st.chunk_ids, st.embeddings_matrix, st.llm, usearch_index=st.usearch_index, limit=limit or 5). Returns list[tuple[dict, float]] where dict has keys chunk_id,text,location,source_title,source_author. Serialize each as {"chunk_id":..,"text":..,"location":..,"source_title":..,"source_author":..,"score":score}. Do NOT call the LLM here.
- **Chat (POST /chat): query_text -> synthesized answer + sources** — Same perform_hybrid_search call as above to get `similarities`. Then context_str = format_context(similarities, top_n=limit or 5) (query.py). If st.llm.provider=='none' or getattr(st.llm,'chat_model','none')=='none': return 503 / a JSON {mode:'retrieval', passages:[...]} (mirror query.py offline branch) — do NOT call generate_completion. Else build the SAME prompt query.py single-query mode uses (lines 519-527 system_instruction + lines 744-747 prompt: f"### RETRIEVED CONTEXT FROM BOOKS:\n{context_str}\n\nUser Query: {query_text}"), then answer = st.llm.generate_completion(system_instruction, prompt). Optionally prepend retrieve_concept_context(conn, query_text) as query.py does (open get_connection(st.db_path), call query.retrieve_concept_context, close). Return {answer, sources:[serialized similarities]}.
- **Ingest a file (POST /ingest): file path (and optional title/author) -> source_id + chunk count** — Reuse ingest.py building blocks, NOT ingest.main(). Sequence: from ingest import calculate_sha256, chunk_text, clean_title_from_filename; from parsers import extract_text; from db import get_connection, check_checksum, add_source, add_chunk, add_embedding, build_or_update_usearch_index. (1) checksum = calculate_sha256(path); (2) conn=get_connection(st.db_path); existing=check_checksum(conn,checksum); if existing is not None and not force -> return {status:'skipped', source_id:existing}; (3) blocks = extract_text(path) (list[dict] with keys text,location); (4) chunks=[]; for b in blocks: for c in chunk_text(b['text']): chunks.append({'text':c,'location':b['location']}); (5) if st.llm.provider!='none': embeddings = st.llm.get_embeddings_batch([c['text'] for c in chunks]); (6) source_id = add_source(conn, title or clean_title_from_filename(path), author or 'Unknown', path, checksum); for idx,cd in enumerate(chunks): cid=add_chunk(conn,source_id,idx,cd['text'],location=cd['location']); if st.llm.provider!='none': add_embedding(conn,cid,embeddings[idx]); (7) conn.close(); build_or_update_usearch_index(st.db_path). NOTE: this writes embeddings to disk but does NOT refresh st.usearch_index/embeddings_matrix in memory for the running process; document that ingest endpoints require a process restart to be searchable, OR after build_or_update_usearch_index re-run web.state.build_state and reassign request.app.state.psyche.
- **Dedup-check (POST /dedup-check): checksum OR file path -> existing source_id or null** — from db import get_connection, check_checksum; from ingest import calculate_sha256. If given a path: checksum=calculate_sha256(path). conn=get_connection(st.db_path); try: sid=check_checksum(conn,checksum) finally: conn.close(). Return {exists: sid is not None, source_id: sid}.
- **List sources (GET /sources): -> sources with per-source chunk counts** — Mirror query.py status block (lines 463-483): conn=get_connection(st.db_path); cur=conn.cursor(); cur.execute("SELECT id, title, author FROM sources"); rows=cur.fetchall(); for each, cur.execute("SELECT COUNT(*) FROM chunks WHERE source_id = ?",(id,)). Return [{id,title,author,chunk_count}]. Close conn in finally. (db.py has no dedicated list_sources helper, so raw SQL over the shared schema is correct and matches query.py.)
- **Graph nodes (GET /graph/nodes): -> all concepts** — from db import get_connection, get_all_concepts; conn=get_connection(st.db_path); try: nodes=get_all_concepts(conn) finally: conn.close(). Returns list[dict] {id,name,definition,category}. Return as-is.
- **Graph edges (GET /graph/edges): -> all concept links** — from db import get_connection, get_concept_links; conn=get_connection(st.db_path); try: edges=get_concept_links(conn) finally: conn.close(). Returns list[dict] {id,source,target,relationship,description}. Return as-is.
- **Build graph (POST /graph/build): optional clusters -> ok** — from build_graph import build_concept_graph; build_concept_graph(st.db_path, clusters or 6). NOTE build_concept_graph constructs its OWN LLMClient internally and calls sys.exit() on empty DB / client error (build_graph.py lines 204-206, 224, 343 are in main() not build_concept_graph — but build_concept_graph DOES sys.exit(1) at lines 206 and 224). To avoid killing the web process, guard: first check chunk count via get_connection+COUNT(*) and return 400 if empty; wrap the call in try/except SystemExit -> 500. For AI-free/local providers it auto-delegates to build_cooccurrence_graph(db_path) (build_graph.py line 211-212). Run synchronously; it is slow — acceptable for a thin internal layer. Re-load app.state afterwards is optional (graph reads use fresh connections).
- **Provider info (GET /provider): -> active provider + models** — st = get_state(request). Return {provider: st.llm.provider, chat_provider: getattr(st.llm,'chat_provider', st.llm.provider), embed_model: st.llm.embed_model, chat_model: st.llm.chat_model, db_path: st.db_path}. All are plain attributes set in LLMClient.__init__ (llm_client.py lines 196-244). No new LLMClient construction.
- **Connect a client (POST /connect): client name + optional dry_run -> list of actions** — from connect import connect; actions = connect(client, dry_run=dry_run). client must be one of 'claude-code','codex','gemini','antigravity' (connect.py line 96; raises ValueError on unknown -> map to 400). Returns list[str] of human-readable actions. Return {actions: actions}.

### Test fixture

Create `tests/test_web_base.py`. It builds a real temp SQLite DB seeded via the real db.add_* functions and a fake LLM so tests are fully offline, then wires it into a TestClient. Pattern (matches tests/test_index_sync.py: tempfile.TemporaryDirectory + real db_path so resolve_db_path keeps it absolute and index_path_for derives a sibling .usearch):

```python
import os, sys, tempfile, unittest
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["TESTING"] = "true"          # makes LLMClient.check_and_run_setup a no-op
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
import db
from fastapi.testclient import TestClient

DIM = 8
def fake_embedding(seed):
    rng = np.random.default_rng(seed)
    return rng.random(DIM, dtype=np.float32).tolist()

class FakeLLM:
    """Offline stand-in for LLMClient. provider!='none' so semantic paths run;
    chat_model set so /chat synthesis path is exercised without network."""
    provider = "fake"; chat_provider = "fake"
    embed_model = "fake-embed"; chat_model = "fake-chat"
    def get_embedding(self, text):
        return fake_embedding(abs(hash(text)) % 10000)
    def get_embeddings_batch(self, texts):
        return [self.get_embedding(t) for t in texts]
    def generate_completion(self, system_instruction, prompt):
        return "FAKE ANSWER"

class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            # 2 sources, chunks + embeddings via REAL add_* functions
            s1 = db.add_source(conn, "Meditations", "Marcus Aurelius", "m.txt", "ck_med")
            for i, t in enumerate(["On discipline and the will.", "Nature and the cosmos."]):
                cid = db.add_chunk(conn, s1, i, t, location=f"Book {i+1}")
                db.add_embedding(conn, cid, fake_embedding(i))
            s2 = db.add_source(conn, "Letters", "Seneca", "l.txt", "ck_let")
            cid = db.add_chunk(conn, s2, 0, "On the shortness of life.", location="Letter 1")
            db.add_embedding(conn, cid, fake_embedding(99))
            # concepts + links via REAL helpers
            db.add_concept(conn, "Stoicism", "A school of philosophy.", "Philosophy")
            db.add_concept(conn, "Virtue", "Moral excellence.", "Philosophy")
            db.add_concept_link(conn, "Stoicism", "Virtue", "emphasizes", "Stoicism centers on virtue.")
        finally:
            conn.close()
        db.build_or_update_usearch_index(self.db_path)   # real .usearch sibling
        # Build app state with the temp DB + fake LLM, bypassing build_state's real LLMClient
        import web.state, web.app
        self._orig_build = web.state.build_state
        def fake_build(_db_path=None):
            st = self._orig_build(self.db_path)          # reuse real index/matrix loader...
            return st
        # Simpler: construct AppState directly with FakeLLM so no real LLMClient/network:
        from web.deps import AppState
        from db import index_path_for, get_connection, get_all_embeddings_only
        usearch_index = None
        try:
            from usearch.index import Index
            ip = index_path_for(self.db_path)
            if os.path.exists(ip):
                usearch_index = Index.restore(ip)
        except Exception:
            usearch_index = None
        chunk_ids = np.array([], dtype=np.int32); matrix = np.array([], dtype=np.float32)
        if usearch_index is None:
            c = get_connection(self.db_path)
            try: recs = get_all_embeddings_only(c)
            finally: c.close()
            chunk_ids = np.array([r["chunk_id"] for r in recs if r["embedding"] is not None], dtype=np.int32)
            vecs = [r["embedding"] for r in recs if r["embedding"] is not None]
            if vecs: matrix = np.vstack(vecs)
        self.state = AppState(db_path=self.db_path, llm=FakeLLM(),
                              chunk_ids=chunk_ids, embeddings_matrix=matrix,
                              usearch_index=usearch_index)
        app = web.app.create_app()
        app.state.psyche = self.state                    # override lifespan-loaded state
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
```

Key points for endpoint authors: (1) `os.environ["TESTING"]="true"` MUST be set before importing llm_client/web modules so no setup wizard or network fires. (2) Tests inject `app.state.psyche` directly with `FakeLLM`, so `build_state` (and its real `LLMClient()`) never runs during tests — the TestClient `with` block still triggers lifespan, but the post-construction override wins; if lifespan's real LLMClient is undesirable even transiently, set DATABASE_PATH to a provider='none' env, or have create_app() skip the loader when `app.state` already has `psyche` (recommended: lifespan does `if not getattr(app.state,'psyche',None): app.state.psyche = build_state(...)`). (3) Because perform_hybrid_search opens its own connections from st.db_path, the seeded temp DB is fully exercised end-to-end with zero network. (4) flashrank reranker: query.get_ranker() may try to load a model; set env `RERANK_PROVIDER=none` in the base module (query.py line 37) to force pure-RRF and keep tests fast/offline.

### Dependencies to add

- fastapi>=0.110.0 — add to BOTH /Users/aniketnamjoshi/knowledge-project/requirements.txt and the dependencies list in /Users/aniketnamjoshi/knowledge-project/pyproject.toml (lines 33-46)
- uvicorn[standard]>=0.29.0 — add to requirements.txt and pyproject.toml dependencies (ASGI server for `psyche web`)
- python-multipart>=0.0.9 — add to requirements.txt and pyproject.toml ONLY IF /ingest accepts multipart file uploads (UploadFile). If /ingest takes a server-side file path string in JSON instead, OMIT this dep.
- httpx — already present in .venv (0.28.1), pulled in transitively by fastapi.testclient.TestClient; no need to add explicitly but list it in requirements.txt if you want reproducible test installs (httpx>=0.27.0)
- pyproject.toml [tool.setuptools] py-modules (line 53) is for top-level single-file modules only; the new code is a PACKAGE (web/), so add `packages = ["web"]` under [tool.setuptools] (alongside the existing py-modules line) — do NOT try to list web.app etc. as py-modules.

### Conventions

PATHS: all new app code under `/Users/aniketnamjoshi/knowledge-project/web/`; all tests under `/Users/aniketnamjoshi/knowledge-project/tests/` named `test_web_*.py` (unittest discovery requires the `test_` prefix). RUN TESTS (exact): `TESTING=true .venv/bin/python -m unittest discover tests` from repo root; to run one file: `TESTING=true .venv/bin/python -m unittest tests.test_web_search`.

IMPORTS: existing modules are top-level (import db, import query, import ingest, from connect import connect) — web/ modules add repo root to path the same way every existing module/test does: `sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))` at top of each web/*.py, OR rely on the package being importable since tests already insert repo root. Mirror tests/test_index_sync.py lines 6-8.

APP + ROUTER PATTERN: `web/app.py` defines `create_app() -> FastAPI`. Each routes_*.py defines `router = APIRouter()` at module top and decorates handlers (`@router.post("/search")`). create_app() does `from web import routes_search, routes_sources, routes_graph, routes_system` then `app.include_router(routes_search.router)` etc. — one line per router, kept alphabetical, so parallel authors only append their single registration line (low merge conflict). Handlers take `request: Request` and call `web.deps.get_state(request)`; do not use module-global state.

NAMING: endpoints lowercase kebab/slash (`/dedup-check`, `/graph/nodes`); Pydantic models PascalCase suffixed by role (`SearchRequest`, `SearchResponse`, `ChatRequest`); response keys snake_case matching the existing dict keys returned by db.py (chunk_id, source_title, source_author, etc.) — do not rename them.

ERROR HANDLING: thin layer, fail loud. Use `from fastapi import HTTPException`. Map: missing/empty DB or no chunks → 400 with {"detail": "..."}; chat requested while provider=='none' or chat_model=='none' → 503 (mirror query.py offline branch) OR return a retrieval-only payload (pick 503 for /chat strictness); connect() ValueError on unknown client → 400; file not found in /ingest (extract_text raises FileNotFoundError, parsers.py line 598) → 404; unexpected exceptions bubble to a single `@app.exception_handler(Exception)` in app.py returning 500 {"detail": str(e)}. NEVER call sys.exit in the web layer — the only sys.exit risk is build_graph.build_concept_graph (lines 206/224); guard with an explicit empty-DB pre-check before calling it. Connections: always `conn = get_connection(st.db_path)` then close in `finally`; never reuse one connection across requests (sqlite is not thread-safe across FastAPI's threadpool). No rich/Console/print in web code (those are CLI-only); return JSON.


---

## Project skeleton, startup loading, test fixture & launcher

### Task SKEL-1: Add FastAPI/uvicorn deps and web/ package scaffold

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/requirements.txt`
- Modify `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`
- Create `/Users/aniketnamjoshi/knowledge-project/web/__init__.py`

- [ ] **Step 1: Write the failing test**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_skel_deps.py`:

  ```python
  import os
  import sys
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"


  class TestWebPackageImportable(unittest.TestCase):
      def test_fastapi_importable(self):
          import fastapi  # noqa: F401
          self.assertTrue(True)

      def test_uvicorn_importable(self):
          import uvicorn  # noqa: F401
          self.assertTrue(True)

      def test_web_package_importable(self):
          import web  # noqa: F401
          self.assertTrue(True)


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_skel_deps -v
  ```

  Expected output (before changes):
  ```
  ERROR: test_fastapi_importable ... ERROR
  ModuleNotFoundError: No module named 'fastapi'
  ...
  ERROR: test_web_package_importable ... ERROR
  ModuleNotFoundError: No module named 'web'
  ```

- [ ] **Step 3: Add deps and create the web package**

  Add to `/Users/aniketnamjoshi/knowledge-project/requirements.txt` (append after last line):
  ```
  fastapi>=0.110.0
  uvicorn[standard]>=0.29.0
  httpx>=0.27.0
  ```

  In `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`, add `fastapi`, `uvicorn[standard]`, and `httpx` to the `dependencies` list, and add `packages = ["web"]` under `[tool.setuptools]`:

  Change this block:
  ```toml
  [tool.setuptools]
  py-modules = ["cli", "db", "parsers", "ingest", "query", "llm_client", "build_graph", "mcp_server", "guidance"]
  ```
  to:
  ```toml
  [tool.setuptools]
  py-modules = ["cli", "db", "parsers", "ingest", "query", "llm_client", "build_graph", "mcp_server", "guidance"]
  packages = ["web"]
  ```

  And change the `dependencies` list in `[project]` to:
  ```toml
  dependencies = [
      "python-dotenv>=1.0.0",
      "requests>=2.31.0",
      "pypdf>=4.0.0",
      "pymupdf>=1.24.0",
      "numpy>=1.24.0",
      "rich>=13.7.0",
      "prompt-toolkit>=3.0.40",
      "fastembed>=0.3.0",
      "sqlite-vec>=0.1.9",
      "usearch>=2.25.3",
      "flashrank>=0.2.10",
      "pyyaml>=6.0.0",
      "fastapi>=0.110.0",
      "uvicorn[standard]>=0.29.0",
      "httpx>=0.27.0"
  ]
  ```

  Install the new deps:
  ```
  .venv/bin/pip install fastapi>=0.110.0 "uvicorn[standard]>=0.29.0" "httpx>=0.27.0"
  ```

  Create `/Users/aniketnamjoshi/knowledge-project/web/__init__.py` as an empty file:
  ```python
  ```

- [ ] **Step 4: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_skel_deps -v
  ```

  Expected output:
  ```
  test_fastapi_importable (tests.test_web_skel_deps.TestWebPackageImportable) ... ok
  test_uvicorn_importable (tests.test_web_skel_deps.TestWebPackageImportable) ... ok
  test_web_package_importable (tests.test_web_skel_deps.TestWebPackageImportable) ... ok

  ----------------------------------------------------------------------
  Ran 3 tests in 0.XXXs

  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add requirements.txt pyproject.toml web/__init__.py tests/test_web_skel_deps.py && git commit -m "SKEL-1: add fastapi/uvicorn/httpx deps and web/ package scaffold"
  ```

---

### Task SKEL-2: Create web/deps.py — AppState dataclass and get_state helper

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/deps.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_deps.py`

- [ ] **Step 1: Write the failing test**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_deps.py`:

  ```python
  import os
  import sys
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"

  import numpy as np


  class TestAppState(unittest.TestCase):
      def test_appstate_construction(self):
          from web.deps import AppState

          class FakeLLM:
              provider = "fake"
              chat_provider = "fake"
              embed_model = "fake-embed"
              chat_model = "fake-chat"

          chunk_ids = np.array([1, 2], dtype=np.int32)
          matrix = np.zeros((2, 8), dtype=np.float32)
          st = AppState(
              db_path="/tmp/test.db",
              llm=FakeLLM(),
              chunk_ids=chunk_ids,
              embeddings_matrix=matrix,
              usearch_index=None,
          )
          self.assertEqual(st.db_path, "/tmp/test.db")
          self.assertEqual(st.llm.provider, "fake")
          self.assertEqual(st.chunk_ids.shape, (2,))
          self.assertIsNone(st.usearch_index)

      def test_appstate_fields_present(self):
          from web.deps import AppState
          import dataclasses

          field_names = {f.name for f in dataclasses.fields(AppState)}
          self.assertIn("db_path", field_names)
          self.assertIn("llm", field_names)
          self.assertIn("chunk_ids", field_names)
          self.assertIn("embeddings_matrix", field_names)
          self.assertIn("usearch_index", field_names)

      def test_get_state_returns_psyche_from_request(self):
          from web.deps import AppState, get_state

          class FakeLLM:
              provider = "none"
              chat_provider = "none"
              embed_model = "none"
              chat_model = "none"

          st = AppState(
              db_path="/tmp/x.db",
              llm=FakeLLM(),
              chunk_ids=np.array([], dtype=np.int32),
              embeddings_matrix=np.array([], dtype=np.float32),
              usearch_index=None,
          )

          class FakeAppState:
              psyche = st

          class FakeApp:
              state = FakeAppState()

          class FakeRequest:
              app = FakeApp()

          result = get_state(FakeRequest())
          self.assertIs(result, st)

      def test_error_response_model(self):
          from web.deps import ErrorResponse
          er = ErrorResponse(detail="something went wrong")
          self.assertEqual(er.detail, "something went wrong")


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_deps -v
  ```

  Expected output:
  ```
  ERROR: test_appstate_construction ... ERROR
  ModuleNotFoundError: No module named 'web.deps'
  ```

- [ ] **Step 3: Implement web/deps.py**

  Create `/Users/aniketnamjoshi/knowledge-project/web/deps.py`:

  ```python
  import os
  import sys
  from dataclasses import dataclass, field
  from typing import Any, Optional

  import numpy as np
  from fastapi import Request
  from pydantic import BaseModel

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


  @dataclass
  class AppState:
      """Shared application state loaded once at startup and stored on app.state.psyche."""

      db_path: str
      llm: Any  # LLMClient or compatible duck-type (FakeLLM in tests)
      chunk_ids: np.ndarray
      embeddings_matrix: np.ndarray
      usearch_index: Optional[Any] = None  # usearch.index.Index or None


  def get_state(request: Request) -> AppState:
      """Dependency helper: retrieves the shared AppState from the FastAPI app state."""
      return request.app.state.psyche


  class ErrorResponse(BaseModel):
      """Standard error envelope returned by the top-level exception handler."""

      detail: str
  ```

- [ ] **Step 4: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_deps -v
  ```

  Expected output:
  ```
  test_appstate_construction (tests.test_web_deps.TestAppState) ... ok
  test_appstate_fields_present (tests.test_web_deps.TestAppState) ... ok
  test_error_response_model (tests.test_web_deps.TestAppState) ... ok
  test_get_state_returns_psyche_from_request (tests.test_web_deps.TestAppState) ... ok

  ----------------------------------------------------------------------
  Ran 4 tests in 0.XXXs

  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add web/deps.py tests/test_web_deps.py && git commit -m "SKEL-2: add web/deps.py with AppState dataclass and get_state helper"
  ```

---

### Task SKEL-3: Create web/state.py — build_state() startup loader

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/state.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_state.py`

- [ ] **Step 1: Write the failing test**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_state.py`:

  ```python
  import os
  import sys
  import tempfile
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  os.environ["LLM_PROVIDER"] = "none"
  os.environ["EMBED_MODEL"] = "none"
  os.environ["CHAT_MODEL"] = "none"
  os.environ.pop("RERANK_PROVIDER", None)
  os.environ["RERANK_PROVIDER"] = "none"

  import numpy as np
  import db


  class TestBuildState(unittest.TestCase):
      def setUp(self):
          self.tmp = tempfile.TemporaryDirectory()
          self.db_path = os.path.join(self.tmp.name, "knowledge.db")
          db.init_db(self.db_path)
          conn = db.get_connection(self.db_path)
          try:
              s1 = db.add_source(conn, "Test Book", "Author A", "t.txt", "ck_test1")
              cid = db.add_chunk(conn, s1, 0, "Philosophy of mind.", location="Ch 1")
              # No embeddings inserted — provider is "none", so matrix stays empty.
          finally:
              conn.close()

      def tearDown(self):
          self.tmp.cleanup()

      def test_build_state_returns_appstate(self):
          from web.state import build_state
          from web.deps import AppState

          st = build_state(self.db_path)
          self.assertIsInstance(st, AppState)

      def test_build_state_db_path_set(self):
          from web.state import build_state

          st = build_state(self.db_path)
          # build_state passes db_path through resolve_db_path; since our temp path
          # is already absolute it should be returned unchanged.
          self.assertEqual(st.db_path, self.db_path)

      def test_build_state_llm_not_none(self):
          from web.state import build_state

          st = build_state(self.db_path)
          self.assertIsNotNone(st.llm)

      def test_build_state_chunk_ids_is_ndarray(self):
          from web.state import build_state

          st = build_state(self.db_path)
          self.assertIsInstance(st.chunk_ids, np.ndarray)

      def test_build_state_embeddings_matrix_is_ndarray(self):
          from web.state import build_state

          st = build_state(self.db_path)
          self.assertIsInstance(st.embeddings_matrix, np.ndarray)

      def test_build_state_provider_none_leaves_empty_arrays(self):
          """With LLM_PROVIDER=none no embeddings are written, so arrays stay empty."""
          from web.state import build_state

          st = build_state(self.db_path)
          # provider is "none" → no semantic indexing → arrays empty
          self.assertEqual(st.chunk_ids.shape[0], 0)

      def test_build_state_usearch_index_none_when_no_provider(self):
          from web.state import build_state

          st = build_state(self.db_path)
          self.assertIsNone(st.usearch_index)


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_state -v
  ```

  Expected output:
  ```
  ERROR: test_build_state_returns_appstate ... ERROR
  ModuleNotFoundError: No module named 'web.state'
  ```

- [ ] **Step 3: Implement web/state.py**

  Create `/Users/aniketnamjoshi/knowledge-project/web/state.py`:

  ```python
  import os
  import sys
  from typing import Optional

  import numpy as np

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

  from db import (
      index_path_for,
      get_connection,
      get_all_embeddings_only,
      resolve_db_path,
      check_and_migrate_embeddings,
  )
  from llm_client import LLMClient
  from web.deps import AppState


  def build_state(db_path: Optional[str] = None) -> AppState:
      """Build and return the shared AppState for the web layer.

      Mirrors query.py main() lines 423-517 with no rich/stdin interaction.
      Called once per process at lifespan startup.  Also callable directly
      from test setUp so the full loader path is unit-testable.
      """
      resolved = resolve_db_path(db_path or os.getenv("DATABASE_PATH", "knowledge.db"))

      # Construct LLMClient once.  check_and_run_setup() inside __init__ is a
      # no-op when TESTING=true / PSYCHE_NONINTERACTIVE=1 / stdin is not a tty.
      llm = LLMClient()

      # Mirror query.py line 436: migrate embeddings if the configured model
      # changed since last ingest.  Skipped automatically in non-interactive /
      # testing environments by check_and_migrate_embeddings itself.
      check_and_migrate_embeddings(resolved, llm)

      # Preload search structures exactly as query.py lines 492-517.
      chunk_ids = np.array([], dtype=np.int32)
      embeddings_matrix = np.array([], dtype=np.float32)
      usearch_index = None

      if llm.provider != "none":
          index_path = index_path_for(resolved)
          try:
              from usearch.index import Index  # type: ignore

              if os.path.exists(index_path):
                  usearch_index = Index.restore(index_path)
          except Exception:
              usearch_index = None

          if usearch_index is None:
              conn = get_connection(resolved)
              try:
                  records = get_all_embeddings_only(conn)
              finally:
                  conn.close()
              valid = [r for r in records if r["embedding"] is not None]
              if valid:
                  chunk_ids = np.array([r["chunk_id"] for r in valid], dtype=np.int32)
                  embeddings_matrix = np.vstack([r["embedding"] for r in valid])

      return AppState(
          db_path=resolved,
          llm=llm,
          chunk_ids=chunk_ids,
          embeddings_matrix=embeddings_matrix,
          usearch_index=usearch_index,
      )
  ```

- [ ] **Step 4: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_state -v
  ```

  Expected output:
  ```
  test_build_state_chunk_ids_is_ndarray (tests.test_web_state.TestBuildState) ... ok
  test_build_state_db_path_set (tests.test_web_state.TestBuildState) ... ok
  test_build_state_embeddings_matrix_is_ndarray (tests.test_web_state.TestBuildState) ... ok
  test_build_state_llm_not_none (tests.test_web_state.TestBuildState) ... ok
  test_build_state_provider_none_leaves_empty_arrays (tests.test_web_state.TestBuildState) ... ok
  test_build_state_returns_appstate (tests.test_web_state.TestBuildState) ... ok
  test_build_state_usearch_index_none_when_no_provider (tests.test_web_state.TestBuildState) ... ok

  ----------------------------------------------------------------------
  Ran 7 tests in 0.XXXs

  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add web/state.py tests/test_web_state.py && git commit -m "SKEL-3: add web/state.py with build_state() startup loader"
  ```

---

### Task SKEL-4: Create web/app.py — FastAPI factory with lifespan and exception handler

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/app.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_app.py`

- [ ] **Step 1: Write the failing test**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_app.py`:

  ```python
  import os
  import sys
  import tempfile
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  os.environ["LLM_PROVIDER"] = "none"
  os.environ["EMBED_MODEL"] = "none"
  os.environ["CHAT_MODEL"] = "none"
  os.environ["RERANK_PROVIDER"] = "none"

  import numpy as np
  import db
  from fastapi.testclient import TestClient


  class FakeLLM:
      provider = "fake"
      chat_provider = "fake"
      embed_model = "fake-embed"
      chat_model = "fake-chat"


  class TestCreateApp(unittest.TestCase):
      def setUp(self):
          self.tmp = tempfile.TemporaryDirectory()
          self.db_path = os.path.join(self.tmp.name, "knowledge.db")
          db.init_db(self.db_path)

          from web.deps import AppState
          self.state = AppState(
              db_path=self.db_path,
              llm=FakeLLM(),
              chunk_ids=np.array([], dtype=np.int32),
              embeddings_matrix=np.array([], dtype=np.float32),
              usearch_index=None,
          )

      def tearDown(self):
          self.tmp.cleanup()

      def _make_client(self):
          from web.app import create_app

          app = create_app()
          app.state.psyche = self.state
          return TestClient(app)

      def test_create_app_returns_fastapi_instance(self):
          from fastapi import FastAPI
          from web.app import create_app

          app = create_app()
          self.assertIsInstance(app, FastAPI)

      def test_health_endpoint_returns_200(self):
          client = self._make_client()
          resp = client.get("/health")
          self.assertEqual(resp.status_code, 200)
          self.assertEqual(resp.json()["status"], "ok")

      def test_unknown_route_returns_404(self):
          client = self._make_client()
          resp = client.get("/nonexistent-route")
          self.assertEqual(resp.status_code, 404)

      def test_unhandled_exception_returns_500(self):
          from web.app import create_app
          from fastapi import APIRouter

          app = create_app()
          app.state.psyche = self.state

          router = APIRouter()

          @router.get("/boom")
          def boom():
              raise RuntimeError("test explosion")

          app.include_router(router)
          client = TestClient(app, raise_server_exceptions=False)
          resp = client.get("/boom")
          self.assertEqual(resp.status_code, 500)
          self.assertIn("detail", resp.json())

      def test_app_state_psyche_accessible(self):
          from web.app import create_app
          from web.deps import get_state
          from fastapi import Request
          from fastapi.testclient import TestClient

          app = create_app()
          app.state.psyche = self.state

          @app.get("/check-state")
          def check_state(request: Request):
              st = get_state(request)
              return {"db_path": st.db_path}

          client = TestClient(app)
          resp = client.get("/check-state")
          self.assertEqual(resp.status_code, 200)
          self.assertEqual(resp.json()["db_path"], self.db_path)

      def test_lifespan_skips_build_state_when_psyche_already_set(self):
          """Lifespan guard: if app.state.psyche is pre-set, build_state is NOT called."""
          import web.state as ws
          from web.app import create_app

          called = []
          original = ws.build_state

          def spy(_db_path=None):
              called.append(True)
              return original(_db_path)

          ws.build_state = spy
          try:
              app = create_app()
              app.state.psyche = self.state  # pre-inject
              with TestClient(app):
                  pass
              self.assertEqual(called, [], "build_state should not be called when psyche is pre-set")
          finally:
              ws.build_state = original


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_app -v
  ```

  Expected output:
  ```
  ERROR: test_create_app_returns_fastapi_instance ... ERROR
  ModuleNotFoundError: No module named 'web.app'
  ```

- [ ] **Step 3: Implement web/app.py**

  Create `/Users/aniketnamjoshi/knowledge-project/web/app.py`:

  ```python
  import os
  import sys
  from contextlib import asynccontextmanager

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

  from fastapi import FastAPI, Request
  from fastapi.responses import JSONResponse


  @asynccontextmanager
  async def lifespan(app: FastAPI):
      # Only call build_state if psyche has not already been injected (e.g. by tests).
      if not getattr(app.state, "psyche", None):
          from web.state import build_state

          app.state.psyche = build_state(os.getenv("DATABASE_PATH"))
      yield


  def create_app() -> FastAPI:
      """FastAPI application factory.

      Endpoint authors: add exactly ONE include_router() call per router module,
      kept in alphabetical order below the router imports.
      """
      app = FastAPI(title="Psyche Web API", version="0.1.0", lifespan=lifespan)

      # --- top-level exception handler (catch-all → 500) ---
      @app.exception_handler(Exception)
      async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
          return JSONResponse(status_code=500, content={"detail": str(exc)})

      # --- health check (no auth, no state required) ---
      @app.get("/health")
      async def health():
          return {"status": "ok"}

      # --- router registrations (alphabetical; parallel authors append here) ---
      # from web import routes_graph, routes_search, routes_sources, routes_system
      # app.include_router(routes_graph.router)
      # app.include_router(routes_search.router)
      # app.include_router(routes_sources.router)
      # app.include_router(routes_system.router)

      return app


  # Module-level app instance for uvicorn: uvicorn web.app:app
  app = create_app()
  ```

- [ ] **Step 4: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_app -v
  ```

  Expected output:
  ```
  test_app_state_psyche_accessible (tests.test_web_app.TestCreateApp) ... ok
  test_create_app_returns_fastapi_instance (tests.test_web_app.TestCreateApp) ... ok
  test_health_endpoint_returns_200 (tests.test_web_app.TestCreateApp) ... ok
  test_lifespan_skips_build_state_when_psyche_already_set (tests.test_web_app.TestCreateApp) ... ok
  test_unhandled_exception_returns_500 (tests.test_web_app.TestCreateApp) ... ok
  test_unknown_route_returns_404 (tests.test_web_app.TestCreateApp) ... ok

  ----------------------------------------------------------------------
  Ran 6 tests in 0.XXXs

  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add web/app.py tests/test_web_app.py && git commit -m "SKEL-4: add web/app.py factory with lifespan guard and exception handler"
  ```

---

### Task SKEL-5: Create tests/test_web_base.py — reusable WebTestCase fixture

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base_selftest.py`

- [ ] **Step 1: Write the failing test (self-test for the fixture)**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base_selftest.py`:

  ```python
  """Self-tests that verify WebTestCase builds a working client and seeded DB."""
  import os
  import sys
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  os.environ["LLM_PROVIDER"] = "none"
  os.environ["EMBED_MODEL"] = "none"
  os.environ["CHAT_MODEL"] = "none"
  os.environ["RERANK_PROVIDER"] = "none"


  class TestWebTestCaseFixture(unittest.TestCase):
      """These tests import WebTestCase to confirm it wires up correctly."""

      def _make_case(self):
          from tests.test_web_base import WebTestCase

          tc = WebTestCase()
          tc.setUp()
          return tc

      def test_client_is_created(self):
          from fastapi.testclient import TestClient

          tc = self._make_case()
          try:
              self.assertIsInstance(tc.client, TestClient)
          finally:
              tc.tearDown()

      def test_db_seeded_with_two_sources(self):
          import db

          tc = self._make_case()
          try:
              conn = db.get_connection(tc.db_path)
              try:
                  rows = conn.execute("SELECT COUNT(*) FROM sources").fetchone()
              finally:
                  conn.close()
              self.assertEqual(rows[0], 2)
          finally:
              tc.tearDown()

      def test_db_seeded_with_three_chunks(self):
          import db

          tc = self._make_case()
          try:
              conn = db.get_connection(tc.db_path)
              try:
                  rows = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
              finally:
                  conn.close()
              self.assertEqual(rows[0], 3)
          finally:
              tc.tearDown()

      def test_db_seeded_with_three_embeddings(self):
          import db

          tc = self._make_case()
          try:
              conn = db.get_connection(tc.db_path)
              try:
                  rows = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
              finally:
                  conn.close()
              self.assertEqual(rows[0], 3)
          finally:
              tc.tearDown()

      def test_state_llm_is_fakelllm(self):
          tc = self._make_case()
          try:
              self.assertEqual(tc.state.llm.provider, "fake")
          finally:
              tc.tearDown()

      def test_state_db_path_is_temp(self):
          import tempfile

          tc = self._make_case()
          try:
              # db_path should be inside a temp directory, not the system DB
              self.assertTrue(os.path.exists(tc.db_path))
              self.assertIn(tempfile.gettempdir(), tc.db_path)
          finally:
              tc.tearDown()

      def test_health_endpoint_reachable_via_fixture_client(self):
          tc = self._make_case()
          try:
              resp = tc.client.get("/health")
              self.assertEqual(resp.status_code, 200)
          finally:
              tc.tearDown()

      def test_concepts_seeded(self):
          import db

          tc = self._make_case()
          try:
              conn = db.get_connection(tc.db_path)
              try:
                  rows = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()
              finally:
                  conn.close()
              self.assertEqual(rows[0], 2)
          finally:
              tc.tearDown()

      def test_concept_links_seeded(self):
          import db

          tc = self._make_case()
          try:
              conn = db.get_connection(tc.db_path)
              try:
                  rows = conn.execute("SELECT COUNT(*) FROM concept_links").fetchone()
              finally:
                  conn.close()
              self.assertEqual(rows[0], 1)
          finally:
              tc.tearDown()

      def test_teardown_cleans_temp_dir(self):
          tc = self._make_case()
          db_path = tc.db_path
          tc.tearDown()
          self.assertFalse(os.path.exists(db_path))


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_base_selftest -v
  ```

  Expected output:
  ```
  ERROR: test_client_is_created ... ERROR
  ModuleNotFoundError: No module named 'tests.test_web_base'
  ```

- [ ] **Step 3: Implement tests/test_web_base.py**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`:

  ```python
  """Reusable base test case for all web endpoint tests.

  Usage:
      from tests.test_web_base import WebTestCase

      class TestMyRouter(WebTestCase):
          def test_something(self):
              resp = self.client.get("/my-route")
              self.assertEqual(resp.status_code, 200)
  """
  import os
  import sys
  import tempfile
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

  # Must be set before any psyche module is imported so LLMClient.check_and_run_setup
  # is a no-op and no network calls fire during test collection or setUp.
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  # Force pure-RRF reranking so flashrank never tries to download a model.
  os.environ.setdefault("RERANK_PROVIDER", "none")

  import numpy as np

  import db
  from fastapi.testclient import TestClient


  DIM = 8


  def fake_embedding(seed: int) -> list:
      """Deterministic stub embedding; never loads a real model."""
      rng = np.random.default_rng(seed)
      return rng.random(DIM, dtype=np.float32).tolist()


  class FakeLLM:
      """Offline stand-in for LLMClient.

      provider != 'none' so semantic code-paths exercise the numpy/usearch
      branches; generate_completion returns a fixed string so /chat tests
      work without any network.
      """

      provider = "fake"
      chat_provider = "fake"
      embed_model = "fake-embed"
      chat_model = "fake-chat"

      def get_embedding(self, text: str) -> list:
          return fake_embedding(abs(hash(text)) % 10000)

      def get_embeddings_batch(self, texts: list) -> list:
          return [self.get_embedding(t) for t in texts]

      def generate_completion(self, system_instruction: str, prompt: str) -> str:
          return "FAKE ANSWER"


  class WebTestCase(unittest.TestCase):
      """Base class for all web layer tests.

      Provides:
          self.db_path  — absolute path to a seeded temp SQLite DB
          self.state    — AppState with FakeLLM (no network, no real LLMClient)
          self.client   — fastapi.testclient.TestClient wired to create_app()
                         with self.state already injected on app.state.psyche

      Seed data:
          Sources   — "Meditations" (Marcus Aurelius), "Letters" (Seneca)
          Chunks    — 3 total with real embeddings (dim=8)
          Concepts  — "Stoicism", "Virtue"
          Links     — Stoicism → Virtue (emphasizes)
      """

      def setUp(self):
          self.tmp = tempfile.TemporaryDirectory()
          self.db_path = os.path.join(self.tmp.name, "knowledge.db")
          db.init_db(self.db_path)

          conn = db.get_connection(self.db_path)
          try:
              # Source 1: Meditations — 2 chunks + embeddings
              s1 = db.add_source(conn, "Meditations", "Marcus Aurelius", "m.txt", "ck_med")
              for i, text in enumerate(
                  ["On discipline and the will.", "Nature and the cosmos."]
              ):
                  cid = db.add_chunk(conn, s1, i, text, location=f"Book {i + 1}")
                  db.add_embedding(conn, cid, fake_embedding(i))

              # Source 2: Letters — 1 chunk + embedding
              s2 = db.add_source(conn, "Letters", "Seneca", "l.txt", "ck_let")
              cid = db.add_chunk(conn, s2, 0, "On the shortness of life.", location="Letter 1")
              db.add_embedding(conn, cid, fake_embedding(99))

              # Concepts and links
              db.add_concept(conn, "Stoicism", "A school of philosophy.", "Philosophy")
              db.add_concept(conn, "Virtue", "Moral excellence.", "Philosophy")
              db.add_concept_link(
                  conn,
                  "Stoicism",
                  "Virtue",
                  "emphasizes",
                  "Stoicism centers on virtue.",
              )
          finally:
              conn.close()

          # Build the usearch index from the seeded embeddings (real sibling .usearch file).
          db.build_or_update_usearch_index(self.db_path)

          # Construct AppState directly with FakeLLM so build_state() (and its
          # real LLMClient) is never invoked during tests.
          from web.deps import AppState

          usearch_index = None
          try:
              from usearch.index import Index  # type: ignore

              ip = db.index_path_for(self.db_path)
              if os.path.exists(ip):
                  usearch_index = Index.restore(ip)
          except Exception:
              usearch_index = None

          chunk_ids = np.array([], dtype=np.int32)
          matrix = np.array([], dtype=np.float32)
          if usearch_index is None:
              c = db.get_connection(self.db_path)
              try:
                  recs = db.get_all_embeddings_only(c)
              finally:
                  c.close()
              valid = [r for r in recs if r["embedding"] is not None]
              if valid:
                  chunk_ids = np.array([r["chunk_id"] for r in valid], dtype=np.int32)
                  matrix = np.vstack([r["embedding"] for r in valid])

          self.state = AppState(
              db_path=self.db_path,
              llm=FakeLLM(),
              chunk_ids=chunk_ids,
              embeddings_matrix=matrix,
              usearch_index=usearch_index,
          )

          import web.app

          app = web.app.create_app()
          # Inject state BEFORE TestClient enters context so lifespan guard finds
          # psyche already set and skips the real build_state() call entirely.
          app.state.psyche = self.state
          self.client = TestClient(app)

      def tearDown(self):
          self.client.close()
          self.tmp.cleanup()
  ```

- [ ] **Step 4: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_base_selftest -v
  ```

  Expected output:
  ```
  test_client_is_created (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_concept_links_seeded (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_concepts_seeded (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_db_seeded_with_three_chunks (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_db_seeded_with_three_embeddings (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_db_seeded_with_two_sources (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_health_endpoint_reachable_via_fixture_client (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_state_db_path_is_temp (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_state_llm_is_fakelllm (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok
  test_teardown_cleans_temp_dir (tests.test_web_base_selftest.TestWebTestCaseFixture) ... ok

  ----------------------------------------------------------------------
  Ran 10 tests in 0.XXXs

  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add tests/test_web_base.py tests/test_web_base_selftest.py && git commit -m "SKEL-5: add reusable WebTestCase fixture with seeded temp DB and FakeLLM"
  ```

---

### Task SKEL-6: Create web/server.py and wire the "psyche web" CLI subcommand

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/server.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/cli.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_server.py`

- [ ] **Step 1: Write the failing test**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_server.py`:

  ```python
  import os
  import sys
  import unittest

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  os.environ["LLM_PROVIDER"] = "none"
  os.environ["EMBED_MODEL"] = "none"
  os.environ["CHAT_MODEL"] = "none"
  os.environ["RERANK_PROVIDER"] = "none"


  class TestWebServerModule(unittest.TestCase):
      def test_server_module_importable(self):
          import web.server  # noqa: F401

          self.assertTrue(True)

      def test_server_has_main_function(self):
          import web.server

          self.assertTrue(callable(web.server.main))

      def test_server_main_accepts_no_args(self):
          """main() signature takes no positional args (reads from env/CLI)."""
          import inspect
          import web.server

          sig = inspect.signature(web.server.main)
          # All params should have defaults so main() can be called bare.
          for name, param in sig.parameters.items():
              self.assertIsNot(
                  param.default,
                  inspect.Parameter.empty,
                  f"Parameter '{name}' of web.server.main() has no default value",
              )

      def test_cli_includes_web_in_usage(self):
          """cli.py usage string must mention 'web'."""
          import ast

          cli_path = os.path.join(
              os.path.dirname(__file__), "..", "cli.py"
          )
          with open(cli_path) as f:
              source = f.read()
          self.assertIn("web", source)

      def test_cli_dispatches_web_subcommand(self):
          """cli.py must have an elif branch for the 'web' subcommand."""
          cli_path = os.path.join(
              os.path.dirname(__file__), "..", "cli.py"
          )
          with open(cli_path) as f:
              source = f.read()
          # Look for the dispatch pattern used by every other subcommand.
          self.assertIn('subcommand == "web"', source)


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run the test — confirm it FAILS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_server -v
  ```

  Expected output:
  ```
  ERROR: test_server_module_importable ... ERROR
  ModuleNotFoundError: No module named 'web.server'
  ...
  FAIL: test_cli_dispatches_web_subcommand ... FAIL
  AssertionError: 'subcommand == "web"' not found in cli.py source
  ```

- [ ] **Step 3: Implement web/server.py**

  Create `/Users/aniketnamjoshi/knowledge-project/web/server.py`:

  ```python
  """Entry point for `psyche web`.

  Reads DATABASE_PATH from the environment (set by cli.py via resolve_db_path),
  starts uvicorn, and opens a browser tab pointing at the local server.
  """
  import os
  import sys

  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


  def main(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
      """Start the Psyche web API server with uvicorn.

      Args:
          host:   Bind address (default 127.0.0.1).
          port:   TCP port (default 8000).
          reload: Enable uvicorn auto-reload for development (default False).
      """
      import uvicorn

      # Open the browser after a brief delay so the server has time to bind.
      # We do this in a daemon thread so it doesn't block uvicorn startup.
      import threading
      import time

      def _open_browser():
          time.sleep(1.2)
          import webbrowser

          webbrowser.open(f"http://{host}:{port}")

      threading.Thread(target=_open_browser, daemon=True).start()

      uvicorn.run(
          "web.app:app",
          host=host,
          port=port,
          reload=reload,
      )
  ```

- [ ] **Step 4: Wire the web subcommand into cli.py**

  In `/Users/aniketnamjoshi/knowledge-project/cli.py`, change the usage print line:

  ```python
      print("Usage: psyche [setup | ingest | query | chat | build-graph | guide | checkin | goal | experiment | log-metric | review | rules | compact-memory | connect | mem | start-mcp] [options]")
  ```
  to:
  ```python
      print("Usage: psyche [setup | ingest | query | chat | build-graph | guide | checkin | goal | experiment | log-metric | review | rules | compact-memory | connect | mem | start-mcp | web] [options]")
  ```

  Change the "Available commands" print line:

  ```python
          print("Available commands: setup, ingest, query, chat, build-graph, guide, checkin, goal, experiment, log-metric, review, rules, compact-memory, connect, mem, start-mcp")
  ```
  to:
  ```python
          print("Available commands: setup, ingest, query, chat, build-graph, guide, checkin, goal, experiment, log-metric, review, rules, compact-memory, connect, mem, start-mcp, web")
  ```

  Add the `elif subcommand == "web":` branch immediately before the final `else:` block (after the `start-mcp` branch):

  ```python
      elif subcommand == "web":
          import web.server
          web.server.main()
  ```

- [ ] **Step 5: Run the test — confirm it PASSES**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_server -v
  ```

  Expected output:
  ```
  test_cli_dispatches_web_subcommand (tests.test_web_server.TestWebServerModule) ... ok
  test_cli_includes_web_in_usage (tests.test_web_server.TestWebServerModule) ... ok
  test_server_has_main_function (tests.test_web_server.TestWebServerModule) ... ok
  test_server_main_accepts_no_args (tests.test_web_server.TestWebServerModule) ... ok
  test_server_module_importable (tests.test_web_server.TestWebServerModule) ... ok

  ----------------------------------------------------------------------
  Ran 5 tests in 0.XXXs

  OK
  ```

- [ ] **Step 6: Verify the full SKEL suite passes together**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_skel_deps tests.test_web_deps tests.test_web_state tests.test_web_app tests.test_web_base_selftest tests.test_web_server -v
  ```

  Expected output: all tests `ok`, final line `OK`.

- [ ] **Step 7: Commit**

  ```
  git add web/server.py cli.py tests/test_web_server.py && git commit -m "SKEL-6: add web/server.py uvicorn launcher and wire 'psyche web' CLI subcommand"
  ```

---

## Ingest endpoints

### Task INGEST-1: Wire deps, models, and the routes_sources router skeleton

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/__init__.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/deps.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/state.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/app.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/requirements.txt`
- Modify `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`

- [ ] **Step 1: Write the failing test — base fixture imports the skeleton without error**

```python
# tests/test_web_base_import.py  (temporary; removed after INGEST-1 passes)
import os
import sys
import unittest

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

class TestWebSkeletonImportable(unittest.TestCase):
    def test_deps_importable(self):
        from web.deps import AppState, get_state  # noqa: F401

    def test_app_factory_importable(self):
        from web.app import create_app  # noqa: F401
        app = create_app()
        self.assertIsNotNone(app)

    def test_routes_sources_importable(self):
        from web import routes_sources  # noqa: F401
        self.assertTrue(hasattr(routes_sources, "router"))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — confirm FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_base_import -v
```

Expected output (before files exist):
```
ModuleNotFoundError: No module named 'web'
```

- [ ] **Step 3: Add fastapi + uvicorn + python-multipart to requirements.txt and pyproject.toml, then create the skeleton files**

Append to `/Users/aniketnamjoshi/knowledge-project/requirements.txt`:
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
python-multipart>=0.0.9
httpx>=0.27.0
```

In `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`, add to `dependencies`:
```
"fastapi>=0.110.0",
"uvicorn[standard]>=0.29.0",
"python-multipart>=0.0.9",
```

And under `[tool.setuptools]` add:
```toml
packages = ["web"]
```

Create `/Users/aniketnamjoshi/knowledge-project/web/__init__.py`:
```python
```

Create `/Users/aniketnamjoshi/knowledge-project/web/deps.py`:
```python
import os
import sys
from dataclasses import dataclass, field
import numpy as np
from fastapi import Request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@dataclass
class AppState:
    db_path: str
    llm: object
    chunk_ids: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int32))
    embeddings_matrix: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float32))
    usearch_index: object = None


def get_state(request: Request) -> AppState:
    """Returns the shared AppState stored on app.state.psyche."""
    return request.app.state.psyche
```

Create `/Users/aniketnamjoshi/knowledge-project/web/state.py`:
```python
import os
import sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import (
    resolve_db_path,
    index_path_for,
    get_connection,
    get_all_embeddings_only,
    check_and_migrate_embeddings,
)
from llm_client import LLMClient
from web.deps import AppState


def build_state(db_path: str = None) -> AppState:
    """Builds shared AppState: one LLMClient, preloaded index/matrix. Mirrors query.py main()."""
    resolved = resolve_db_path(db_path or os.getenv("DATABASE_PATH", "knowledge.db"))

    llm = LLMClient()
    check_and_migrate_embeddings(resolved, llm)

    chunk_ids = np.array([], dtype=np.int32)
    embeddings_matrix = np.array([], dtype=np.float32)
    usearch_index = None

    if llm.provider != "none":
        index_path = index_path_for(resolved)
        try:
            from usearch.index import Index
            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None

    if usearch_index is None:
        conn = get_connection(resolved)
        try:
            records = get_all_embeddings_only(conn)
        finally:
            conn.close()
        valid = [r for r in records if r["embedding"] is not None]
        if valid:
            chunk_ids = np.array([r["chunk_id"] for r in valid], dtype=np.int32)
            embeddings_matrix = np.vstack([r["embedding"] for r in valid])

    return AppState(
        db_path=resolved,
        llm=llm,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        usearch_index=usearch_index,
    )
```

Create `/Users/aniketnamjoshi/knowledge-project/web/app.py`:
```python
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from web import routes_sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not getattr(app.state, "psyche", None):
        from web.state import build_state
        app.state.psyche = build_state(os.getenv("DATABASE_PATH"))
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Psyche Web API", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    app.include_router(routes_sources.router)

    return app


app = create_app()
```

Create `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`:
```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

from web.deps import get_state

router = APIRouter()
```

Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`:
```python
import os
import sys
import tempfile
import unittest
import numpy as np

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ.setdefault("RERANK_PROVIDER", "none")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from fastapi.testclient import TestClient

DIM = 8


def fake_embedding(seed: int) -> list:
    rng = np.random.default_rng(seed)
    return rng.random(DIM, dtype=np.float32).tolist()


class FakeLLM:
    """Offline stand-in for LLMClient. provider != 'none' so semantic paths run."""
    provider = "fake"
    chat_provider = "fake"
    embed_model = "fake-embed"
    chat_model = "fake-chat"

    def get_embedding(self, text: str) -> list:
        return fake_embedding(abs(hash(text)) % 10000)

    def get_embeddings_batch(self, texts: list) -> list:
        return [self.get_embedding(t) for t in texts]

    def generate_completion(self, system_instruction: str, prompt: str) -> str:
        return "FAKE ANSWER"


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        db.init_db(self.db_path)

        conn = db.get_connection(self.db_path)
        try:
            s1 = db.add_source(conn, "Meditations", "Marcus Aurelius", "m.txt", "ck_med")
            for i, t in enumerate(["On discipline and the will.", "Nature and the cosmos."]):
                cid = db.add_chunk(conn, s1, i, t, location=f"Book {i + 1}")
                db.add_embedding(conn, cid, fake_embedding(i))

            s2 = db.add_source(conn, "Letters", "Seneca", "l.txt", "ck_let")
            cid = db.add_chunk(conn, s2, 0, "On the shortness of life.", location="Letter 1")
            db.add_embedding(conn, cid, fake_embedding(99))

            db.add_concept(conn, "Stoicism", "A school of philosophy.", "Philosophy")
            db.add_concept(conn, "Virtue", "Moral excellence.", "Philosophy")
            db.add_concept_link(conn, "Stoicism", "Virtue", "emphasizes", "Stoicism centers on virtue.")
        finally:
            conn.close()

        db.build_or_update_usearch_index(self.db_path)

        # Build AppState directly with FakeLLM — bypasses real LLMClient and network.
        from web.deps import AppState
        from db import index_path_for, get_connection, get_all_embeddings_only

        usearch_index = None
        try:
            from usearch.index import Index
            ip = index_path_for(self.db_path)
            if os.path.exists(ip):
                usearch_index = Index.restore(ip)
        except Exception:
            usearch_index = None

        chunk_ids = np.array([], dtype=np.int32)
        matrix = np.array([], dtype=np.float32)
        if usearch_index is None:
            c = get_connection(self.db_path)
            try:
                recs = get_all_embeddings_only(c)
            finally:
                c.close()
            valid = [r for r in recs if r["embedding"] is not None]
            if valid:
                chunk_ids = np.array([r["chunk_id"] for r in valid], dtype=np.int32)
                matrix = np.vstack([r["embedding"] for r in valid])

        self.state = AppState(
            db_path=self.db_path,
            llm=FakeLLM(),
            chunk_ids=chunk_ids,
            embeddings_matrix=matrix,
            usearch_index=usearch_index,
        )

        import web.app
        self.app = web.app.create_app()
        self.app.state.psyche = self.state
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
```

- [ ] **Step 4: Run — confirm PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_base_import -v
```

Expected output:
```
test_app_factory_importable (tests.test_web_base_import.TestWebSkeletonImportable) ... ok
test_deps_importable (tests.test_web_base_import.TestWebSkeletonImportable) ... ok
test_routes_sources_importable (tests.test_web_base_import.TestWebSkeletonImportable) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add web/__init__.py web/deps.py web/state.py web/app.py web/routes_sources.py tests/test_web_base.py tests/test_web_base_import.py requirements.txt pyproject.toml && git commit -m "feat(web): add FastAPI package skeleton — deps, state loader, app factory, sources router stub"
```

---

### Task INGEST-2: GET /sources — list ingested sources with chunk counts

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_sources.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_sources.py
import os
import sys
import unittest

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ.setdefault("RERANK_PROVIDER", "none")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_web_base import WebTestCase


class TestGetSources(WebTestCase):
    def test_sources_returns_200(self):
        resp = self.client.get("/sources")
        self.assertEqual(resp.status_code, 200)

    def test_sources_returns_list(self):
        resp = self.client.get("/sources")
        data = resp.json()
        self.assertIsInstance(data, list)

    def test_sources_contains_seeded_titles(self):
        resp = self.client.get("/sources")
        titles = [s["title"] for s in resp.json()]
        self.assertIn("Meditations", titles)
        self.assertIn("Letters", titles)

    def test_sources_has_chunk_count(self):
        resp = self.client.get("/sources")
        meditations = next(s for s in resp.json() if s["title"] == "Meditations")
        self.assertEqual(meditations["chunk_count"], 2)
        letters = next(s for s in resp.json() if s["title"] == "Letters")
        self.assertEqual(letters["chunk_count"], 1)

    def test_sources_has_required_keys(self):
        resp = self.client.get("/sources")
        for src in resp.json():
            for key in ("id", "title", "author", "chunk_count"):
                self.assertIn(key, src)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — confirm FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources -v
```

Expected output:
```
ERROR: test_sources_returns_200 ... 404 Not Found (GET /sources not yet registered)
```

- [ ] **Step 3: Implement GET /sources in routes_sources.py**

```python
# web/routes_sources.py  (full file — replace existing stub)
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from web.deps import get_state
from db import get_connection

router = APIRouter()


# ── Response models ────────────────────────────────────────────────────────────

class SourceRecord(BaseModel):
    id: int
    title: str
    author: Optional[str]
    chunk_count: int


# ── GET /sources ───────────────────────────────────────────────────────────────

@router.get("/sources", response_model=List[SourceRecord])
def list_sources(request: Request):
    """Returns all ingested sources with per-source chunk counts."""
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, author FROM sources")
        rows = cur.fetchall()
        result = []
        for src_id, title, author in rows:
            cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_id = ?", (src_id,)
            )
            count = cur.fetchone()[0]
            result.append(
                SourceRecord(id=src_id, title=title, author=author, chunk_count=count)
            )
        return result
    finally:
        conn.close()
```

- [ ] **Step 4: Run — confirm PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources -v
```

Expected output:
```
test_sources_contains_seeded_titles (tests.test_web_sources.TestGetSources) ... ok
test_sources_has_chunk_count (tests.test_web_sources.TestGetSources) ... ok
test_sources_has_required_keys (tests.test_web_sources.TestGetSources) ... ok
test_sources_returns_200 (tests.test_web_sources.TestGetSources) ... ok
test_sources_returns_list (tests.test_web_sources.TestGetSources) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_sources.py tests/test_web_sources.py && git commit -m "feat(web): implement GET /sources — list ingested sources with chunk counts"
```

---

### Task INGEST-3: GET /ingest/status — SHA-256 dedup check

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_sources.py`

- [ ] **Step 1: Write the failing test — add to test_web_sources.py**

```python
# Append this class to tests/test_web_sources.py

class TestIngestStatus(WebTestCase):
    """GET /ingest/status?checksum=<sha256> — dedup check."""

    def test_status_known_checksum_found(self):
        # "ck_med" is the checksum seeded for Meditations in WebTestCase.setUp
        resp = self.client.get("/ingest/status", params={"checksum": "ck_med"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["already_ingested"])
        self.assertIsNotNone(data["source_id"])
        self.assertIsInstance(data["source_id"], int)

    def test_status_unknown_checksum_not_found(self):
        resp = self.client.get("/ingest/status", params={"checksum": "deadbeefdeadbeef"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["already_ingested"])
        self.assertIsNone(data["source_id"])

    def test_status_missing_checksum_returns_422(self):
        # FastAPI validates query param presence; no checksum -> 422 Unprocessable Entity
        resp = self.client.get("/ingest/status")
        self.assertEqual(resp.status_code, 422)

    def test_status_has_required_keys(self):
        resp = self.client.get("/ingest/status", params={"checksum": "ck_let"})
        data = resp.json()
        self.assertIn("already_ingested", data)
        self.assertIn("source_id", data)
```

- [ ] **Step 2: Run — confirm FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestIngestStatus -v
```

Expected output:
```
ERROR: test_status_known_checksum_found ... 404 Not Found (GET /ingest/status not yet registered)
```

- [ ] **Step 3: Add GET /ingest/status to routes_sources.py**

Add to `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py` (append after the `list_sources` handler, before EOF):

```python
# ── Response model for dedup check ────────────────────────────────────────────

class IngestStatusResponse(BaseModel):
    already_ingested: bool
    source_id: Optional[int]


# ── GET /ingest/status ─────────────────────────────────────────────────────────

@router.get("/ingest/status", response_model=IngestStatusResponse)
def ingest_status(checksum: str, request: Request):
    """Checks whether a file with this SHA-256 checksum has already been ingested."""
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        from db import check_checksum
        sid = check_checksum(conn, checksum)
        return IngestStatusResponse(already_ingested=sid is not None, source_id=sid)
    finally:
        conn.close()
```

- [ ] **Step 4: Run — confirm PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestIngestStatus -v
```

Expected output:
```
test_status_has_required_keys (tests.test_web_sources.TestIngestStatus) ... ok
test_status_known_checksum_found (tests.test_web_sources.TestIngestStatus) ... ok
test_status_missing_checksum_returns_422 (tests.test_web_sources.TestIngestStatus) ... ok
test_status_unknown_checksum_not_found (tests.test_web_sources.TestIngestStatus) ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_sources.py tests/test_web_sources.py && git commit -m "feat(web): implement GET /ingest/status — SHA-256 dedup lookup"
```

---

### Task INGEST-4: POST /ingest — ingest a server-side file path (JSON body, no-LLM path)

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_sources.py`

- [ ] **Step 1: Write the failing test — add to test_web_sources.py**

```python
# Append this class to tests/test_web_sources.py
import tempfile
import os as _os


class TestPostIngestLocalPath(WebTestCase):
    """POST /ingest with a JSON body containing a server-side file path."""

    def _write_txt(self, content: str) -> str:
        """Write a temp .txt file and return its absolute path."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            dir=self.tmp.name
        )
        f.write(content)
        f.close()
        return f.name

    def test_ingest_new_file_returns_200(self):
        path = self._write_txt("Philosophy is the love of wisdom. " * 40)
        resp = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp.status_code, 200)

    def test_ingest_new_file_returns_source_id_and_chunk_count(self):
        path = self._write_txt("Socrates knew he knew nothing. " * 40)
        resp = self.client.post("/ingest", json={"path": path})
        data = resp.json()
        self.assertIn("source_id", data)
        self.assertIn("chunk_count", data)
        self.assertIn("skipped", data)
        self.assertIsInstance(data["source_id"], int)
        self.assertGreater(data["chunk_count"], 0)
        self.assertFalse(data["skipped"])

    def test_ingest_same_file_twice_skips_second(self):
        path = self._write_txt("The unexamined life is not worth living. " * 40)
        resp1 = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp1.status_code, 200)
        resp2 = self.client.post("/ingest", json={"path": path})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["skipped"])
        self.assertEqual(data2["chunk_count"], 0)

    def test_ingest_force_reingest(self):
        path = self._write_txt("Virtue is its own reward. " * 40)
        resp1 = self.client.post("/ingest", json={"path": path})
        sid1 = resp1.json()["source_id"]
        resp2 = self.client.post("/ingest", json={"path": path, "force": True})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertFalse(data2["skipped"])
        self.assertGreater(data2["chunk_count"], 0)
        # After force re-ingest a new source_id is created
        self.assertIsInstance(data2["source_id"], int)

    def test_ingest_missing_path_returns_404(self):
        resp = self.client.post("/ingest", json={"path": "/nonexistent/file.txt"})
        self.assertEqual(resp.status_code, 404)

    def test_ingest_title_and_author_override(self):
        path = self._write_txt("The mind is everything. " * 40)
        resp = self.client.post(
            "/ingest",
            json={"path": path, "title": "MyBook", "author": "MyAuthor"}
        )
        self.assertEqual(resp.status_code, 200)
        # Verify it appears in sources with the custom title/author
        sources_resp = self.client.get("/sources")
        sources = sources_resp.json()
        match = next((s for s in sources if s["title"] == "MyBook"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["author"], "MyAuthor")

    def test_ingest_unsupported_ext_returns_400(self):
        f = tempfile.NamedTemporaryFile(
            suffix=".xyz", delete=False, dir=self.tmp.name
        )
        f.write(b"data")
        f.close()
        resp = self.client.post("/ingest", json={"path": f.name})
        self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: Run — confirm FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestPostIngestLocalPath -v
```

Expected output:
```
ERROR: test_ingest_new_file_returns_200 ... 404 Not Found  (POST /ingest not registered)
```

- [ ] **Step 3: Implement POST /ingest in routes_sources.py**

Add to `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py` (append after `IngestStatusResponse` block):

```python
# ── Request / Response models for ingest ──────────────────────────────────────

class IngestRequest(BaseModel):
    path: str
    title: Optional[str] = None
    author: Optional[str] = None
    force: bool = False


class IngestResponse(BaseModel):
    source_id: int
    chunk_count: int
    skipped: bool


# ── POST /ingest ───────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=IngestResponse)
def ingest_file(body: IngestRequest, request: Request):
    """Ingest a server-side file by absolute path.

    Steps mirror ingest.py main() but without rich/Console/sys.exit.
    After writing to SQLite the on-disk usearch index is rebuilt; the
    in-memory st.chunk_ids / st.embeddings_matrix are NOT refreshed —
    a process restart (or re-hit to the state loader) is required for
    newly ingested content to appear in search results.
    """
    from ingest import calculate_sha256, chunk_text, clean_title_from_filename
    from parsers import extract_text
    from db import (
        get_connection as _get_conn,
        check_checksum,
        add_source,
        add_chunk,
        add_embedding,
        build_or_update_usearch_index,
        remove_source,
    )

    st = get_state(request)
    path = body.path

    # ── 1. File existence / extension ────────────────────────────────────────
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        blocks = extract_text(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        # Unsupported extension
        raise HTTPException(status_code=400, detail=str(exc))

    if not blocks:
        raise HTTPException(status_code=400, detail="No text could be extracted from the file.")

    # ── 2. Checksum / dedup ───────────────────────────────────────────────────
    checksum = calculate_sha256(path)
    conn = _get_conn(st.db_path)
    try:
        existing_id = check_checksum(conn, checksum)
        if existing_id is not None and not body.force:
            return IngestResponse(source_id=existing_id, chunk_count=0, skipped=True)

        if existing_id is not None and body.force:
            remove_source(conn, existing_id, db_path=st.db_path)

        # ── 3. Chunk ──────────────────────────────────────────────────────────
        chunks = []
        for block in blocks:
            for c in chunk_text(block["text"]):
                chunks.append({"text": c, "location": block["location"]})

        if not chunks:
            raise HTTPException(status_code=400, detail="No text chunks created — content too short.")

        # ── 4. Embeddings (skipped when provider == 'none' or 'fake') ─────────
        embeddings = []
        if st.llm.provider not in ("none",):
            try:
                embeddings = st.llm.get_embeddings_batch([c["text"] for c in chunks])
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Embedding generation failed: {exc}")

        # ── 5. Persist ────────────────────────────────────────────────────────
        title = body.title or clean_title_from_filename(path)
        author = body.author or "Unknown"

        source_id = add_source(conn, title, author, path, checksum)
        for idx, chunk_data in enumerate(chunks):
            cid = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
            if embeddings:
                add_embedding(conn, cid, embeddings[idx])
    finally:
        conn.close()

    # ── 6. Rebuild on-disk index (process restart needed to refresh in-memory state) ─
    build_or_update_usearch_index(st.db_path)

    return IngestResponse(source_id=source_id, chunk_count=len(chunks), skipped=False)
```

- [ ] **Step 4: Run — confirm PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestPostIngestLocalPath -v
```

Expected output:
```
test_ingest_force_reingest (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_missing_path_returns_404 (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_new_file_returns_200 (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_new_file_returns_source_id_and_chunk_count (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_same_file_twice_skips_second (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_title_and_author_override (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_unsupported_ext_returns_400 (tests.test_web_sources.TestPostIngestLocalPath) ... ok

----------------------------------------------------------------------
Ran 7 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_sources.py tests/test_web_sources.py && git commit -m "feat(web): implement POST /ingest — server-side file path ingest with dedup and force re-ingest"
```

---

### Task INGEST-5: POST /ingest — multipart file upload path

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_sources.py`

- [ ] **Step 1: Write the failing test — add to test_web_sources.py**

```python
# Append this class to tests/test_web_sources.py

class TestPostIngestUpload(WebTestCase):
    """POST /ingest/upload — multipart UploadFile path."""

    def test_upload_txt_returns_200(self):
        content = b"Every moment think steadily as a Roman. " * 40
        resp = self.client.post(
            "/ingest/upload",
            files={"file": ("stoic.txt", content, "text/plain")},
        )
        self.assertEqual(resp.status_code, 200)

    def test_upload_returns_source_id_and_chunk_count(self):
        content = b"He who fears death will never do anything worthy of a living man. " * 40
        resp = self.client.post(
            "/ingest/upload",
            files={"file": ("seneca.txt", content, "text/plain")},
        )
        data = resp.json()
        self.assertIn("source_id", data)
        self.assertIn("chunk_count", data)
        self.assertIn("skipped", data)
        self.assertGreater(data["chunk_count"], 0)
        self.assertFalse(data["skipped"])

    def test_upload_same_content_twice_skips_second(self):
        content = b"Loss is nothing else but change. " * 40
        resp1 = self.client.post(
            "/ingest/upload",
            files={"file": ("marcus1.txt", content, "text/plain")},
        )
        self.assertEqual(resp1.status_code, 200)
        resp2 = self.client.post(
            "/ingest/upload",
            files={"file": ("marcus2.txt", content, "text/plain")},
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["skipped"])

    def test_upload_unsupported_ext_returns_400(self):
        resp = self.client.post(
            "/ingest/upload",
            files={"file": ("data.xyz", b"bytes", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_title_and_author_form_fields(self):
        content = b"The obstacle is the way. " * 40
        resp = self.client.post(
            "/ingest/upload",
            files={"file": ("obstacle.txt", content, "text/plain")},
            data={"title": "Obstacle Book", "author": "Ryan Holiday"},
        )
        self.assertEqual(resp.status_code, 200)
        sources = self.client.get("/sources").json()
        match = next((s for s in sources if s["title"] == "Obstacle Book"), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["author"], "Ryan Holiday")
```

- [ ] **Step 2: Run — confirm FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestPostIngestUpload -v
```

Expected output:
```
ERROR: test_upload_txt_returns_200 ... 404 Not Found  (POST /ingest/upload not registered)
```

- [ ] **Step 3: Add POST /ingest/upload to routes_sources.py**

Add to `/Users/aniketnamjoshi/knowledge-project/web/routes_sources.py` after the `ingest_file` handler:

```python
# ── POST /ingest/upload ────────────────────────────────────────────────────────

@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    request: Request,
    file: "UploadFile" = None,
    title: Optional[str] = None,
    author: Optional[str] = None,
    force: bool = False,
):
    """Ingest a file sent as a multipart upload.

    The uploaded bytes are written to a temp file, then the same pipeline
    as POST /ingest runs, and the temp file is cleaned up afterwards.
    """
    import tempfile as _tempfile
    from fastapi import UploadFile as _UploadFile
    from ingest import calculate_sha256, chunk_text, clean_title_from_filename
    from parsers import extract_text
    from db import (
        get_connection as _get_conn,
        check_checksum,
        add_source,
        add_chunk,
        add_embedding,
        build_or_update_usearch_index,
        remove_source,
    )

    if file is None:
        raise HTTPException(status_code=422, detail="A file must be supplied as multipart field 'file'.")

    original_filename = file.filename or "upload.txt"
    ext = os.path.splitext(original_filename)[1].lower()
    if not ext:
        ext = ".txt"

    # Write upload to a named temp file so extract_text (which needs a path) works.
    with _tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        st = get_state(request)

        try:
            blocks = extract_text(tmp_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if not blocks:
            raise HTTPException(status_code=400, detail="No text could be extracted from the uploaded file.")

        checksum = calculate_sha256(tmp_path)
        conn = _get_conn(st.db_path)
        try:
            existing_id = check_checksum(conn, checksum)
            if existing_id is not None and not force:
                return IngestResponse(source_id=existing_id, chunk_count=0, skipped=True)

            if existing_id is not None and force:
                remove_source(conn, existing_id, db_path=st.db_path)

            chunks = []
            for block in blocks:
                for c in chunk_text(block["text"]):
                    chunks.append({"text": c, "location": block["location"]})

            if not chunks:
                raise HTTPException(status_code=400, detail="No text chunks created — content too short.")

            embeddings = []
            if st.llm.provider not in ("none",):
                try:
                    embeddings = st.llm.get_embeddings_batch([c["text"] for c in chunks])
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"Embedding generation failed: {exc}")

            resolved_title = title or clean_title_from_filename(original_filename)
            resolved_author = author or "Unknown"

            source_id = add_source(conn, resolved_title, resolved_author, original_filename, checksum)
            for idx, chunk_data in enumerate(chunks):
                cid = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
                if embeddings:
                    add_embedding(conn, cid, embeddings[idx])
        finally:
            conn.close()

        build_or_update_usearch_index(st.db_path)
        return IngestResponse(source_id=source_id, chunk_count=len(chunks), skipped=False)

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
```

Also add the `UploadFile` import at the top of the file (add after the existing imports):

```python
from fastapi import UploadFile
```

- [ ] **Step 4: Run — confirm PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources.TestPostIngestUpload -v
```

Expected output:
```
test_upload_returns_source_id_and_chunk_count (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_same_content_twice_skips_second (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_title_and_author_form_fields (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_txt_returns_200 (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_unsupported_ext_returns_400 (tests.test_web_sources.TestPostIngestUpload) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_sources.py tests/test_web_sources.py && git commit -m "feat(web): implement POST /ingest/upload — multipart file upload ingest"
```

---

### Task INGEST-6: Full suite smoke-run and cleanup of temporary bootstrap test

**Files:**
- Delete `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base_import.py`

- [ ] **Step 1: Write the "failing" gate — entire sources test file must be green, bootstrap helper removed**

The bootstrap file `tests/test_web_base_import.py` was a temporary scaffold to drive INGEST-1. Now that the real tests cover the same surface area, delete it to keep the suite clean.

```
rm /Users/aniketnamjoshi/knowledge-project/tests/test_web_base_import.py
```

- [ ] **Step 2: Run — confirm ALL sources tests pass together**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_sources -v
```

Expected output:
```
test_ingest_force_reingest (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_missing_path_returns_404 (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_new_file_returns_200 (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_new_file_returns_source_id_and_chunk_count (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_same_file_twice_skips_second (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_title_and_author_override (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_ingest_unsupported_ext_returns_400 (tests.test_web_sources.TestPostIngestLocalPath) ... ok
test_sources_contains_seeded_titles (tests.test_web_sources.TestGetSources) ... ok
test_sources_has_chunk_count (tests.test_web_sources.TestGetSources) ... ok
test_sources_has_required_keys (tests.test_web_sources.TestGetSources) ... ok
test_sources_returns_200 (tests.test_web_sources.TestGetSources) ... ok
test_sources_returns_list (tests.test_web_sources.TestGetSources) ... ok
test_status_has_required_keys (tests.test_web_sources.TestIngestStatus) ... ok
test_status_known_checksum_found (tests.test_web_sources.TestIngestStatus) ... ok
test_status_missing_checksum_returns_422 (tests.test_web_sources.TestIngestStatus) ... ok
test_status_unknown_checksum_not_found (tests.test_web_sources.TestIngestStatus) ... ok
test_upload_returns_source_id_and_chunk_count (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_same_content_twice_skips_second (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_title_and_author_form_fields (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_txt_returns_200 (tests.test_web_sources.TestPostIngestUpload) ... ok
test_upload_unsupported_ext_returns_400 (tests.test_web_sources.TestPostIngestUpload) ... ok

----------------------------------------------------------------------
Ran 21 tests in 0.XXXs

OK
```

- [ ] **Step 3: Commit removal of bootstrap file**

```
git add -u tests/test_web_base_import.py && git commit -m "chore(tests): remove temporary bootstrap import test superseded by test_web_sources"
```

---

## Search & chat endpoints

### Task SEARCH-1: Foundation files — deps, state, app factory, and base test fixture

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/__init__.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/deps.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/state.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/app.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`

- [ ] **Step 1: Write failing smoke test that imports the not-yet-existing modules**

`/Users/aniketnamjoshi/knowledge-project/tests/test_web_base.py`:

```python
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["RERANK_PROVIDER"] = "none"

import db

from fastapi.testclient import TestClient

DIM = 8


def fake_embedding(seed):
    rng = np.random.default_rng(seed)
    return rng.random(DIM, dtype=np.float32).tolist()


class FakeLLM:
    """Offline stand-in for LLMClient.

    provider != 'none' so semantic search paths execute;
    chat_model is set so the /chat synthesis branch is exercised without
    hitting any real network.
    """

    provider = "fake"
    chat_provider = "fake"
    embed_model = "fake-embed"
    chat_model = "fake-chat"

    def get_embedding(self, text):
        return fake_embedding(abs(hash(text)) % 10000)

    def get_embeddings_batch(self, texts):
        return [self.get_embedding(t) for t in texts]

    def generate_completion(self, system_instruction, prompt):
        return "FAKE ANSWER"


class WebTestCase(unittest.TestCase):
    """Reusable base class for all web endpoint tests.

    Sets up a real temp SQLite DB seeded with known data, builds AppState
    with FakeLLM, and wires it into a TestClient so every subclass gets a
    clean, deterministic, fully-offline HTTP client.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")

        db.init_db(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            # Source 1: two chunks with embeddings
            s1 = db.add_source(conn, "Meditations", "Marcus Aurelius", "m.txt", "ck_med")
            for i, text in enumerate(
                ["On discipline and the will.", "Nature and the cosmos."]
            ):
                cid = db.add_chunk(conn, s1, i, text, location=f"Book {i + 1}")
                db.add_embedding(conn, cid, fake_embedding(i))

            # Source 2: one chunk with embedding
            s2 = db.add_source(conn, "Letters", "Seneca", "l.txt", "ck_let")
            cid = db.add_chunk(conn, s2, 0, "On the shortness of life.", location="Letter 1")
            db.add_embedding(conn, cid, fake_embedding(99))

            # Concepts + link (used by graph and chat context tests)
            db.add_concept(conn, "Stoicism", "A school of philosophy.", "Philosophy")
            db.add_concept(conn, "Virtue", "Moral excellence.", "Philosophy")
            db.add_concept_link(conn, "Stoicism", "Virtue", "emphasizes", "Stoicism centers on virtue.")
        finally:
            conn.close()

        # Build real usearch sibling index (falls back gracefully if usearch absent)
        try:
            db.build_or_update_usearch_index(self.db_path)
        except Exception:
            pass

        # Build AppState directly — bypasses build_state's real LLMClient
        from web.deps import AppState
        from db import index_path_for, get_connection, get_all_embeddings_only

        usearch_index = None
        try:
            from usearch.index import Index

            ip = index_path_for(self.db_path)
            if os.path.exists(ip):
                usearch_index = Index.restore(ip)
        except Exception:
            usearch_index = None

        chunk_ids = np.array([], dtype=np.int32)
        matrix = np.array([], dtype=np.float32)
        if usearch_index is None:
            c = get_connection(self.db_path)
            try:
                recs = get_all_embeddings_only(c)
            finally:
                c.close()
            chunk_ids = np.array(
                [r["chunk_id"] for r in recs if r["embedding"] is not None],
                dtype=np.int32,
            )
            vecs = [r["embedding"] for r in recs if r["embedding"] is not None]
            if vecs:
                matrix = np.vstack(vecs)

        self.state = AppState(
            db_path=self.db_path,
            llm=FakeLLM(),
            chunk_ids=chunk_ids,
            embeddings_matrix=matrix,
            usearch_index=usearch_index,
        )

        import web.app

        app = web.app.create_app()
        # Override the lifespan-loaded state with our deterministic fake state
        app.state.psyche = self.state
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()


class TestBaseFixtureImports(WebTestCase):
    """Smoke-tests that the foundation modules import and the fixture builds."""

    def test_app_state_has_db_path(self):
        self.assertEqual(self.state.db_path, self.db_path)

    def test_app_state_has_fake_llm(self):
        self.assertEqual(self.state.llm.provider, "fake")

    def test_client_can_reach_unknown_route(self):
        # Any route returns non-500 (404 is fine; proves the app is wired)
        resp = self.client.get("/nonexistent-route-check")
        self.assertNotEqual(resp.status_code, 500)
```

- [ ] **Step 2: Run the test — confirm it FAILS (modules not yet created)**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_base -v
```

Expected output:
```
ERROR: test_app_state_has_db_path (tests.test_web_base.TestBaseFixtureImports)
...
ModuleNotFoundError: No module named 'web'
```

- [ ] **Step 3: Create `web/__init__.py`**

`/Users/aniketnamjoshi/knowledge-project/web/__init__.py`:

```python
# web — FastAPI layer for Psyche
```

- [ ] **Step 4: Create `web/deps.py`**

`/Users/aniketnamjoshi/knowledge-project/web/deps.py`:

```python
import os
import sys
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import Request
from pydantic import BaseModel


@dataclass
class AppState:
    """Shared, read-only application state loaded once at startup."""

    db_path: str
    llm: object  # LLMClient or compatible duck-type (e.g. FakeLLM in tests)
    chunk_ids: np.ndarray
    embeddings_matrix: np.ndarray
    usearch_index: object  # usearch.index.Index or None


def get_state(request: Request) -> AppState:
    """Retrieves the shared AppState attached to the application by the lifespan loader."""
    return request.app.state.psyche


class ErrorResponse(BaseModel):
    detail: str
```

- [ ] **Step 5: Create `web/state.py`**

`/Users/aniketnamjoshi/knowledge-project/web/state.py`:

```python
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import (
    get_connection,
    get_all_embeddings_only,
    index_path_for,
    check_and_migrate_embeddings,
    resolve_db_path,
)
from llm_client import LLMClient
from web.deps import AppState


def build_state(db_path: str = None) -> AppState:
    """Constructs AppState by mirroring query.py startup (lines 423-517).

    Never calls sys.exit — missing/empty DB is surfaced via endpoint-level
    errors; the lifespan guard below prevents double-loading in tests.
    """
    resolved = resolve_db_path(db_path or os.getenv("DATABASE_PATH", "knowledge.db"))

    llm = LLMClient()

    # Migrate embeddings if the provider changed (no-op in TESTING mode)
    check_and_migrate_embeddings(resolved, llm)

    chunk_ids = np.array([], dtype=np.int32)
    embeddings_matrix = np.array([], dtype=np.float32)
    usearch_index = None

    if llm.provider != "none":
        index_path = index_path_for(resolved)
        try:
            from usearch.index import Index

            if os.path.exists(index_path):
                usearch_index = Index.restore(index_path)
        except Exception:
            usearch_index = None

        if usearch_index is None:
            conn = get_connection(resolved)
            try:
                records = get_all_embeddings_only(conn)
            finally:
                conn.close()

            chunk_ids = np.array(
                [r["chunk_id"] for r in records if r["embedding"] is not None],
                dtype=np.int32,
            )
            vecs = [r["embedding"] for r in records if r["embedding"] is not None]
            if vecs:
                embeddings_matrix = np.vstack(vecs)

    return AppState(
        db_path=resolved,
        llm=llm,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        usearch_index=usearch_index,
    )
```

- [ ] **Step 6: Create `web/app.py`**

`/Users/aniketnamjoshi/knowledge-project/web/app.py`:

```python
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from web.state import build_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Skip loading when tests have already injected app.state.psyche
    if not getattr(app.state, "psyche", None):
        app.state.psyche = build_state(os.getenv("DATABASE_PATH"))
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Psyche", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # Routers registered here in alphabetical order; each task adds one line.
    from web import routes_search

    app.include_router(routes_search.router)

    return app


# Module-level instance for uvicorn: `uvicorn web.app:app`
app = create_app()
```

- [ ] **Step 7: Run the test — confirm it PASSES**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_base -v
```

Expected output:
```
test_app_state_has_db_path (tests.test_web_base.TestBaseFixtureImports) ... ok
test_app_state_has_fake_llm (tests.test_web_base.TestBaseFixtureImports) ... ok
test_client_can_reach_unknown_route (tests.test_web_base.TestBaseFixtureImports) ... ok

----------------------------------------------------------------------
Ran 3 tests in ...s

OK
```

- [ ] **Step 8: Commit**

```
git add web/__init__.py web/deps.py web/state.py web/app.py tests/test_web_base.py && git commit -m "feat(web): add foundation package — deps, state, app factory, base test fixture"
```

---

### Task SEARCH-2: POST /search — router stub, Pydantic models, and core behaviour tests

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/routes_search.py`
- Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_search.py`

- [ ] **Step 1: Write failing tests for POST /search**

`/Users/aniketnamjoshi/knowledge-project/tests/test_web_search.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TESTING"] = "true"
os.environ["PSYCHE_NONINTERACTIVE"] = "1"
os.environ["RERANK_PROVIDER"] = "none"

from tests.test_web_base import WebTestCase


class TestSearchEndpoint(WebTestCase):
    """Tests for POST /search."""

    # ------------------------------------------------------------------
    # Happy-path: basic search returns correct structure
    # ------------------------------------------------------------------

    def test_search_returns_200(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        self.assertEqual(resp.status_code, 200)

    def test_search_response_is_list(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data, list)

    def test_search_result_has_required_keys(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertTrue(len(data) > 0, "Expected at least one result")
        item = data[0]
        for key in ("chunk_id", "text", "location", "source_title", "source_author", "score"):
            self.assertIn(key, item, f"Missing key: {key}")

    def test_search_score_is_float(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data[0]["score"], float)

    def test_search_chunk_id_is_int(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        data = resp.json()
        self.assertIsInstance(data[0]["chunk_id"], int)

    def test_search_result_contains_seeded_source(self):
        resp = self.client.post("/search", json={"query_text": "discipline"})
        titles = [r["source_title"] for r in resp.json()]
        self.assertIn("Meditations", titles)

    # ------------------------------------------------------------------
    # limit parameter
    # ------------------------------------------------------------------

    def test_search_default_limit_is_five(self):
        # Seeded DB has 3 chunks; default limit=5 means we get all 3 back
        resp = self.client.post("/search", json={"query_text": "life"})
        data = resp.json()
        self.assertLessEqual(len(data), 5)

    def test_search_custom_limit_respected(self):
        resp = self.client.post("/search", json={"query_text": "life", "limit": 1})
        data = resp.json()
        self.assertEqual(len(data), 1)

    def test_search_limit_zero_uses_default(self):
        # limit=0 is falsy; endpoint should fall back to default (5)
        resp = self.client.post("/search", json={"query_text": "life", "limit": 0})
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # Validation errors
    # ------------------------------------------------------------------

    def test_search_missing_query_text_returns_422(self):
        resp = self.client.post("/search", json={})
        self.assertEqual(resp.status_code, 422)

    def test_search_empty_query_text_returns_400(self):
        resp = self.client.post("/search", json={"query_text": ""})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Results ordering: scores should be descending
    # ------------------------------------------------------------------

    def test_search_results_ordered_by_score_descending(self):
        resp = self.client.post("/search", json={"query_text": "stoic philosophy"})
        data = resp.json()
        if len(data) > 1:
            scores = [r["score"] for r in data]
            self.assertEqual(scores, sorted(scores, reverse=True))
```

- [ ] **Step 2: Run the tests — confirm they FAIL (route does not exist)**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search -v
```

Expected output (representative):
```
ERROR: test_search_returns_200 (tests.test_web_search.TestSearchEndpoint)
...
ImportError: cannot import name 'routes_search' from 'web'
```

- [ ] **Step 3: Create `web/routes_search.py` with the /search implementation**

`/Users/aniketnamjoshi/knowledge-project/web/routes_search.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from query import perform_hybrid_search
from web.deps import get_state

router = APIRouter()

_DEFAULT_LIMIT = 5


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query_text: str
    limit: int = _DEFAULT_LIMIT


class SearchResult(BaseModel):
    chunk_id: int
    text: str
    location: str | None
    source_title: str
    source_author: str
    score: float


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------


@router.post("/search", response_model=list[SearchResult])
def search(req: SearchRequest, request: Request) -> list[SearchResult]:
    """Hybrid search (semantic + keyword RRF) over ingested chunks.

    Returns ranked passages without LLM synthesis.
    """
    if not req.query_text.strip():
        raise HTTPException(status_code=400, detail="query_text must not be empty")

    limit = req.limit if req.limit else _DEFAULT_LIMIT
    st = get_state(request)

    raw = perform_hybrid_search(
        st.db_path,
        req.query_text,
        st.chunk_ids,
        st.embeddings_matrix,
        st.llm,
        usearch_index=st.usearch_index,
        limit=limit,
    )

    return [
        SearchResult(
            chunk_id=chunk["chunk_id"],
            text=chunk["text"],
            location=chunk.get("location"),
            source_title=chunk["source_title"],
            source_author=chunk["source_author"],
            score=float(score),
        )
        for chunk, score in raw
    ]
```

- [ ] **Step 4: Run the tests — confirm all PASS**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search -v
```

Expected output:
```
test_search_chunk_id_is_int (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_custom_limit_respected (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_default_limit_is_five (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_empty_query_text_returns_400 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_limit_zero_uses_default (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_missing_query_text_returns_422 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_response_is_list (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_result_contains_seeded_source (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_result_has_required_keys (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_results_ordered_by_score_descending (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_returns_200 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_score_is_float (tests.test_web_search.TestSearchEndpoint) ... ok

----------------------------------------------------------------------
Ran 12 tests in ...s

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_search.py tests/test_web_search.py && git commit -m "feat(web): add POST /search endpoint with hybrid search and TDD tests"
```

---

### Task SEARCH-3: POST /chat — synthesis path, offline-mode 503, and citation structure

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_search.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_search.py`

- [ ] **Step 1: Append failing /chat tests to the test file**

Add the class below at the bottom of `/Users/aniketnamjoshi/knowledge-project/tests/test_web_search.py`:

```python
class TestChatEndpoint(WebTestCase):
    """Tests for POST /chat."""

    # ------------------------------------------------------------------
    # Happy path: FakeLLM.provider == 'fake' and chat_model == 'fake-chat'
    # Both are truthy non-'none' strings, so synthesis must run.
    # ------------------------------------------------------------------

    def test_chat_returns_200(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        self.assertEqual(resp.status_code, 200)

    def test_chat_response_has_answer_key(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIn("answer", data)

    def test_chat_response_has_sources_key(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIn("sources", data)

    def test_chat_sources_is_list(self):
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertIsInstance(data["sources"], list)

    def test_chat_answer_is_fake_answer(self):
        # FakeLLM.generate_completion always returns "FAKE ANSWER"
        resp = self.client.post("/chat", json={"query_text": "What is discipline?"})
        data = resp.json()
        self.assertEqual(data["answer"], "FAKE ANSWER")

    def test_chat_sources_have_required_keys(self):
        resp = self.client.post("/chat", json={"query_text": "discipline cosmos"})
        data = resp.json()
        sources = data["sources"]
        self.assertTrue(len(sources) > 0)
        for key in ("chunk_id", "text", "location", "source_title", "source_author", "score"):
            self.assertIn(key, sources[0], f"Missing key in source: {key}")

    def test_chat_source_scores_ordered_descending(self):
        resp = self.client.post("/chat", json={"query_text": "discipline cosmos"})
        sources = resp.json()["sources"]
        if len(sources) > 1:
            scores = [s["score"] for s in sources]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_chat_limit_restricts_sources(self):
        resp = self.client.post("/chat", json={"query_text": "life", "limit": 1})
        sources = resp.json()["sources"]
        self.assertLessEqual(len(sources), 1)

    def test_chat_default_limit_is_five(self):
        resp = self.client.post("/chat", json={"query_text": "life"})
        sources = resp.json()["sources"]
        self.assertLessEqual(len(sources), 5)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_chat_missing_query_returns_422(self):
        resp = self.client.post("/chat", json={})
        self.assertEqual(resp.status_code, 422)

    def test_chat_empty_query_returns_400(self):
        resp = self.client.post("/chat", json={"query_text": ""})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Offline mode: patch llm so provider or chat_model is 'none'
    # ------------------------------------------------------------------

    def test_chat_returns_503_when_provider_is_none(self):
        original_provider = self.state.llm.provider
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.provider = "none"
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            self.assertEqual(resp.status_code, 503)
        finally:
            self.state.llm.provider = original_provider
            self.state.llm.chat_model = original_chat_model

    def test_chat_returns_503_when_chat_model_is_none(self):
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            self.assertEqual(resp.status_code, 503)
        finally:
            self.state.llm.chat_model = original_chat_model

    def test_chat_503_detail_mentions_provider(self):
        original_provider = self.state.llm.provider
        original_chat_model = self.state.llm.chat_model
        try:
            self.state.llm.provider = "none"
            self.state.llm.chat_model = "none"
            resp = self.client.post("/chat", json={"query_text": "discipline"})
            data = resp.json()
            self.assertIn("detail", data)
            self.assertIsInstance(data["detail"], str)
        finally:
            self.state.llm.provider = original_provider
            self.state.llm.chat_model = original_chat_model
```

- [ ] **Step 2: Run the new tests — confirm they FAIL (/chat route does not exist yet)**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search.TestChatEndpoint -v
```

Expected output:
```
FAIL: test_chat_returns_200 (tests.test_web_search.TestChatEndpoint)
AssertionError: 404 != 200
...
Ran 13 tests in ...s

FAILED (failures=13)
```

- [ ] **Step 3: Add the /chat endpoint to `web/routes_search.py`**

Append to `/Users/aniketnamjoshi/knowledge-project/web/routes_search.py` (after the existing `/search` handler and its models — add the new models and handler below the `SearchResult` class):

```python
# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

from query import format_context, retrieve_concept_context
from db import get_connection


class ChatRequest(BaseModel):
    query_text: str
    limit: int = _DEFAULT_LIMIT


class ChatResponse(BaseModel):
    answer: str
    sources: list[SearchResult]


_SYSTEM_INSTRUCTION = (
    "You are a helpful knowledge assistant. Synthesize a detailed, clear answer based on "
    "the retrieved context chunks below. You must ground your answers strictly in the "
    "provided context. In your answer, you MUST explicitly cite your sources and page "
    "numbers/chapters whenever stating facts from them (e.g. [Title, Page X] or "
    "[Title, Chapter Y]). The Source identifier and location information is provided at "
    "the start of each context block. If the answer cannot be found in the context, be "
    "honest and state that you do not have enough information in the ingested documents "
    "to answer."
)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Hybrid search followed by LLM synthesis (RAG).

    Returns 503 when no chat-capable provider is configured — caller should
    fall back to pure retrieval via POST /search.
    """
    if not req.query_text.strip():
        raise HTTPException(status_code=400, detail="query_text must not be empty")

    limit = req.limit if req.limit else _DEFAULT_LIMIT
    st = get_state(request)

    # Guard: refuse synthesis when LLM chat is unavailable
    if st.llm.provider == "none" or getattr(st.llm, "chat_model", "none") == "none":
        raise HTTPException(
            status_code=503,
            detail=(
                f"No chat-capable provider configured "
                f"(provider={st.llm.provider!r}, "
                f"chat_model={getattr(st.llm, 'chat_model', 'none')!r}). "
                "Use POST /search for retrieval-only results."
            ),
        )

    # 1. Hybrid search
    raw = perform_hybrid_search(
        st.db_path,
        req.query_text,
        st.chunk_ids,
        st.embeddings_matrix,
        st.llm,
        usearch_index=st.usearch_index,
        limit=limit,
    )

    # 2. Format retrieval context
    context_str = format_context(raw, top_n=limit)

    # 3. Optionally enrich with concept-graph context (mirrors query.py lines 601-606)
    conn = get_connection(st.db_path)
    try:
        graph_context = retrieve_concept_context(conn, req.query_text)
    finally:
        conn.close()

    full_context = context_str
    if graph_context:
        full_context = f"{graph_context}\n\n---\n\n{context_str}"

    # 4. Build prompt (mirrors query.py single-query mode lines 744-747)
    prompt = (
        f"### RETRIEVED CONTEXT FROM BOOKS:\n{full_context}\n\n"
        f"User Query: {req.query_text}"
    )

    # 5. Generate completion
    answer = st.llm.generate_completion(_SYSTEM_INSTRUCTION, prompt)

    # 6. Serialize sources (same shape as /search results)
    sources = [
        SearchResult(
            chunk_id=chunk["chunk_id"],
            text=chunk["text"],
            location=chunk.get("location"),
            source_title=chunk["source_title"],
            source_author=chunk["source_author"],
            score=float(score),
        )
        for chunk, score in raw
    ]

    return ChatResponse(answer=answer, sources=sources)
```

- [ ] **Step 4: Run all search+chat tests — confirm all PASS**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search -v
```

Expected output:
```
test_chat_503_detail_mentions_provider (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_answer_is_fake_answer (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_default_limit_is_five (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_empty_query_returns_400 (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_limit_restricts_sources (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_missing_query_returns_422 (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_response_has_answer_key (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_response_has_sources_key (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_returns_200 (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_returns_503_when_chat_model_is_none (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_returns_503_when_provider_is_none (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_source_scores_ordered_descending (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_sources_have_required_keys (tests.test_web_search.TestChatEndpoint) ... ok
test_chat_sources_is_list (tests.test_web_search.TestChatEndpoint) ... ok
test_search_chunk_id_is_int (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_custom_limit_respected (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_default_limit_is_five (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_empty_query_text_returns_400 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_limit_zero_uses_default (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_missing_query_text_returns_422 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_response_is_list (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_result_contains_seeded_source (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_result_has_required_keys (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_results_ordered_by_score_descending (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_returns_200 (tests.test_web_search.TestSearchEndpoint) ... ok
test_search_score_is_float (tests.test_web_search.TestSearchEndpoint) ... ok

----------------------------------------------------------------------
Ran 26 tests in ...s

OK
```

- [ ] **Step 5: Commit**

```
git add web/routes_search.py tests/test_web_search.py && git commit -m "feat(web): add POST /chat endpoint with LLM synthesis, offline 503, and full TDD coverage"
```

---

### Task SEARCH-4: Dependency wiring — requirements.txt, pyproject.toml, and CLI subcommand

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/requirements.txt`
- Modify `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`
- Modify `/Users/aniketnamjoshi/knowledge-project/cli.py`
- Create `/Users/aniketnamjoshi/knowledge-project/web/server.py`

- [ ] **Step 1: Write failing test for the CLI wiring**

Add the class below at the bottom of `/Users/aniketnamjoshi/knowledge-project/tests/test_web_search.py`:

```python
class TestWebServerModule(unittest.TestCase):
    """Smoke-tests that web.server is importable and exposes a main() callable."""

    def test_web_server_module_importable(self):
        import importlib

        mod = importlib.import_module("web.server")
        self.assertTrue(callable(getattr(mod, "main", None)))

    def test_fastapi_importable(self):
        import fastapi  # noqa: F401 — confirms dep is installed

    def test_uvicorn_importable(self):
        import uvicorn  # noqa: F401 — confirms dep is installed
```

- [ ] **Step 2: Run the test — confirm it FAILS**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search.TestWebServerModule -v
```

Expected output:
```
ERROR: test_web_server_module_importable (tests.test_web_search.TestWebServerModule)
ModuleNotFoundError: No module named 'web.server'
...
FAILED (errors=1)
```

- [ ] **Step 3: Add deps to `requirements.txt`**

Open `/Users/aniketnamjoshi/knowledge-project/requirements.txt` and add these lines (insert after the existing entries, before any blank trailing line):

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0
```

- [ ] **Step 4: Add the `web` package and deps to `pyproject.toml`**

In the `[project]` `dependencies` list in `/Users/aniketnamjoshi/knowledge-project/pyproject.toml`, add:

```
"fastapi>=0.110.0",
"uvicorn[standard]>=0.29.0",
```

In the `[tool.setuptools]` section, add (alongside the existing `py-modules` line):

```toml
packages = ["web"]
```

- [ ] **Step 5: Create `web/server.py`**

`/Users/aniketnamjoshi/knowledge-project/web/server.py`:

```python
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import resolve_db_path


def main():
    """Entry point for `psyche web`. Resolves the DB path then launches uvicorn."""
    import uvicorn

    db_path = resolve_db_path(os.getenv("DATABASE_PATH", "knowledge.db"))
    os.environ["DATABASE_PATH"] = db_path

    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PSYCHE_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Wire the `web` subcommand in `cli.py`**

Locate the subcommand dispatch block in `/Users/aniketnamjoshi/knowledge-project/cli.py` and add an `elif` branch for `web`:

```python
elif subcommand == "web":
    import web.server
    web.server.main()
```

Also add `"web"` to the usage string and the `"Available commands"` list print lines in `cli.py`.

- [ ] **Step 7: Run the dep smoke-tests — confirm all PASS**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_search.TestWebServerModule -v
```

Expected output:
```
test_fastapi_importable (tests.test_web_search.TestWebServerModule) ... ok
test_uvicorn_importable (tests.test_web_search.TestWebServerModule) ... ok
test_web_server_module_importable (tests.test_web_search.TestWebServerModule) ... ok

----------------------------------------------------------------------
Ran 3 tests in ...s

OK
```

- [ ] **Step 8: Run the full cluster test suite to confirm no regressions**

```
TESTING=true RERANK_PROVIDER=none .venv/bin/python -m unittest tests.test_web_base tests.test_web_search -v
```

Expected output:
```
----------------------------------------------------------------------
Ran 29 tests in ...s

OK
```

- [ ] **Step 9: Commit**

```
git add requirements.txt pyproject.toml web/server.py cli.py && git commit -m "feat(web): wire fastapi/uvicorn deps, web.server entry point, and psyche web CLI subcommand"
```

---

## Knowledge-graph endpoints

### Task GRAPH-1: Add `web/routes_graph.py` router skeleton and register it in `web/app.py`

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/routes_graph.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/web/app.py`

- [ ] **Step 1: Write the failing unittest — import and router presence**

```python
# tests/test_web_graph.py
import os
import sys
import unittest

os.environ.setdefault("TESTING", "true")
os.environ.setdefault("PSYCHE_NONINTERACTIVE", "1")
os.environ.setdefault("RERANK_PROVIDER", "none")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.test_web_base import WebTestCase


class TestGraphRouterRegistered(WebTestCase):
    def test_graph_nodes_route_exists(self):
        """GET /graph/nodes must return 200 (even if empty) — proves router is registered."""
        resp = self.client.get("/graph/nodes")
        self.assertEqual(resp.status_code, 200)

    def test_graph_edges_route_exists(self):
        """GET /graph/edges must return 200 (even if empty) — proves router is registered."""
        resp = self.client.get("/graph/edges")
        self.assertEqual(resp.status_code, 200)

    def test_graph_build_route_exists(self):
        """POST /graph/build must not return 404 — proves router is registered."""
        resp = self.client.post("/graph/build", json={})
        self.assertNotEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test — confirm it FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphRouterRegistered -v
```

Expected output:
```
ERROR: test_graph_build_route_exists (tests.test_web_graph.TestGraphRouterRegistered) ...
AssertionError: 404 == 404  (or ImportError / AttributeError on missing module)
...
FAILED (errors=3)
```

- [ ] **Step 3: Create `web/routes_graph.py` with the router skeleton**

```python
# web/routes_graph.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, Request
from web.deps import get_state

router = APIRouter()


@router.get("/graph/nodes")
def graph_nodes(request: Request):
    """Returns all concepts (nodes) in the knowledge graph."""
    from db import get_connection, get_all_concepts
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        nodes = get_all_concepts(conn)
    finally:
        conn.close()
    return nodes


@router.get("/graph/edges")
def graph_edges(request: Request):
    """Returns all concept links (edges) in the knowledge graph."""
    from db import get_connection, get_concept_links
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        edges = get_concept_links(conn)
    finally:
        conn.close()
    return edges


@router.post("/graph/build")
def graph_build(request: Request, clusters: int = 6):
    """Builds the concept graph synchronously. Guards against empty DB and sys.exit."""
    from db import get_connection
    from build_graph import build_concept_graph
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM chunks")
        count = cur.fetchone()[0]
    finally:
        conn.close()
    if count == 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Database is empty. Ingest some documents first.")
    try:
        build_concept_graph(st.db_path, clusters)
    except SystemExit as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Graph build failed: {exc}")
    return {"status": "ok", "clusters": clusters}
```

- [ ] **Step 4: Register the router in `web/app.py`**

Open `/Users/aniketnamjoshi/knowledge-project/web/app.py` and add exactly one `include_router` line for `routes_graph` (kept alphabetical among the `include_router` calls):

```python
from web import routes_graph
app.include_router(routes_graph.router)
```

(Insert this line in the alphabetical position in `create_app()` alongside any existing `include_router` registrations.)

- [ ] **Step 5: Run the test — confirm it PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphRouterRegistered -v
```

Expected output:
```
test_graph_build_route_exists (tests.test_web_graph.TestGraphRouterRegistered) ... ok
test_graph_edges_route_exists (tests.test_web_graph.TestGraphRouterRegistered) ... ok
test_graph_nodes_route_exists (tests.test_web_graph.TestGraphRouterRegistered) ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.XXXs

OK
```

- [ ] **Step 6: Commit**

```
git add /Users/aniketnamjoshi/knowledge-project/web/routes_graph.py /Users/aniketnamjoshi/knowledge-project/web/app.py /Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py && git commit -m "feat(web): add routes_graph router skeleton with /graph/nodes, /graph/edges, /graph/build"
```

---

### Task GRAPH-2: `GET /graph/nodes` — returns all concepts with correct schema

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`
- No implementation change needed (handler already written in GRAPH-1)

- [ ] **Step 1: Write the failing unittest — response shape and seeded data**

Add to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`:

```python
class TestGraphNodes(WebTestCase):
    def test_nodes_returns_list(self):
        """Response body must be a JSON array."""
        resp = self.client.get("/graph/nodes")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_nodes_contain_seeded_concepts(self):
        """The fixture seeds 'Stoicism' and 'Virtue'; both must appear."""
        resp = self.client.get("/graph/nodes")
        names = [n["name"] for n in resp.json()]
        self.assertIn("Stoicism", names)
        self.assertIn("Virtue", names)

    def test_nodes_schema(self):
        """Every node must carry id, name, definition, category keys."""
        resp = self.client.get("/graph/nodes")
        for node in resp.json():
            with self.subTest(node=node):
                self.assertIn("id", node)
                self.assertIn("name", node)
                self.assertIn("definition", node)
                self.assertIn("category", node)

    def test_nodes_id_is_integer(self):
        """id field must be an integer."""
        resp = self.client.get("/graph/nodes")
        for node in resp.json():
            with self.subTest(node=node):
                self.assertIsInstance(node["id"], int)
```

- [ ] **Step 2: Run the test — confirm it FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphNodes -v
```

Expected output (before GRAPH-1 is merged this would be ImportError; after GRAPH-1 implementation the route exists but if the test class was not yet in the file, discovery finds nothing — the NEW test cases should fail because they were not present):
```
tests.test_web_graph.TestGraphNodes.test_nodes_contain_seeded_concepts ... FAIL
tests.test_web_graph.TestGraphNodes.test_nodes_id_is_integer ... FAIL
tests.test_web_graph.TestGraphNodes.test_nodes_returns_list ... FAIL
tests.test_web_graph.TestGraphNodes.test_nodes_schema ... FAIL

----------------------------------------------------------------------
FAILED (failures=4)
```

- [ ] **Step 3: Write the minimal implementation**

The `graph_nodes` handler in `web/routes_graph.py` (written in GRAPH-1) already calls `get_all_concepts(conn)` and returns the list directly. `get_all_concepts` in `db.py` returns `[{"id": r[0], "name": r[1], "definition": r[2], "category": r[3]} for r in rows]`, which exactly matches the required schema. No additional implementation change is needed — the failing step above was because the test class was not yet added to the file.

Confirm `web/routes_graph.py` `graph_nodes` handler reads:

```python
@router.get("/graph/nodes")
def graph_nodes(request: Request):
    """Returns all concepts (nodes) in the knowledge graph."""
    from db import get_connection, get_all_concepts
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        nodes = get_all_concepts(conn)
    finally:
        conn.close()
    return nodes
```

- [ ] **Step 4: Run the test — confirm it PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphNodes -v
```

Expected output:
```
test_nodes_contain_seeded_concepts (tests.test_web_graph.TestGraphNodes) ... ok
test_nodes_id_is_integer (tests.test_web_graph.TestGraphNodes) ... ok
test_nodes_returns_list (tests.test_web_graph.TestGraphNodes) ... ok
test_nodes_schema (tests.test_web_graph.TestGraphNodes) ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add /Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py && git commit -m "test(web): add GET /graph/nodes response-shape and seeded-data assertions"
```

---

### Task GRAPH-3: `GET /graph/edges` — returns all concept links with correct schema

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`
- No implementation change needed (handler already written in GRAPH-1)

- [ ] **Step 1: Write the failing unittest — response shape and seeded link**

Add to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`:

```python
class TestGraphEdges(WebTestCase):
    def test_edges_returns_list(self):
        """Response body must be a JSON array."""
        resp = self.client.get("/graph/edges")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_edges_contain_seeded_link(self):
        """The fixture seeds Stoicism -[emphasizes]-> Virtue; that edge must appear."""
        resp = self.client.get("/graph/edges")
        edges = resp.json()
        found = any(
            e["source"] == "Stoicism" and e["target"] == "Virtue" and e["relationship"] == "emphasizes"
            for e in edges
        )
        self.assertTrue(found, f"Expected Stoicism->Virtue edge, got: {edges}")

    def test_edges_schema(self):
        """Every edge must carry id, source, target, relationship, description keys."""
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIn("id", edge)
                self.assertIn("source", edge)
                self.assertIn("target", edge)
                self.assertIn("relationship", edge)
                self.assertIn("description", edge)

    def test_edges_id_is_integer(self):
        """id field must be an integer."""
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIsInstance(edge["id"], int)

    def test_edges_source_target_are_strings(self):
        """source and target must be concept name strings (not IDs)."""
        resp = self.client.get("/graph/edges")
        for edge in resp.json():
            with self.subTest(edge=edge):
                self.assertIsInstance(edge["source"], str)
                self.assertIsInstance(edge["target"], str)
```

- [ ] **Step 2: Run the test — confirm it FAILS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphEdges -v
```

Expected output:
```
tests.test_web_graph.TestGraphEdges.test_edges_contain_seeded_link ... FAIL
tests.test_web_graph.TestGraphEdges.test_edges_id_is_integer ... FAIL
tests.test_web_graph.TestGraphEdges.test_edges_returns_list ... FAIL
tests.test_web_graph.TestGraphEdges.test_edges_schema ... FAIL
tests.test_web_graph.TestGraphEdges.test_edges_source_target_are_strings ... FAIL

----------------------------------------------------------------------
FAILED (failures=5)
```

- [ ] **Step 3: Confirm the implementation**

The `graph_edges` handler in `web/routes_graph.py` (written in GRAPH-1) already calls `get_concept_links(conn)` and returns the list directly. `get_concept_links` in `db.py` returns `[{"id": r[0], "source": r[1], "target": r[2], "relationship": r[3], "description": r[4]} for r in rows]`, where `source` and `target` are concept **names** (the JOIN resolves IDs to names). This exactly matches the required schema; the handler is:

```python
@router.get("/graph/edges")
def graph_edges(request: Request):
    """Returns all concept links (edges) in the knowledge graph."""
    from db import get_connection, get_concept_links
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        edges = get_concept_links(conn)
    finally:
        conn.close()
    return edges
```

No additional implementation change is needed.

- [ ] **Step 4: Run the test — confirm it PASSES**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphEdges -v
```

Expected output:
```
test_edges_contain_seeded_link (tests.test_web_graph.TestGraphEdges) ... ok
test_edges_id_is_integer (tests.test_web_graph.TestGraphEdges) ... ok
test_edges_returns_list (tests.test_web_graph.TestGraphEdges) ... ok
test_edges_schema (tests.test_web_graph.TestGraphEdges) ... ok
test_edges_source_target_are_strings (tests.test_web_graph.TestGraphEdges) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.XXXs

OK
```

- [ ] **Step 5: Commit**

```
git add /Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py && git commit -m "test(web): add GET /graph/edges response-shape and seeded-link assertions"
```

---

### Task GRAPH-4: `POST /graph/build` — empty-DB 400 guard, success path, and sys.exit shielding

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_graph.py`

- [ ] **Step 1: Write the failing unittests**

Add to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py`:

```python
import tempfile
import os as _os
import db as _db


class TestGraphBuildEmptyDB(WebTestCase):
    """POST /graph/build on an empty database must return 400, not kill the process."""

    def setUp(self):
        super().setUp()
        # Build a second app wired to an empty DB so we can test the guard.
        self._empty_tmp = tempfile.TemporaryDirectory()
        empty_db_path = _os.path.join(self._empty_tmp.name, "empty.db")
        _db.init_db(empty_db_path)

        import web.app
        from web.deps import AppState
        import numpy as np

        empty_state = AppState(
            db_path=empty_db_path,
            llm=self.state.llm,
            chunk_ids=np.array([], dtype=np.int32),
            embeddings_matrix=np.array([], dtype=np.float32),
            usearch_index=None,
        )
        empty_app = web.app.create_app()
        empty_app.state.psyche = empty_state
        from fastapi.testclient import TestClient
        self._empty_client = TestClient(empty_app)

    def tearDown(self):
        self._empty_client.close()
        self._empty_tmp.cleanup()
        super().tearDown()

    def test_empty_db_returns_400(self):
        """Empty DB must yield 400 with a human-readable detail message."""
        resp = self._empty_client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("detail", body)
        self.assertIn("empty", body["detail"].lower())

    def test_empty_db_does_not_raise_system_exit(self):
        """The web process must NOT be killed by a sys.exit inside build_concept_graph."""
        # If the guard is missing, TestClient propagates SystemExit and the test
        # itself crashes — so reaching this assertion proves the guard is in place.
        try:
            self._empty_client.post("/graph/build", json={})
        except SystemExit:
            self.fail("POST /graph/build raised SystemExit — guard is missing")


class TestGraphBuildSuccess(WebTestCase):
    """POST /graph/build with seeded chunks must return {status: 'ok'}."""

    def test_build_returns_ok(self):
        """Successful build must return HTTP 200 with status=='ok'."""
        # The fixture DB has 3 chunks; build_concept_graph will call LLMClient()
        # internally (its own instance). Because TESTING=true and
        # PSYCHE_NONINTERACTIVE=1 are set, LLMClient.__init__ is a near-no-op and
        # provider resolves to 'none', which delegates to build_cooccurrence_graph —
        # a pure-Python path with no network calls.
        resp = self.client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")

    def test_build_response_includes_clusters(self):
        """Response must echo back the clusters parameter used."""
        resp = self.client.post("/graph/build", json={"clusters": 3})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["clusters"], 3)

    def test_build_default_clusters_is_6(self):
        """When clusters is omitted, the default of 6 must be echoed back."""
        resp = self.client.post("/graph/build", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["clusters"], 6)
```

- [ ] **Step 2: Run the tests — confirm they FAIL**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphBuildEmptyDB tests.test_web_graph.TestGraphBuildSuccess -v
```

Expected output:
```
test_build_default_clusters_is_6 ... FAIL
test_build_response_includes_clusters ... FAIL
test_build_returns_ok ... FAIL
test_empty_db_does_not_raise_system_exit ... FAIL
test_empty_db_returns_400 ... FAIL

----------------------------------------------------------------------
FAILED (failures=5)
```

- [ ] **Step 3: Write the minimal implementation in `web/routes_graph.py`**

Replace the `graph_build` handler (written in GRAPH-1) with the version below, which accepts `clusters` as a query parameter and properly shields against `sys.exit`:

```python
# web/routes_graph.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from web.deps import get_state

router = APIRouter()


@router.get("/graph/nodes")
def graph_nodes(request: Request):
    """Returns all concepts (nodes) in the knowledge graph."""
    from db import get_connection, get_all_concepts
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        nodes = get_all_concepts(conn)
    finally:
        conn.close()
    return nodes


@router.get("/graph/edges")
def graph_edges(request: Request):
    """Returns all concept links (edges) in the knowledge graph."""
    from db import get_connection, get_concept_links
    st = get_state(request)
    conn = get_connection(st.db_path)
    try:
        edges = get_concept_links(conn)
    finally:
        conn.close()
    return edges


@router.post("/graph/build")
def graph_build(request: Request, clusters: int = 6):
    """Builds the concept graph synchronously.

    Guards:
    - Returns 400 when the database has no chunks (avoids sys.exit inside
      build_concept_graph).
    - Catches SystemExit from build_concept_graph and returns 500 instead of
      killing the server process.
    """
    from db import get_connection
    from build_graph import build_concept_graph

    st = get_state(request)

    # Pre-flight: reject immediately if DB is empty to avoid sys.exit(1) inside
    # build_concept_graph (build_graph.py lines 223-224).
    conn = get_connection(st.db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM chunks")
        count = cur.fetchone()[0]
    finally:
        conn.close()

    if count == 0:
        raise HTTPException(
            status_code=400,
            detail="Database is empty. Ingest some documents first.",
        )

    try:
        build_concept_graph(st.db_path, clusters)
    except SystemExit as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Graph build failed: {exc}",
        )

    return {"status": "ok", "clusters": clusters}
```

- [ ] **Step 4: Run the tests — confirm they PASS**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph.TestGraphBuildEmptyDB tests.test_web_graph.TestGraphBuildSuccess -v
```

Expected output:
```
test_build_default_clusters_is_6 (tests.test_web_graph.TestGraphBuildSuccess) ... ok
test_build_response_includes_clusters (tests.test_web_graph.TestGraphBuildSuccess) ... ok
test_build_returns_ok (tests.test_web_graph.TestGraphBuildSuccess) ... ok
test_empty_db_does_not_raise_system_exit (tests.test_web_graph.TestGraphBuildEmptyDB) ... ok
test_empty_db_returns_400 (tests.test_web_graph.TestGraphBuildEmptyDB) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.XXXs

OK
```

- [ ] **Step 5: Run the full graph test file to confirm no regressions**

```
TESTING=true .venv/bin/python -m unittest tests.test_web_graph -v
```

Expected output:
```
test_build_default_clusters_is_6 ... ok
test_build_response_includes_clusters ... ok
test_build_returns_ok ... ok
test_empty_db_does_not_raise_system_exit ... ok
test_empty_db_returns_400 ... ok
test_edges_contain_seeded_link ... ok
test_edges_id_is_integer ... ok
test_edges_returns_list ... ok
test_edges_schema ... ok
test_edges_source_target_are_strings ... ok
test_graph_build_route_exists ... ok
test_graph_edges_route_exists ... ok
test_graph_nodes_route_exists ... ok
test_nodes_contain_seeded_concepts ... ok
test_nodes_id_is_integer ... ok
test_nodes_returns_list ... ok
test_nodes_schema ... ok

----------------------------------------------------------------------
Ran 17 tests in 0.XXXs

OK
```

- [ ] **Step 6: Commit**

```
git add /Users/aniketnamjoshi/knowledge-project/web/routes_graph.py /Users/aniketnamjoshi/knowledge-project/tests/test_web_graph.py && git commit -m "feat(web): implement POST /graph/build with empty-DB guard and sys.exit shielding"
```

---

## Provider & agent-connect endpoints

### Task CONNECT-1: Scaffold `web/routes_system.py` with stubs and register it in `web/app.py`

**Files:**
- Create `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/web/app.py` (add one `include_router` line)

- [ ] **Step 1: Write the failing test — import and router registration**

  Create `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`:

  ```python
  import os
  import sys
  os.environ["TESTING"] = "true"
  os.environ["PSYCHE_NONINTERACTIVE"] = "1"
  os.environ["RERANK_PROVIDER"] = "none"
  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

  import unittest
  from tests.test_web_base import WebTestCase


  class TestSystemRouterRegistered(WebTestCase):
      """Routes /provider, /connect, /connect/status, /supported-clients are reachable."""

      def test_provider_endpoint_exists(self):
          """GET /provider returns 200, not 404/405."""
          resp = self.client.get("/provider")
          self.assertNotEqual(resp.status_code, 404, "GET /provider should be registered")
          self.assertNotEqual(resp.status_code, 405, "GET /provider should allow GET")

      def test_connect_endpoint_exists(self):
          """POST /connect returns something other than 404."""
          resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
          self.assertNotEqual(resp.status_code, 404, "POST /connect should be registered")

      def test_connect_status_endpoint_exists(self):
          """GET /connect/status returns something other than 404."""
          resp = self.client.get("/connect/status", params={"client": "claude-code"})
          self.assertNotEqual(resp.status_code, 404, "GET /connect/status should be registered")

      def test_supported_clients_endpoint_exists(self):
          """GET /supported-clients returns something other than 404."""
          resp = self.client.get("/supported-clients")
          self.assertNotEqual(resp.status_code, 404, "GET /supported-clients should be registered")


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run — expect FAIL (404s because the router and module don't exist yet)**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestSystemRouterRegistered -v
  ```

  Expected output (all four tests fail with AssertionError — 404 returned):
  ```
  FAIL: test_connect_endpoint_exists ...
  FAIL: test_connect_status_endpoint_exists ...
  FAIL: test_provider_endpoint_exists ...
  FAIL: test_supported_clients_endpoint_exists ...
  Ran 4 tests in ...s
  FAILED (failures=4)
  ```

- [ ] **Step 3: Create `web/routes_system.py` with stub handlers**

  Create `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py`:

  ```python
  """
  web/routes_system.py — GET /provider, POST /connect, GET /connect/status,
  GET /supported-clients.
  """
  import os
  import sys
  sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

  from fastapi import APIRouter, HTTPException, Request
  from pydantic import BaseModel
  from typing import Optional

  from web.deps import get_state

  router = APIRouter()

  _SUPPORTED_CLIENTS = ["claude-code", "codex", "gemini", "antigravity"]


  class ConnectRequest(BaseModel):
      client: str
      dry_run: Optional[bool] = False


  class ConnectResponse(BaseModel):
      actions: list[str]


  class ProviderResponse(BaseModel):
      provider: str
      chat_provider: str
      embed_model: str
      chat_model: str
      db_path: str


  @router.get("/provider", response_model=ProviderResponse)
  def get_provider(request: Request):
      st = get_state(request)
      return ProviderResponse(
          provider=st.llm.provider,
          chat_provider=getattr(st.llm, "chat_provider", st.llm.provider),
          embed_model=st.llm.embed_model,
          chat_model=st.llm.chat_model,
          db_path=st.db_path,
      )


  @router.post("/connect", response_model=ConnectResponse)
  def post_connect(body: ConnectRequest, request: Request):
      from connect import connect
      try:
          actions = connect(body.client, dry_run=bool(body.dry_run))
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc))
      return ConnectResponse(actions=actions)


  @router.get("/connect/status", response_model=ConnectResponse)
  def get_connect_status(client: str, request: Request):
      from connect import connect
      try:
          actions = connect(client, dry_run=True)
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc))
      return ConnectResponse(actions=actions)


  @router.get("/supported-clients")
  def get_supported_clients():
      return _SUPPORTED_CLIENTS
  ```

  Then add the `include_router` line in `web/app.py`. Open that file and append inside `create_app()` (alphabetical order with the other routers):

  ```python
  from web import routes_system
  app.include_router(routes_system.router)
  ```

- [ ] **Step 4: Run — expect PASS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestSystemRouterRegistered -v
  ```

  Expected output:
  ```
  test_connect_endpoint_exists ... ok
  test_connect_status_endpoint_exists ... ok
  test_provider_endpoint_exists ... ok
  test_supported_clients_endpoint_exists ... ok
  Ran 4 tests in ...s
  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add web/routes_system.py tests/test_web_system.py web/app.py && git commit -m "feat(web): scaffold routes_system router with stub system endpoints"
  ```

---

### Task CONNECT-2: `GET /provider` — returns provider and model attributes from `AppState`

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`
- Modify `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py` (implementation already present from CONNECT-1; this task adds precise behavioral tests)

- [ ] **Step 1: Write the failing test — response shape and values**

  Append this test class to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py` (before the `if __name__ == "__main__":` block):

  ```python
  class TestGetProvider(WebTestCase):
      """GET /provider returns the FakeLLM attributes wired into AppState."""

      def test_status_200(self):
          resp = self.client.get("/provider")
          self.assertEqual(resp.status_code, 200)

      def test_response_keys(self):
          resp = self.client.get("/provider")
          body = resp.json()
          for key in ("provider", "chat_provider", "embed_model", "chat_model", "db_path"):
              self.assertIn(key, body, f"key {key!r} missing from /provider response")

      def test_provider_matches_fake_llm(self):
          resp = self.client.get("/provider")
          body = resp.json()
          self.assertEqual(body["provider"], "fake")
          self.assertEqual(body["chat_provider"], "fake")
          self.assertEqual(body["embed_model"], "fake-embed")
          self.assertEqual(body["chat_model"], "fake-chat")

      def test_db_path_is_absolute(self):
          resp = self.client.get("/provider")
          body = resp.json()
          self.assertTrue(
              os.path.isabs(body["db_path"]),
              f"db_path should be absolute, got {body['db_path']!r}",
          )

      def test_no_llm_construction_per_request(self):
          """Two requests return identical provider — no re-init between calls."""
          r1 = self.client.get("/provider")
          r2 = self.client.get("/provider")
          self.assertEqual(r1.json()["provider"], r2.json()["provider"])
  ```

- [ ] **Step 2: Run — expect FAIL (tests not yet in the file; class missing)**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetProvider -v
  ```

  Expected output:
  ```
  ERROR: tests.test_web_system.TestGetProvider
  AttributeError: module 'tests.test_web_system' has no attribute 'TestGetProvider'
  ...
  FAILED (errors=1)
  ```

- [ ] **Step 3: The implementation is already in `web/routes_system.py` from CONNECT-1. Verify the `get_provider` handler reads directly from `st.llm` attributes without constructing a new `LLMClient`.**

  The handler (already written) reads:
  ```python
  @router.get("/provider", response_model=ProviderResponse)
  def get_provider(request: Request):
      st = get_state(request)
      return ProviderResponse(
          provider=st.llm.provider,
          chat_provider=getattr(st.llm, "chat_provider", st.llm.provider),
          embed_model=st.llm.embed_model,
          chat_model=st.llm.chat_model,
          db_path=st.db_path,
      )
  ```

  No changes needed — the test class addition in Step 1 is the only edit required.

- [ ] **Step 4: Run — expect PASS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetProvider -v
  ```

  Expected output:
  ```
  test_db_path_is_absolute ... ok
  test_no_llm_construction_per_request ... ok
  test_provider_matches_fake_llm ... ok
  test_response_keys ... ok
  test_status_200 ... ok
  Ran 5 tests in ...s
  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add tests/test_web_system.py && git commit -m "test(web): add behavioral tests for GET /provider response shape and values"
  ```

---

### Task CONNECT-3: `GET /supported-clients` — returns the fixed list of valid client names

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`
- `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py` (no changes needed — handler already written)

- [ ] **Step 1: Write the failing test**

  Append this class to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`:

  ```python
  class TestGetSupportedClients(WebTestCase):
      """GET /supported-clients returns the canonical list of client names."""

      def test_status_200(self):
          resp = self.client.get("/supported-clients")
          self.assertEqual(resp.status_code, 200)

      def test_returns_list(self):
          resp = self.client.get("/supported-clients")
          body = resp.json()
          self.assertIsInstance(body, list)

      def test_all_four_clients_present(self):
          resp = self.client.get("/supported-clients")
          body = resp.json()
          for name in ("claude-code", "codex", "gemini", "antigravity"):
              self.assertIn(name, body, f"{name!r} missing from /supported-clients")

      def test_exactly_four_clients(self):
          resp = self.client.get("/supported-clients")
          body = resp.json()
          self.assertEqual(
              len(body), 4,
              f"Expected exactly 4 clients, got {len(body)}: {body}",
          )
  ```

- [ ] **Step 2: Run — expect FAIL (class not yet added)**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetSupportedClients -v
  ```

  Expected output:
  ```
  ERROR: tests.test_web_system.TestGetSupportedClients
  AttributeError: module 'tests.test_web_system' has no attribute 'TestGetSupportedClients'
  ...
  FAILED (errors=1)
  ```

- [ ] **Step 3: Implementation is already present in `web/routes_system.py`:**

  ```python
  _SUPPORTED_CLIENTS = ["claude-code", "codex", "gemini", "antigravity"]

  @router.get("/supported-clients")
  def get_supported_clients():
      return _SUPPORTED_CLIENTS
  ```

  No code changes needed — appending the test class in Step 1 is the only edit.

- [ ] **Step 4: Run — expect PASS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetSupportedClients -v
  ```

  Expected output:
  ```
  test_all_four_clients_present ... ok
  test_exactly_four_clients ... ok
  test_returns_list ... ok
  test_status_200 ... ok
  Ran 4 tests in ...s
  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add tests/test_web_system.py && git commit -m "test(web): add behavioral tests for GET /supported-clients"
  ```

---

### Task CONNECT-4: `POST /connect` — wires a client, returns actions list; validates client name

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`
- `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py` (no changes needed — handler already written)

- [ ] **Step 1: Write the failing tests**

  Append this class to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`:

  ```python
  class TestPostConnect(WebTestCase):
      """POST /connect calls connect(client, dry_run=...) and returns {actions:[...]}."""

      def setUp(self):
          super().setUp()
          # Redirect HOME so connect() writes into a temp dir, never the real ~/.claude
          import tempfile
          self._home_tmp = tempfile.TemporaryDirectory()
          self._orig_home = os.environ.get("HOME")
          os.environ["HOME"] = self._home_tmp.name
          # Reload connect so _backup_once / expanduser pick up the new HOME
          import importlib
          import connect as _c
          importlib.reload(_c)

      def tearDown(self):
          if self._orig_home is None:
              os.environ.pop("HOME", None)
          else:
              os.environ["HOME"] = self._orig_home
          self._home_tmp.cleanup()
          super().tearDown()

      def test_status_200_dry_run(self):
          resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
          self.assertEqual(resp.status_code, 200)

      def test_response_has_actions_key(self):
          resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
          body = resp.json()
          self.assertIn("actions", body, f"'actions' key missing: {body}")

      def test_actions_is_list_of_strings(self):
          resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": True})
          body = resp.json()
          actions = body["actions"]
          self.assertIsInstance(actions, list)
          for item in actions:
              self.assertIsInstance(item, str, f"non-string action item: {item!r}")

      def test_dry_run_returns_nonempty_actions(self):
          """connect() in dry_run mode still describes what it would do."""
          resp = self.client.post("/connect", json={"client": "codex", "dry_run": True})
          body = resp.json()
          self.assertGreater(len(body["actions"]), 0, "dry_run should return at least one action")

      def test_antigravity_alias_accepted(self):
          """antigravity is a valid client alias (maps to gemini internally)."""
          resp = self.client.post("/connect", json={"client": "antigravity", "dry_run": True})
          self.assertEqual(resp.status_code, 200)

      def test_unknown_client_returns_400(self):
          resp = self.client.post("/connect", json={"client": "unknown-tool", "dry_run": True})
          self.assertEqual(resp.status_code, 400)
          body = resp.json()
          self.assertIn("detail", body)

      def test_missing_client_field_returns_422(self):
          """Pydantic validation: client is required."""
          resp = self.client.post("/connect", json={"dry_run": True})
          self.assertEqual(resp.status_code, 422)

      def test_connect_writes_files_when_not_dry_run(self):
          """Without dry_run, connect() actually writes config files into temp HOME."""
          resp = self.client.post("/connect", json={"client": "claude-code", "dry_run": False})
          self.assertEqual(resp.status_code, 200)
          settings_path = os.path.join(self._home_tmp.name, ".claude", "settings.json")
          self.assertTrue(
              os.path.exists(settings_path),
              "settings.json should exist after a real (non-dry-run) connect call",
          )
  ```

- [ ] **Step 2: Run — expect FAIL (class not yet added)**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestPostConnect -v
  ```

  Expected output:
  ```
  ERROR: tests.test_web_system.TestPostConnect
  AttributeError: module 'tests.test_web_system' has no attribute 'TestPostConnect'
  ...
  FAILED (errors=1)
  ```

- [ ] **Step 3: Verify the handler in `web/routes_system.py` correctly maps `ValueError` from `connect()` to HTTP 400:**

  ```python
  @router.post("/connect", response_model=ConnectResponse)
  def post_connect(body: ConnectRequest, request: Request):
      from connect import connect
      try:
          actions = connect(body.client, dry_run=bool(body.dry_run))
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc))
      return ConnectResponse(actions=actions)
  ```

  This is already correct from CONNECT-1. No source changes needed.

- [ ] **Step 4: Run — expect PASS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestPostConnect -v
  ```

  Expected output:
  ```
  test_actions_is_list_of_strings ... ok
  test_antigravity_alias_accepted ... ok
  test_connect_writes_files_when_not_dry_run ... ok
  test_dry_run_returns_nonempty_actions ... ok
  test_missing_client_field_returns_422 ... ok
  test_response_has_actions_key ... ok
  test_status_200_dry_run ... ok
  test_unknown_client_returns_400 ... ok
  Ran 8 tests in ...s
  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add tests/test_web_system.py && git commit -m "test(web): add behavioral tests for POST /connect including dry-run, alias, and 400 on unknown client"
  ```

---

### Task CONNECT-5: `GET /connect/status` — dry-run preview for a client; validates client query param

**Files:**
- Modify `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`
- `/Users/aniketnamjoshi/knowledge-project/web/routes_system.py` (no changes needed — handler already written)

- [ ] **Step 1: Write the failing tests**

  Append this class to `/Users/aniketnamjoshi/knowledge-project/tests/test_web_system.py`:

  ```python
  class TestGetConnectStatus(WebTestCase):
      """GET /connect/status?client=X is always a dry-run preview; never writes files."""

      def setUp(self):
          super().setUp()
          import tempfile
          self._home_tmp = tempfile.TemporaryDirectory()
          self._orig_home = os.environ.get("HOME")
          os.environ["HOME"] = self._home_tmp.name
          import importlib
          import connect as _c
          importlib.reload(_c)

      def tearDown(self):
          if self._orig_home is None:
              os.environ.pop("HOME", None)
          else:
              os.environ["HOME"] = self._orig_home
          self._home_tmp.cleanup()
          super().tearDown()

      def test_status_200(self):
          resp = self.client.get("/connect/status", params={"client": "claude-code"})
          self.assertEqual(resp.status_code, 200)

      def test_response_has_actions_key(self):
          resp = self.client.get("/connect/status", params={"client": "claude-code"})
          body = resp.json()
          self.assertIn("actions", body)

      def test_actions_is_list(self):
          resp = self.client.get("/connect/status", params={"client": "claude-code"})
          self.assertIsInstance(resp.json()["actions"], list)

      def test_dry_run_does_not_write_files(self):
          """/connect/status must never write config files (it is always dry_run=True)."""
          self.client.get("/connect/status", params={"client": "claude-code"})
          settings_path = os.path.join(self._home_tmp.name, ".claude", "settings.json")
          self.assertFalse(
              os.path.exists(settings_path),
              "/connect/status must not write ~/.claude/settings.json (dry_run=True)",
          )

      def test_codex_dry_run_no_files(self):
          self.client.get("/connect/status", params={"client": "codex"})
          config_path = os.path.join(self._home_tmp.name, ".codex", "config.toml")
          self.assertFalse(os.path.exists(config_path))

      def test_antigravity_accepted(self):
          resp = self.client.get("/connect/status", params={"client": "antigravity"})
          self.assertEqual(resp.status_code, 200)

      def test_unknown_client_returns_400(self):
          resp = self.client.get("/connect/status", params={"client": "notepad"})
          self.assertEqual(resp.status_code, 400)

      def test_missing_client_param_returns_422(self):
          """Query param `client` is required."""
          resp = self.client.get("/connect/status")
          self.assertEqual(resp.status_code, 422)

      def test_status_and_post_connect_dry_run_return_same_actions(self):
          """GET /connect/status and POST /connect?dry_run=true must agree."""
          import tempfile, importlib
          # Both calls share the same monkeypatched HOME from setUp
          get_resp = self.client.get("/connect/status", params={"client": "codex"})
          post_resp = self.client.post("/connect", json={"client": "codex", "dry_run": True})
          self.assertEqual(get_resp.json()["actions"], post_resp.json()["actions"])
  ```

- [ ] **Step 2: Run — expect FAIL (class not yet added)**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetConnectStatus -v
  ```

  Expected output:
  ```
  ERROR: tests.test_web_system.TestGetConnectStatus
  AttributeError: module 'tests.test_web_system' has no attribute 'TestGetConnectStatus'
  ...
  FAILED (errors=1)
  ```

- [ ] **Step 3: Verify the handler in `web/routes_system.py` always passes `dry_run=True`:**

  ```python
  @router.get("/connect/status", response_model=ConnectResponse)
  def get_connect_status(client: str, request: Request):
      from connect import connect
      try:
          actions = connect(client, dry_run=True)
      except ValueError as exc:
          raise HTTPException(status_code=400, detail=str(exc))
      return ConnectResponse(actions=actions)
  ```

  This is already correct from CONNECT-1. No source changes needed.

- [ ] **Step 4: Run — expect PASS**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system.TestGetConnectStatus -v
  ```

  Expected output:
  ```
  test_actions_is_list ... ok
  test_antigravity_accepted ... ok
  test_codex_dry_run_no_files ... ok
  test_dry_run_does_not_write_files ... ok
  test_missing_client_param_returns_422 ... ok
  test_response_has_actions_key ... ok
  test_status_200 ... ok
  test_status_and_post_connect_dry_run_return_same_actions ... ok
  test_unknown_client_returns_400 ... ok
  Ran 9 tests in ...s
  OK
  ```

- [ ] **Step 5: Commit**

  ```
  git add tests/test_web_system.py && git commit -m "test(web): add behavioral tests for GET /connect/status dry-run preview"
  ```

---

### Task CONNECT-6: Full suite smoke-run — all CONNECT tests green together

**Files:**
- No new files. Runs all tests added in CONNECT-1 through CONNECT-5.

- [ ] **Step 1: Run full system test module**

  ```
  TESTING=true .venv/bin/python -m unittest tests.test_web_system -v
  ```

  Expected output (all classes, all tests pass):
  ```
  test_connect_endpoint_exists (tests.test_web_system.TestSystemRouterRegistered) ... ok
  test_connect_status_endpoint_exists (tests.test_web_system.TestSystemRouterRegistered) ... ok
  test_provider_endpoint_exists (tests.test_web_system.TestSystemRouterRegistered) ... ok
  test_supported_clients_endpoint_exists (tests.test_web_system.TestSystemRouterRegistered) ... ok
  test_db_path_is_absolute (tests.test_web_system.TestGetProvider) ... ok
  test_no_llm_construction_per_request (tests.test_web_system.TestGetProvider) ... ok
  test_provider_matches_fake_llm (tests.test_web_system.TestGetProvider) ... ok
  test_response_keys (tests.test_web_system.TestGetProvider) ... ok
  test_status_200 (tests.test_web_system.TestGetProvider) ... ok
  test_all_four_clients_present (tests.test_web_system.TestGetSupportedClients) ... ok
  test_exactly_four_clients (tests.test_web_system.TestGetSupportedClients) ... ok
  test_returns_list (tests.test_web_system.TestGetSupportedClients) ... ok
  test_status_200 (tests.test_web_system.TestGetSupportedClients) ... ok
  test_actions_is_list_of_strings (tests.test_web_system.TestPostConnect) ... ok
  test_antigravity_alias_accepted (tests.test_web_system.TestPostConnect) ... ok
  test_connect_writes_files_when_not_dry_run (tests.test_web_system.TestPostConnect) ... ok
  test_dry_run_returns_nonempty_actions (tests.test_web_system.TestPostConnect) ... ok
  test_missing_client_field_returns_422 (tests.test_web_system.TestPostConnect) ... ok
  test_response_has_actions_key (tests.test_web_system.TestPostConnect) ... ok
  test_status_200_dry_run (tests.test_web_system.TestPostConnect) ... ok
  test_unknown_client_returns_400 (tests.test_web_system.TestPostConnect) ... ok
  test_actions_is_list (tests.test_web_system.TestGetConnectStatus) ... ok
  test_antigravity_accepted (tests.test_web_system.TestGetConnectStatus) ... ok
  test_codex_dry_run_no_files (tests.test_web_system.TestGetConnectStatus) ... ok
  test_dry_run_does_not_write_files (tests.test_web_system.TestGetConnectStatus) ... ok
  test_missing_client_param_returns_422 (tests.test_web_system.TestGetConnectStatus) ... ok
  test_response_has_actions_key (tests.test_web_system.TestGetConnectStatus) ... ok
  test_status_200 (tests.test_web_system.TestGetConnectStatus) ... ok
  test_status_and_post_connect_dry_run_return_same_actions (tests.test_web_system.TestGetConnectStatus) ... ok
  test_unknown_client_returns_400 (tests.test_web_system.TestGetConnectStatus) ... ok
  Ran 30 tests in ...s
  OK
  ```

- [ ] **Step 2: Commit**

  ```
  git add tests/test_web_system.py web/routes_system.py && git commit -m "feat(web): complete CONNECT cluster — /provider, /connect, /connect/status, /supported-clients with full test coverage"
  ```

---

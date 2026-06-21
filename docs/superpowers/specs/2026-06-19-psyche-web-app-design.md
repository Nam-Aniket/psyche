# Psyche Local Web App — Design Spec (v1)

**Date:** 2026-06-19
**Status:** Approved design, ready for implementation planning
**Owner:** Aniket (Nam-Aniket)
**Repo:** `knowledge-project` (Psyche)

---

## 1. Overview

Psyche today is a local-first RAG + cross-agent-memory engine that you use through the
terminal (CLI) or through coding agents (MCP). This project adds a **local web app** — a
GUI served from `localhost` — so that **non-technical people can use Psyche too**, not just
developers in a terminal.

This is **not** a hosted SaaS. Psyche still runs entirely on the user's own machine.
Documents are uploaded to the *local* app, ingested locally, and the knowledge graph is
built locally. The only data that ever leaves the machine is the query + retrieved snippets
sent to whatever LLM the user themselves configured — exactly what Claude Code already does
today. So the local-first promise ("your documents never leave your disk") stays true; the
product simply gains a UI anyone can use.

### The positioning insight (two stories, two audiences)

The codebase carries two value propositions:

- **Knowledge-for-everyone** (the web app): upload docs → search → cited answers → explore a
  knowledge graph. Human-facing, in the browser.
- **Memory-for-developers** (existing): atomic facts, cross-agent, injected via hooks, cache
  savings. Agent-facing, via MCP in Claude Code / Cursor / Codex / Antigravity.

The **web-app v1 surfaces the knowledge / RAG / graph layer for humans**. The
memory / cross-agent layer stays agent-facing (MCP). The landing page carries both with **one
unifying hero + two CTAs**: "Open the app" (everyone) and "Connect your agent" (devs).

---

## 2. Audience, goals, success criteria

- **Primary visitor:** a developer who already uses an AI coding agent (Claude Code, Cursor,
  Codex, Antigravity). Secondary: a broader "second brain" audience who can now use the GUI.
- **North-star outcomes:** (A) **Adoption** — installs / "connect to my agent" actions, and
  (B) **Credibility & distribution** — GitHub stars, MCP-registry listing, discoverability.
- **"Done" (verifiable):** a stranger, unassisted, goes: land → `npx psyche-mcp web` → drop in
  a PDF → watch ingest finish → see their knowledge graph → ask a question → get a **cited**
  answer, in roughly **5 minutes**, on the restrained light design with a working dark-mode
  toggle.

---

## 3. Architecture & approach

**Principle:** reuse the proven core engine untouched; build a thin new web layer; let the
design define the API contract.

- **Reuse, do not rebuild:** ingestion, retrieval, graph, LLM client, memory, and guidance
  already exist as stateless, importable Python functions (see §6). They are not modified.
- **Build new:** a thin **FastAPI** layer that wraps those functions as HTTP endpoints, plus a
  **`psyche web` launcher** that boots the server and opens `localhost` in the browser, plus the
  Claude-Design-produced **frontend** (single-page app or static multipage, TBD in plan).
- **Order (design-first):** the approved frontend design *defines* the API contract — each
  screen states exactly what data it needs, and the thin API is built to that contract.
- **Guardrail:** the design only depicts data Psyche can actually serve in v1 (citations, real
  nodes/edges, ingest status, chat answers). Anything richer is marked **visual-only / v2** so
  the design never promises a screen the backend can't fill.

### Build sequence

1. **Spec** (this document) → the brief.
2. **Design** all 5 surfaces in Claude Design → one cohesive design system.
3. **Derive the API contract** from the approved design.
4. **Build the thin FastAPI layer** over the existing core + the `psyche web` launcher.
5. **Wire** frontend ↔ API, swap mock data for real data, then polish (impeccable) and verify
   the full loop live in the browser.

---

## 4. The five v1 surfaces

### 4.1 Landing
- **Purpose:** sell both stories; route visitors to the app or to agent setup.
- **Content:** unifying hero + two CTAs ("Open the app" / "Connect your agent"); the problem
  ("AI assistants have amnesia — you pay in tokens"); the everyone-facing upload→explore→ask
  story; the dev-facing cross-agent memory story; privacy/local-first; quickstart.
- **Reuses** existing `showcase/index.html` copy (see §9).

### 4.2 Setup
- **Purpose:** get a newcomer to a working state fast. Choose an LLM path; optionally one-click
  "connect to my agent."
- **UX:** pick one of three paths (BYO key → OpenAI/Gemini; Ollama offline; agent-only). Show
  current provider/config state. For devs, a one-click connect to claude-code / codex / gemini /
  antigravity with a dry-run preview of what will be wired.
- **Backend:** `connect(client, dry_run)`, provider info from the `LLMClient` instance, config
  persisted to `~/.psyche/.env`.

### 4.3 Upload & ingest
- **Purpose:** drop in documents and watch them become searchable knowledge.
- **UX:** drag-and-drop PDFs / EPUB / Markdown / txt / HTML / DOCX / Org; per-file progress;
  dedup feedback ("already ingested"); list of ingested sources.
- **Backend:** `extract_text` → `chunk_text` → `get_embeddings_batch` → persist; SHA-256 dedup
  via `calculate_sha256` / checksum check. Batch size 50 → progress via SSE or polling.

### 4.4 Knowledge-graph explorer
- **Purpose:** the signature "wow" — see *your* knowledge as a graph.
- **UX:** render the concept graph; click a node → see its definition + connected nodes; node
  categories; the full violet→cyan gradient *glow* lives here (where it's earned). Trigger a
  build if none exists.
- **Backend:** `get_all_concepts` (nodes), `get_concept_links` (typed directed edges),
  `build_concept_graph` / `build_cooccurrence_graph` (build, as a background job).
- **v1 = interactive visualization** (click node → connections). Deep analytics ("how knowledge
  connects") is **v2**.

### 4.5 Chat
- **Purpose:** ask a question, get a cited answer grounded in the user's documents.
- **UX:** chat input; answer with inline citations (source title, author, location); the LLM is
  the user's chosen provider.
- **Backend:** `perform_hybrid_search` → `format_context` → `generate_completion`. Citations are
  already carried in the chunk records (`source_title`, `source_author`, `location`).

---

## 5. Scope: v1 vs v2

- **v1 (real now):** 7-format ingest, hybrid search with reranking, cited answers, concept graph
  (build + view + click), one-click agent connect, the three LLM paths.
- **v2 (backend exists, not surfaced in v1):**
  - **Memory / Facts page** — atomic memory CRUD + entities (`memzero.py`).
  - **Goals / Guidance page** — goals, experiments, rules, check-ins (`guidance.py`).
  - **Deep graph analytics** — beyond visualization.
  - **Claude as an in-app chat provider** (see §7).

---

## 6. API contract (thin FastAPI layer over existing functions)

All endpoints wrap existing, stateless functions. Request/response shapes derive from the real
data shapes already returned by the core.

| Endpoint | Method | Wraps | Returns |
|---|---|---|---|
| `/ingest` | POST | `extract_text`→`chunk_text`→`get_embeddings_batch`→persist | `{source_id, chunk_count, skipped}` |
| `/ingest/status` | GET | `calculate_sha256` + checksum check | `{already_ingested, source_id}` |
| `/sources` | GET | sources table | `[{id, title, author, chunk_count}]` |
| `/search` | POST | `perform_hybrid_search` | `[{chunk_id, text, location, source_title, source_author, score}]` |
| `/chat` | POST | `perform_hybrid_search`→`format_context`→`generate_completion` | `{answer, citations[]}` |
| `/graph/nodes` | GET | `get_all_concepts` | `[{id, name, definition, category}]` |
| `/graph/edges` | GET | `get_concept_links` | `[{id, source, target, relationship, description}]` |
| `/graph/build` | POST | `build_concept_graph` / `build_cooccurrence_graph` | `{status}` (background job) |
| `/provider` | GET | `LLMClient` attrs | `{provider, embed_model, chat_model, chat_provider}` |
| `/connect` | POST | `connect(client, dry_run)` | `{actions: [string]}` |
| `/connect/status` | GET | `connect(client, dry_run=True)` | `{actions: [string]}` (preview) |
| `/supported-clients` | GET | static | `["claude-code","codex","gemini","antigravity"]` |

Plus a `psyche web` launcher command: boots FastAPI, serves the frontend, opens `localhost`.

### Key data shapes (grounded in the code)
- **ChunkRecord / citation:** `chunk_id, text, location, source_title, source_author` (+ score).
- **Graph node (concept):** `id, name, definition?, category?`.
- **Graph edge (concept_link):** `id, source (name), target (name), relationship, description?`
  — directed, typed; multiple relationship types allowed between the same pair.
- **Source:** `id, title, author?, file_path, checksum (sha256), created_at`.

---

## 7. LLM paths

1. **BYO key (in-app chat):** **OpenAI or Gemini.** ⚠️ `llm_client.py` has **no Anthropic/Claude
   provider** — v1 in-app chat is OpenAI/Gemini only. Adding Claude is a small fast-follow (v2).
2. **Ollama:** fully offline chat + embeddings, no key — keeps "100% local" literally true.
3. **Agent-only:** no key; ingest + graph in the browser, but the user *talks* to Psyche through
   Claude Code / Cursor via MCP.

Embedding and chat providers are independently configurable. A pure local ONNX (fastembed,
`BAAI/bge-small-en-v1.5`) embedding mode exists; "none" mode = FTS-only, no embeddings.

---

## 8. Visual system (the Claude Design brief)

**Direction:** clean light base + restrained violet→cyan "neural" accent + dark-mode toggle.

- **Light:** background `#ffffff`, surface `#f7f8fb`, border `#e7e9ee`, ink `#0f172a`, muted
  `#64748b`.
- **Accent:** violet→cyan gradient `#7c5cff → #22d3ee` (also `#a78bfa`), used **restrained** —
  logo mark, the graph, and key moments only. Headlines stay ink. Buttons solid indigo `#4f46e5`.
- **Dark mode:** canvas `#07060f`, accents glow harder.
- **Gradient glow** (the full effect) is reserved for the **graph explorer**, where it's earned.
- **Type:** crisp system sans for UI; clear hierarchy; generous whitespace; soft shadows.
- **Tone:** premium, calm, confident — "real tool," not "AI startup." (Expressive/heavy-gradient
  was explicitly rejected as looking cheap.)

**Pages for Claude Design to produce as one design system:** Landing, Setup, Upload, Graph
explorer, Chat — light + dark. **Constraint:** only depict data the backend can serve in v1 (§5).

---

## 9. Content / copy

**Hero:** one unifying line + two CTAs — **"Open the app"** and **"Connect your agent."**

**Reusable lines (from existing site/README):**
- "One memory across every AI agent."
- "Your AI assistant has amnesia. You pay for it — in tokens."
- "Not a search box — a memory system."
- "From your files to your agent — entirely on-device."
- "Local-first, or it isn't private."
- "Two commands. Under 60 seconds."
- "Give any AI assistant searchable, cited access to your private notes and documents."

Add an everyone-facing layer: upload your PDFs/notes, explore them as a graph, ask and get cited
answers — all on your own machine.

---

## 10. Naming, brand, commands

- **Brand:** Psyche (unchanged, displayed everywhere).
- **Published package:** **`psyche-mcp`** — the bare `psyche` is taken on both npm and PyPI.
- **New command:** `npx psyche-mcp web` (launches the local web app).
- The README's existing `npx psyche …` install lines get corrected to `psyche-mcp` as part of
  this work.

---

## 11. Testing / verification plan

- **Unit:** the thin API endpoints (mock the core functions where useful); the `psyche web`
  launcher boots and serves.
- **Integration:** ingest a sample PDF → assert chunks + embeddings persisted; `/search` returns
  cited chunks; `/graph/nodes` + `/graph/edges` return the built graph; `/chat` returns an answer
  with citations.
- **End-to-end ("done"):** the ~5-minute stranger loop in §2, verified live in the browser
  (light + dark), including dedup feedback and an empty-state (no docs yet) path.

---

## 12. Open / future (v2)

- Memory/Facts page; Goals/Guidance page (backends exist).
- Deep knowledge-graph analytics.
- Claude/Anthropic as an in-app chat provider.
- Multipage marketing expansion (docs / use-cases) beyond the app shell, if warranted.

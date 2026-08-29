# Changelog

All notable changes to the **Psyche** project will be documented in this file.

## [Unreleased]

### Security
- Local mode no longer invokes Claude CLI for transcript extraction unless `PSYCHE_ALLOW_CLAUDE_CLI_EXTRACTION=1` is explicitly set.
- Setup no longer edits AI client configs, installs background services, adds Git hooks, or creates a global command unless the matching option is requested.
- Background watchers invoke the installed Psyche executable directly instead of the unrelated npm package named `psyche`.

### Changed
- Installation, web UI, and showcase copy now distinguish local data handling from optional Gemini, OpenAI, Ollama, and Claude CLI paths.
- `psyche setup` works from installed distributions such as pipx without attempting to install the current working directory.

## [0.7.0] - 2026-06-12

### Added
- **Host-agent guidance (BYO-model)**: when no chat model is configured, `generate_guidance` now returns a structured `synthesis_pack` (retrieved context + plan schema + instruction) and a new `submit_guidance_plan` MCP tool that validates and materializes the host-agent-authored plan through the existing parser/materialization path — turning the old retrieval-only dead end into a tracked, agent-agnostic protocol. Plans carry a `synthesized_by` provenance field (`host-agent` vs `psyche-llm`) and dedup against recent identical goals.
- **`CHAT_PROVIDER`**: decouples the chat model from the embedding provider (defaults to `LLM_PROVIDER`, so existing configs are unchanged), letting local-embedding users pair an Ollama/Gemini/OpenAI chat model for terminal `psyche guide`.
- **Cache-stable injections**: the session-start memory block is now ordered by immutable `id` and rendered without per-fact dates, making it byte-stable across sessions so it no longer breaks the host model's prompt cache.
- **Per-provider cache-exposure metric**: the token ledger records the session-start block hash and `psyche mem stats` reports how often the cacheable prefix changed across sessions, plus a clearly-labeled modeled savings estimate using a per-provider discount table (Anthropic/OpenAI/Gemini).
- **Measured cache metrics in `psyche mem stats`**: real `cache_read`/`cache_creation` counts read from Claude Code transcripts; replaced the modeled savings figure with measured cache share + block-attributable cost-avoidance; per-project block-change metric.
- **Protocol guidance**: the `psyche connect` protocol block now documents the synthesis-pack flow and append-only placement of memory content.

### Notes
- Single-sourced the version via `mcp_server.__version__`; `pyproject.toml`, `package.json`, and the README badge are manual mirrors (resolves prior 0.4.0/0.5.0/0.6.0 drift).
- No schema migration — `SCHEMA_VERSION` remains 3.

---

## [0.6.0] - 2026-06-12

### Added
- **Guidance Redesign**: Actionable guidance plans via strict JSON parsing with retry, materialization to goals and experiments records, a check-in follow-through loop, graceful degradation for no-chat models, and atomic-memory context injection.
- **Memory Productization**: `psyche connect` for one-command onboarding (Claude Code, Codex, Gemini/Antigravity), project-scoped facts with cwd-derived keys and boosted retrieval, `psyche mem` CLI (list, search, add, delete, prune, stats), token-savings ledger, and contradiction superseding (similarity in [0.80,0.95)) with retrieval-count ranking tiebreak.

---

## [0.5.0] - 2026-06-08

### Added
- **Personal Upgrade & Guidance Layer**: Evolved Psyche beyond RAG into a knowledge-guided decision system. Added structured workflows for Goals, Experiments, Metric tracking, Reviews, and Personal Rules.
- **Guidance Engine**: New `psyche guide` subcommand that synthesizes retrieved knowledge into structured, actionable JSON-based guidance briefs.
- **MCP Guidance Tools**: Added `generate_guidance` and `list_goals_and_experiments` tools to expose the guidance layer to AI assistants.
- **Domain Packs**: Domain-specific heuristics and metrics for business, health, wealth, career, happiness, and ideation.
- **Idea Generation**: Expanded domain detection to include an `ideation` workflow for expanding ideas grounded in knowledge.

---

## [0.3.5] - 2026-06-05

### Added
- **New Document Parsers**: Added native support for parsing **Word DOCX**, **HTML/HTM**, and **Emacs Org-mode** files offline without external Python dependencies.
- **Directory Ingestion Expansion**: Updated directory scanning defaults in `ingest.py` to automatically discover and index `.docx`, `.html`, `.htm`, and `.org` files.

---

## [0.3.4] - 2026-06-05

### Added
- **Local Cross-Encoder Reranking (`flashrank`)**: Fully integrated an offline, CPU-bound ONNX reranker (`ms-marco-TinyBERT-L-2-v2`) to post-process RRF candidates and score relevance.
- **Native SQLite Vector Search (`sqlite-vec`)**: Ingested embeddings into a `vec0` virtual table for highly optimized, C-level semantic match calculations directly inside the SQLite engine.
- **Sub-millisecond ANN Indexing (`usearch`)**: Created a portable HNSW vector index (`knowledge.usearch`) alongside the SQLite database file for $O(\log N)$ semantic retrieval.
- **Dynamic Retrieval Tiering**: Fallback logic gracefully downgrades from `usearch` index searches to `sqlite-vec` MATCH queries, then to NumPy CPU matrices, and finally to pure FTS5 BM25.
- **First-Class Python Installation**: Added configuration and instructions for `pipx install git+https://github.com/Nam-Aniket/psyche.git`.
- **Discovery Keywords/Topics**: Added rich topic list to `package.json` for enhanced search discoverability (`mcp`, `second-brain`, `graphrag`, `local-first`, `pdf-rag`, `ollama`, etc.).

### Changed
- **Branding Renaming**: Unified naming mismatch, renaming all repo and package configurations to `psyche`.
- **Refactored Descriptions**: Replaced risky marketing terms like "premium" with concrete technical descriptors ("high-performance").

---

## [0.3.3] - 2026-06-04

### Changed
- **Vectorized Similarity**: NumPy-vectorized similarity calculations in `query.py` to replace sequential python loops, reducing CPU overhead during flat scans.

---

## [0.3.2] - 2026-06-04

### Added
- **BM25 FTS5 Keyword Scoring**: Switched SQLite keyword search to native FTS5 `bm25()` rank scoring.

---

## [0.3.1] - 2026-06-03

### Changed
- **Decoupled Text Retrieval**: Implemented separation of chunk texts from embedding vectors during retrieval, reducing memory load from 100MB+ to under 5MB.

---

## [0.3.0] - 2026-05-20

### Added
- **Multi-Path Ingestion**: Scan and sync multiple directories or files simultaneously (e.g. `psyche ingest ~/Vault1 ~/Books`).
- **Metadata Check Migration**: Detect embedding dimension changes and prompt/run automatic database migrations.

---

## [0.2.0] - 2026-04-10

### Added
- **AI-Free Fallbacks**: Statistically co-occurring proper-noun graph builder to construct concept links offline.
- **Interactive Chat REPL**: Command-line chat session with prompt history and command completion.

---

## [0.1.0-alpha] - 2026-03-01

### Added
- **Initial Core Implementation**: Parsers for Obsidian MD (wikilinks/frontmatter), EPUB, and PDF.
- **Hybrid RRF Search**: Merging keyword matching and semantic embeddings via Reciprocal Rank Fusion.
- **Model Context Protocol (MCP)**: Exposed tools `search_knowledge` and `retrieve_graph` to MCP-capable assistants.

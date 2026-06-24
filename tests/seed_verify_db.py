"""Seed a throwaway DB with demo concepts/links/sources for visual verification
of the web frontend. Not part of the test suite; safe to delete.

Usage:  python tests/seed_verify_db.py /tmp/psyche_verify.db
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import init_db, get_connection, add_source, add_chunk, add_concept, add_concept_link

CONCEPTS = [
    ("KV Cache", "caching", "Per-token Key/Value vectors the model computes to hold context. Writing them is ~90% of the compute; reading them back is nearly free."),
    ("Prompt Caching", "caching", "Reuse the KV vectors of a byte-identical prefix from a prior call, billed at the read rate instead of recomputing from scratch."),
    ("Stable-Prefix Layout", "caching", "Keep changing content at the end of the prompt so the cached prefix stays byte-identical from turn to turn."),
    ("Append-Only Discipline", "caching", "Never edit or reorder what was already sent. A prompt is an append-only log, not a document you revise in place."),
    ("Cache-Aware Context Assembly", "caching", "Jointly optimize token reduction and cache reuse, the gap most memory tools leave open by injecting changing content early."),
    ("TTL Eviction", "caching", "GPU-resident caches are dropped after roughly five minutes idle; the prefix then re-writes at full price on the next call."),
    ("Atomic Memory", "memory", "Deduplicated one-sentence facts, hybrid-retrieved and shared across agents, but cache-breaking if injected at the top of the prompt."),
    ("Reciprocal Rank Fusion", "retrieval", "Merges lexical (BM25) and semantic (vector) rankings into one fused order without tuning score scales."),
    ("HNSW Vector Search", "retrieval", "Approximate nearest-neighbour search over embeddings via a navigable small-world graph."),
    ("FTS5 BM25", "retrieval", "SQLite full-text keyword search scoring lexical relevance with the BM25 ranking function."),
    ("Cross-Encoder Reranking", "retrieval", "A lightweight ONNX model that rescores the fused candidate set on CPU for a sharper final order."),
    ("First Principles Thinking", "models", "Identify the elements of a situation that are non-reducible, then reason upward from them."),
    ("Inversion", "models", "Invert, always invert: design the worst possible approach to surface the failure modes you must avoid."),
    ("Map is not Territory", "models", "The model of reality is not reality; every map is a lossy reduction of what it represents."),
    ("Feynman Technique", "learning", "Explain a concept step-by-step in plain language; where you stumble, return to the source material."),
    ("Interleaving", "learning", "Mix problem types so you practise choosing the right approach, not merely executing one in isolation."),
    ("Structure Building", "learning", "Extract the salient ideas and build a coherent mental scaffold to hang new detail on."),
]

LINKS = [
    ("Prompt Caching", "KV Cache", "reuses"),
    ("Stable-Prefix Layout", "Prompt Caching", "preserves"),
    ("Append-Only Discipline", "Stable-Prefix Layout", "implements"),
    ("Atomic Memory", "Prompt Caching", "breaks"),
    ("Cache-Aware Context Assembly", "Atomic Memory", "reconciles"),
    ("Cache-Aware Context Assembly", "Stable-Prefix Layout", "unifies"),
    ("TTL Eviction", "KV Cache", "invalidates"),
    ("Reciprocal Rank Fusion", "FTS5 BM25", "fuses"),
    ("Reciprocal Rank Fusion", "HNSW Vector Search", "fuses"),
    ("Cross-Encoder Reranking", "Reciprocal Rank Fusion", "rescores"),
    ("Atomic Memory", "Reciprocal Rank Fusion", "retrieved by"),
    ("First Principles Thinking", "KV Cache", "grounds"),
    ("Inversion", "Cache-Aware Context Assembly", "stress-tests"),
    ("Map is not Territory", "Prompt Caching", "qualifies"),
    ("Structure Building", "Cache-Aware Context Assembly", "scaffolds"),
    ("Interleaving", "Atomic Memory", "contrasts"),
    ("Feynman Technique", "First Principles Thinking", "teaches"),
]

SOURCES = [
    ("The Great Mental Models, Vol. 1", "Shane Parrish", 24),
    ("Make It Stick", "Brown, Roediger & McDaniel", 18),
    ("Learn Like a Pro", "Oakley & Schewe", 12),
    ("prompt-caching-and-context-assembly.md", "Psyche · teaching", 14),
    ("why-cached-tokens-cost-less.md", "Psyche · teaching", 9),
    ("MEMORY.md", "Psyche · project", 6),
]


def main(path):
    if os.path.exists(path):
        os.remove(path)
    init_db(path)
    conn = get_connection(path)
    for i, (title, author, n_chunks) in enumerate(SOURCES):
        sid = add_source(conn, title, author, f"/seed/{title}", f"seed-checksum-{i}")
        for j in range(n_chunks):
            add_chunk(conn, sid, j, f"Demo chunk {j} of {title}.", location=f"p.{j + 1}")
    for name, cat, definition in CONCEPTS:
        add_concept(conn, name, definition=definition, category=cat)
    for src, tgt, rel in LINKS:
        add_concept_link(conn, src, tgt, rel, description=f"{src} {rel} {tgt}")
    conn.commit()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ["sources", "chunks", "concepts", "concept_links"]
    }
    conn.close()
    print(f"Seeded {path}: {counts}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/psyche_verify.db")

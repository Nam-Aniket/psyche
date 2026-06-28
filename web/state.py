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


def load_search_structures(resolved: str, llm):
    """Load the in-memory search structures (usearch index or embeddings matrix)
    for a database. Shared by build_state (startup) and refresh_search_state
    (after a live ingest/delete), so the running server reflects new content
    without a process restart.
    """
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

    return chunk_ids, embeddings_matrix, usearch_index


def refresh_search_state(state: AppState) -> AppState:
    """Reload the in-memory search structures on an existing AppState in place.

    Called after a web ingest or delete so /search and /chat immediately see the
    change — no process restart required.
    """
    chunk_ids, embeddings_matrix, usearch_index = load_search_structures(state.db_path, state.llm)
    state.chunk_ids = chunk_ids
    state.embeddings_matrix = embeddings_matrix
    state.usearch_index = usearch_index
    return state


def build_state_for_topic(topic: str, llm) -> AppState:
    """Build an AppState for a named topic (topic_<topic>.db), reusing the shared
    LLMClient. Mirrors build_state but skips LLM construction. A topic whose DB
    doesn't exist yet yields an empty (but valid) state."""
    resolved = resolve_db_path(f"topic_{topic}.db")
    check_and_migrate_embeddings(resolved, llm)
    chunk_ids, embeddings_matrix, usearch_index = load_search_structures(resolved, llm)
    return AppState(
        db_path=resolved,
        llm=llm,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        usearch_index=usearch_index,
    )


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

    chunk_ids, embeddings_matrix, usearch_index = load_search_structures(resolved, llm)

    return AppState(
        db_path=resolved,
        llm=llm,
        chunk_ids=chunk_ids,
        embeddings_matrix=embeddings_matrix,
        usearch_index=usearch_index,
    )

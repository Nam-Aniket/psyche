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

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from query import perform_hybrid_search
from web.deps import get_state_for

router = APIRouter()

_DEFAULT_LIMIT = 5


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query_text: str
    limit: int = _DEFAULT_LIMIT
    topic: str | None = None


class SearchResult(BaseModel):
    chunk_id: int
    text: str
    location: str | None
    source_title: str
    source_author: str | None  # null when a source has no known author
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
    st = get_state_for(request, req.topic)

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


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

from query import format_context, retrieve_concept_context
from db import get_connection


class ChatRequest(BaseModel):
    query_text: str
    limit: int = _DEFAULT_LIMIT
    topic: str | None = None


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
    st = get_state_for(request, req.topic)

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

    # 3. Optionally enrich with concept-graph context (mirrors query.py)
    conn = get_connection(st.db_path)
    try:
        graph_context = retrieve_concept_context(conn, req.query_text)
    finally:
        conn.close()

    full_context = context_str
    if graph_context:
        full_context = f"{graph_context}\n\n---\n\n{context_str}"

    # 4. Build prompt (mirrors query.py single-query mode)
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

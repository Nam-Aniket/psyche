import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, Request
from pydantic import BaseModel
from web.deps import get_state_for

router = APIRouter()


class MemorySearchRequest(BaseModel):
    query: str
    top: int = 12
    topic: str | None = None


@router.get("/memory/list")
def memory_list(request: Request, limit: int = 100, category: str = None, project: str = None, topic: str = None):
    """Lists the auto-captured atomic memories (live, newest first)."""
    import memzero
    st = get_state_for(request, topic)
    return memzero.list_memories(limit=limit, category=category, project=project, db_path=st.db_path)


@router.post("/memory/search")
def memory_search(body: MemorySearchRequest, request: Request):
    """Hybrid semantic + keyword search over the memory store."""
    import memzero
    st = get_state_for(request, body.topic)
    return memzero.search_memories(body.query, top=body.top, db_path=st.db_path, llm=st.llm)


@router.get("/memory/stats")
def memory_stats(request: Request, topic: str = None):
    """Counts by category/project for the memory store header."""
    import memzero
    st = get_state_for(request, topic)
    return memzero.stats(db_path=st.db_path)


@router.get("/memory/graph")
def memory_graph(request: Request, topic: str = None):
    """Entity co-occurrence graph over memories, in the concept-graph shape."""
    import memzero
    st = get_state_for(request, topic)
    return memzero.entity_graph(db_path=st.db_path)

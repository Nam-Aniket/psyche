import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class AppState:
    """Shared application state loaded once at startup and stored on app.state.psyche."""

    db_path: str
    llm: Any  # LLMClient or compatible duck-type (FakeLLM in tests)
    chunk_ids: np.ndarray
    embeddings_matrix: np.ndarray
    usearch_index: Optional[Any] = None  # usearch.index.Index or None


def get_state(request: Request) -> AppState:
    """Dependency helper: retrieves the default (no-topic) AppState."""
    return request.app.state.psyche


def get_state_for(request: Request, topic: Optional[str] = None) -> AppState:
    """Returns the AppState for the given topic, building and caching it on first
    use. Empty/None topic → the default state. Topic names are validated to a safe
    charset (they become a `topic_<name>.db` filename), so an invalid topic is a
    400, not a path-traversal. Per-topic states share the single LLMClient."""
    base = request.app.state.psyche
    if not topic:
        return base
    if not _TOPIC_RE.match(topic):
        raise HTTPException(status_code=400, detail=f"Invalid topic name: {topic!r}")

    cache = getattr(request.app.state, "psyche_topics", None)
    if cache is None:
        cache = {}
        request.app.state.psyche_topics = cache
    if topic not in cache:
        from web.state import build_state_for_topic
        cache[topic] = build_state_for_topic(topic, base.llm)
    return cache[topic]


class ErrorResponse(BaseModel):
    """Standard error envelope returned by the top-level exception handler."""

    detail: str

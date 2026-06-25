import os
import sys
from dataclasses import dataclass
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

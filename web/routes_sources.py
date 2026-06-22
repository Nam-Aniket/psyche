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

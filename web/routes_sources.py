import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, Request, HTTPException, File, Form, UploadFile
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


# ── Request / Response models for ingest ──────────────────────────────────────

class IngestRequest(BaseModel):
    path: str
    title: Optional[str] = None
    author: Optional[str] = None
    force: bool = False


class IngestResponse(BaseModel):
    source_id: int
    chunk_count: int
    skipped: bool


# ── POST /ingest ───────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=IngestResponse)
def ingest_file(body: IngestRequest, request: Request):
    """Ingest a server-side file by absolute path.

    Steps mirror ingest.py main() but without rich/Console/sys.exit.
    After writing to SQLite the on-disk usearch index is rebuilt; the
    in-memory st.chunk_ids / st.embeddings_matrix are NOT refreshed —
    a process restart (or re-hit to the state loader) is required for
    newly ingested content to appear in search results.
    """
    from ingest import calculate_sha256, chunk_text, clean_title_from_filename
    from parsers import extract_text
    from db import (
        get_connection as _get_conn,
        check_checksum,
        add_source,
        add_chunk,
        add_embedding,
        build_or_update_usearch_index,
        remove_source,
    )

    st = get_state(request)
    path = body.path

    # ── 1. File existence / extension ────────────────────────────────────────
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        blocks = extract_text(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        # Unsupported extension
        raise HTTPException(status_code=400, detail=str(exc))

    if not blocks:
        raise HTTPException(status_code=400, detail="No text could be extracted from the file.")

    # ── 2. Checksum / dedup ───────────────────────────────────────────────────
    checksum = calculate_sha256(path)
    conn = _get_conn(st.db_path)
    try:
        existing_id = check_checksum(conn, checksum)
        if existing_id is not None and not body.force:
            return IngestResponse(source_id=existing_id, chunk_count=0, skipped=True)

        if existing_id is not None and body.force:
            remove_source(conn, existing_id, db_path=st.db_path)

        # ── 3. Chunk ──────────────────────────────────────────────────────────
        chunks = []
        for block in blocks:
            for c in chunk_text(block["text"]):
                chunks.append({"text": c, "location": block["location"]})

        if not chunks:
            raise HTTPException(status_code=400, detail="No text chunks created — content too short.")

        # ── 4. Embeddings (skipped when provider == 'none') ───────────────────
        embeddings = []
        if st.llm.provider not in ("none",):
            try:
                embeddings = st.llm.get_embeddings_batch([c["text"] for c in chunks])
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Embedding generation failed: {exc}")

        # ── 5. Persist ────────────────────────────────────────────────────────
        title = body.title or clean_title_from_filename(path)
        author = body.author or "Unknown"

        source_id = add_source(conn, title, author, path, checksum)
        for idx, chunk_data in enumerate(chunks):
            cid = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
            if embeddings:
                add_embedding(conn, cid, embeddings[idx])
    finally:
        conn.close()

    # ── 6. Rebuild on-disk index (process restart needed to refresh in-memory state) ─
    build_or_update_usearch_index(st.db_path)

    return IngestResponse(source_id=source_id, chunk_count=len(chunks), skipped=False)


# ── POST /ingest/upload ────────────────────────────────────────────────────────

@router.post("/ingest/upload", response_model=IngestResponse)
async def ingest_upload(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    author: Optional[str] = Form(None),
    force: bool = Form(False),
):
    """Ingest a file sent as a multipart upload.

    The uploaded bytes are written to a temp file, then the same pipeline
    as POST /ingest runs, and the temp file is cleaned up afterwards.

    Note: title/author/force are declared with Form() so they bind from
    multipart form fields (a plain default would make them query params).
    """
    import tempfile as _tempfile
    from ingest import calculate_sha256, chunk_text, clean_title_from_filename
    from parsers import extract_text
    from db import (
        get_connection as _get_conn,
        check_checksum,
        add_source,
        add_chunk,
        add_embedding,
        build_or_update_usearch_index,
        remove_source,
    )

    original_filename = file.filename or "upload.txt"
    ext = os.path.splitext(original_filename)[1].lower()
    if not ext:
        ext = ".txt"

    # Write upload to a named temp file so extract_text (which needs a path) works.
    with _tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        st = get_state(request)

        try:
            blocks = extract_text(tmp_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if not blocks:
            raise HTTPException(status_code=400, detail="No text could be extracted from the uploaded file.")

        checksum = calculate_sha256(tmp_path)
        conn = _get_conn(st.db_path)
        try:
            existing_id = check_checksum(conn, checksum)
            if existing_id is not None and not force:
                return IngestResponse(source_id=existing_id, chunk_count=0, skipped=True)

            if existing_id is not None and force:
                remove_source(conn, existing_id, db_path=st.db_path)

            chunks = []
            for block in blocks:
                for c in chunk_text(block["text"]):
                    chunks.append({"text": c, "location": block["location"]})

            if not chunks:
                raise HTTPException(status_code=400, detail="No text chunks created — content too short.")

            embeddings = []
            if st.llm.provider not in ("none",):
                try:
                    embeddings = st.llm.get_embeddings_batch([c["text"] for c in chunks])
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"Embedding generation failed: {exc}")

            resolved_title = title or clean_title_from_filename(original_filename)
            resolved_author = author or "Unknown"

            source_id = add_source(conn, resolved_title, resolved_author, original_filename, checksum)
            for idx, chunk_data in enumerate(chunks):
                cid = add_chunk(conn, source_id, idx, chunk_data["text"], location=chunk_data["location"])
                if embeddings:
                    add_embedding(conn, cid, embeddings[idx])
        finally:
            conn.close()

        build_or_update_usearch_index(st.db_path)
        return IngestResponse(source_id=source_id, chunk_count=len(chunks), skipped=False)

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

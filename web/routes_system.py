"""
web/routes_system.py — GET /provider, POST /connect, GET /connect/status,
GET /supported-clients.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from web.deps import get_state

router = APIRouter()

_SUPPORTED_CLIENTS = ["claude-code", "codex", "gemini", "antigravity"]


class ConnectRequest(BaseModel):
    client: str
    dry_run: Optional[bool] = False


class ConnectResponse(BaseModel):
    actions: list[str]


class ProviderResponse(BaseModel):
    provider: str
    chat_provider: str
    embed_model: str
    chat_model: str
    db_path: str


@router.get("/provider", response_model=ProviderResponse)
def get_provider(request: Request):
    st = get_state(request)
    return ProviderResponse(
        provider=st.llm.provider,
        chat_provider=getattr(st.llm, "chat_provider", st.llm.provider),
        embed_model=st.llm.embed_model,
        chat_model=st.llm.chat_model,
        db_path=st.db_path,
    )


@router.post("/connect", response_model=ConnectResponse)
def post_connect(body: ConnectRequest, request: Request):
    from connect import connect
    try:
        actions = connect(body.client, dry_run=bool(body.dry_run))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ConnectResponse(actions=actions)


@router.get("/connect/status", response_model=ConnectResponse)
def get_connect_status(client: str, request: Request):
    from connect import connect
    try:
        actions = connect(client, dry_run=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ConnectResponse(actions=actions)


@router.get("/supported-clients")
def get_supported_clients():
    return _SUPPORTED_CLIENTS

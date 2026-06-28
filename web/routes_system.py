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


class ProviderConfig(BaseModel):
    chat_provider: str  # one of: none, ollama, openai, gemini
    api_key: Optional[str] = None
    chat_model: Optional[str] = None


_CHAT_PROVIDERS = {"none", "ollama", "openai", "gemini"}


def _write_env(updates: dict) -> str:
    """Merge key/value updates into ~/.psyche/.env, preserving existing lines."""
    from llm_client import resolve_env_path

    path = resolve_env_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing: dict[str, str] = {}
    order: list[str] = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                k = k.strip()
                if k not in existing:
                    order.append(k)
                existing[k] = v.strip()
    for k, v in updates.items():
        if k not in existing:
            order.append(k)
        existing[k] = v
    with open(path, "w") as f:
        for k in order:
            f.write(f"{k}={existing[k]}\n")
    return path


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


@router.post("/provider", response_model=ProviderResponse)
def set_provider(body: ProviderConfig, request: Request):
    """Configure the CHAT provider from the browser. Embeddings always stay
    local (LLM_PROVIDER=local) so no re-embedding is ever triggered. Hot-swaps
    the live LLMClient so /chat works immediately without a restart.
    """
    chat_provider = (body.chat_provider or "none").lower()
    if chat_provider not in _CHAT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported chat provider: {chat_provider}")

    st = get_state(request)
    from llm_client import resolve_env_path

    # Pre-validate that a cloud provider will have a usable key (from the request
    # or already stored) BEFORE touching .env, so a rejected request can never
    # leave the env in a state that bricks the next startup.
    if chat_provider in ("gemini", "openai"):
        env_key = "GEMINI_API_KEY" if chat_provider == "gemini" else "OPENAI_API_KEY"
        if not (body.api_key and body.api_key.strip()) and not os.getenv(env_key):
            raise HTTPException(status_code=400, detail=f"{chat_provider} chat needs an API key.")

    # Keep embeddings local; only change chat wiring.
    updates = {"LLM_PROVIDER": "local", "CHAT_PROVIDER": chat_provider}
    if body.chat_model:
        updates["CHAT_MODEL"] = body.chat_model
    if body.api_key and body.api_key.strip():
        if chat_provider == "gemini":
            updates["GEMINI_API_KEY"] = body.api_key.strip()
        elif chat_provider == "openai":
            updates["OPENAI_API_KEY"] = body.api_key.strip()

    # Back up the existing .env so we can roll back if the new config is invalid.
    env_path = resolve_env_path()
    prev_bytes = None
    if os.path.exists(env_path):
        with open(env_path, "rb") as f:
            prev_bytes = f.read()

    _write_env(updates)

    from dotenv import load_dotenv
    from llm_client import LLMClient
    load_dotenv(env_path, override=True)

    try:
        new_llm = LLMClient()
    except ValueError as exc:
        # Roll back the env and reload the previous config.
        if prev_bytes is not None:
            with open(env_path, "wb") as f:
                f.write(prev_bytes)
        elif os.path.exists(env_path):
            os.remove(env_path)
        load_dotenv(env_path, override=True) if os.path.exists(env_path) else None
        raise HTTPException(status_code=400, detail=str(exc))

    st.llm = new_llm
    from web.state import refresh_search_state
    refresh_search_state(st)

    return ProviderResponse(
        provider=new_llm.provider,
        chat_provider=getattr(new_llm, "chat_provider", new_llm.provider),
        embed_model=new_llm.embed_model,
        chat_model=new_llm.chat_model,
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


@router.get("/topics")
def get_topics():
    """Lists available topic libraries: the default (knowledge.db) plus every
    topic_<name>.db in the Psyche data dir, with a cheap source count each.
    Powers the global topic switcher."""
    import sqlite3
    from db import resolve_db_path

    default_db = resolve_db_path("knowledge.db")
    data_dir = os.path.dirname(default_db)

    def _count_sources(path):
        try:
            conn = sqlite3.connect(path)
            try:
                return conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

    topics = [{"name": "", "label": "Default", "is_default": True,
               "sources": _count_sources(default_db)}]
    try:
        for fn in sorted(os.listdir(data_dir)):
            if fn.startswith("topic_") and fn.endswith(".db"):
                name = fn[len("topic_"):-len(".db")]
                topics.append({"name": name, "label": name, "is_default": False,
                               "sources": _count_sources(os.path.join(data_dir, fn))})
    except OSError:
        pass
    return topics

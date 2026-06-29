"""Shared helpers for Psyche Claude Code hooks.

Hooks must never break the user's session: every entry point swallows all
exceptions and exits 0. Debug output goes to ~/.psyche/memzero_hook.log.
"""
import hashlib
import json
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
os.environ.setdefault("PSYCHE_NONINTERACTIVE", "1")

LOG_PATH = os.path.expanduser("~/.psyche/memzero_hook.log")


def recursion_guard():
    """Exits immediately when running inside a headless claude spawned by a
    hook (PSYCHE_MEM_HOOK=1), so extraction can't trigger hooks recursively."""
    if os.environ.get("PSYCHE_MEM_HOOK") == "1":
        sys.exit(0)


def log(msg: str):
    try:
        import time
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a") as f:
            f.write(f"{ts} {msg.rstrip()}\n")
    except Exception:
        pass


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def cwd_from_payload(payload) -> str | None:
    return payload.get("cwd") or payload.get("workspace") or None


MEM_LEDGER_PATH = os.path.expanduser("~/.psyche/mem_ledger.jsonl")


def append_ledger(event: str, session_id: str, count: int, chars: int, path: str = None,
                  block_hash: str = None, cwd: str = None):
    """Appends one JSON line: {ts, event, session_id, count, chars[, block_hash][, cwd]}.
    block_hash and cwd are included only when provided (never written as null).
    Swallows all errors (hooks must never break)."""
    from datetime import datetime, timezone
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": session_id,
            "count": count,
            "chars": chars,
        }
        if block_hash is not None:
            entry["block_hash"] = block_hash
        if cwd is not None:
            entry["cwd"] = cwd
        with open(path or MEM_LEDGER_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def ledger_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return f"/tmp/psyche_mem_ledger_{safe}.json"


def read_ledger(session_id: str) -> set:
    try:
        with open(ledger_path(session_id)) as f:
            return set(json.load(f))
    except Exception:
        return set()


def stable_block_hash(text: str) -> str:
    """SHA-1 hex digest (12 chars) of the injection text — used as a cache-exposure key."""
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def write_ledger(session_id: str, ids: set):
    try:
        with open(ledger_path(session_id), "w") as f:
            json.dump(sorted(ids), f)
    except Exception:
        pass


def _extract_state_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return os.path.expanduser(f"~/.psyche/sessions/{safe}.extract.json")


def read_extract_state(session_id: str) -> dict:
    """Per-session watermark for incremental (Stop-hook) extraction.
    Returns {} on first run / any failure — the gate treats this as 'never extracted'."""
    try:
        with open(_extract_state_path(session_id)) as f:
            state = json.load(f)
            return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def write_extract_state(session_id: str, state: dict):
    """Writes the extraction watermark via a temp file + atomic rename."""
    try:
        p = _extract_state_path(session_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, p)
    except Exception:
        pass

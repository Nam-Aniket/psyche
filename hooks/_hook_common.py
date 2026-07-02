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
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
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


PSYCHE_CONFIG_PATH = os.path.expanduser("~/.psyche/config.json")


def read_config() -> dict:
    """~/.psyche/config.json as a dict; {} on missing/malformed. PSYCHE_CONFIG
    env var overrides the path (used by tests)."""
    try:
        with open(os.environ.get("PSYCHE_CONFIG") or PSYCHE_CONFIG_PATH) as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def auto_capture_topics(config: dict = None) -> list:
    """Topics whose sessions get default-on conversation capture. The naval
    topic is on by default; set auto_capture_topics: [] in config to disable.
    Topic names are lowercased and restricted to [a-z0-9_-] (they become part
    of a topic_<name>.db filename)."""
    cfg = read_config() if config is None else config
    topics = cfg.get("auto_capture_topics", ["naval"])
    if not isinstance(topics, list):
        topics = ["naval"]
    safe = []
    for t in topics:
        t = str(t).lower()
        if t and all(c.isalnum() or c in "-_" for c in t):
            safe.append(t)
    return safe


def topic_for_cwd(cwd: str, topics: list) -> str | None:
    """First topic whose name equals a path component of cwd (case-insensitive),
    else None. This is how a session declares itself topic-scoped: it runs
    inside a directory named after the topic (e.g. ~/Downloads/NAVAL)."""
    if not cwd or not topics:
        return None
    parts = {p.lower() for p in os.path.normpath(cwd).split(os.sep) if p}
    for t in topics:
        if t in parts:
            return t
    return None


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


EXTRACT_HEALTH_PATH = os.path.expanduser("~/.psyche/extract_health.json")


def write_extract_health(ok: bool, error: str = None):
    """Records the outcome of the last extraction LLM call so session_start
    can warn the user instead of extraction failing silently for days."""
    from datetime import datetime, timezone
    try:
        entry = {"ok": ok, "ts": datetime.now(timezone.utc).isoformat()}
        if error:
            entry["error"] = error[:200]
        with open(EXTRACT_HEALTH_PATH, "w") as f:
            json.dump(entry, f)
    except Exception:
        pass


def read_extract_health() -> dict:
    try:
        with open(EXTRACT_HEALTH_PATH) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


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

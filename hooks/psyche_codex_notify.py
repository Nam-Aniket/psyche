"""Codex notify hook: chains the user's existing notify program AND, on a time
gate, captures the last assistant turn into the shared memory store.

Codex's `notify` fires on 'agent-turn-complete' with a single JSON argv that
carries only `last-assistant-message` / `input-messages` (NO transcript file),
so this is a lower-fidelity capture than the Claude/Gemini transcript hooks —
but it keeps Codex memories flowing automatically into the same DB
(agent_id=codex), readable by every other agent via search_memories.

The user's prior notify command (e.g. Computer-Use) is preserved: connect.py
records it in ~/.psyche/codex_notify_chain.json and this wrapper invokes it
first, passing the same argv through, so existing notifications keep working.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import _hook_common as hc

CHAIN_FILE = os.path.expanduser("~/.psyche/codex_notify_chain.json")
MIN_MINUTES = 10


def _chain_original(args):
    """Invoke the user's pre-existing notify program so it keeps working.
    Fire-and-forget; never raises."""
    try:
        with open(CHAIN_FILE) as f:
            orig = json.load(f).get("notify")
        if isinstance(orig, list) and orig and orig[0]:
            subprocess.Popen([*orig, *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _run_worker():
    """Detached worker: extract + store from the captured turn, then exit."""
    path = os.environ.get("PSYCHE_CODEX_WORKER", "")
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return
    try:
        import memzero
        from psyche_extract import _resolve_llm
        llm = _resolve_llm()
        project = memzero.project_key_for(d.get("cwd"))
        stored = memzero.extract_and_store(d["text"], agent_id="codex",
                                           run_id=d.get("thread"), project=project, llm=llm)
        hc.log(f"codex extract {d.get('thread')}: stored {len(stored)} facts")
    except Exception as e:
        hc.log(f"codex extract failed: {e}")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    if os.environ.get("PSYCHE_CODEX_WORKER"):
        _run_worker()
        return

    args = sys.argv[1:]
    _chain_original(args)  # keep the user's existing notify working

    try:
        payload = json.loads(args[0]) if args else {}
    except Exception:
        return
    if payload.get("type") != "agent-turn-complete":
        return

    thread = str(payload.get("thread-id") or payload.get("turn-id") or "codex")
    key = "codex-" + "".join(c for c in thread if c.isalnum() or c in "-_")
    state = hc.read_extract_state(key)
    now = time.time()
    last_ts = state.get("last_ts")
    if last_ts is not None and (now - last_ts) < MIN_MINUTES * 60:
        return  # time-gate: don't run the LLM extractor on every single turn

    inputs = payload.get("input-messages") or []
    last = payload.get("last-assistant-message") or ""
    parts = ["user: " + str(m) for m in inputs]
    if last:
        parts.append("assistant: " + str(last))
    text = "\n\n".join(parts).strip()
    if not text:
        return

    hc.write_extract_state(key, {"last_ts": now})
    try:
        fd, tmp = tempfile.mkstemp(prefix="psyche_codex_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"text": text, "thread": thread, "cwd": payload.get("cwd")}, f)
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            env={**os.environ, "PSYCHE_CODEX_WORKER": tmp},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, cwd="/tmp",
        )
        hc.log(f"codex notify {thread}: gate passed, worker spawned")
    except Exception as e:
        hc.log(f"codex notify worker spawn failed: {e}")


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"codex notify error: {e}")
    sys.exit(0)

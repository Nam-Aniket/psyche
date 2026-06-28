"""Stop hook: incremental, gated mid-session fact extraction.

Fires at the end of every assistant turn. Cheap by default — it only runs the
(slow) LLM extraction when enough turns OR enough wall-clock time have elapsed
since the last extraction, so memories are captured even if the user never
cleanly exits (no /exit, abrupt close, SIGKILL, or walking away for days).

When the gate passes, extraction runs in a DETACHED worker process so the hook
returns immediately and never blocks the user's next prompt. Outcome
classification is intentionally NOT done here — that final verdict stays on the
SessionEnd/PreCompact path (psyche_extract.py). The near-duplicate guard in
extract_and_store makes overlapping extraction windows safe.

Gating env vars (all optional, sane defaults):
  PSYCHE_STOP_MIN_TURNS    assistant turns between extractions   (default 4)
  PSYCHE_STOP_MIN_MINUTES  wall-clock fallback, minutes          (default 10)
  PSYCHE_STOP_MIN_GROWTH   min transcript growth, chars          (default 800)
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import _hook_common as hc
from psyche_extract import count_turns, extract_facts, transcript_text


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def should_extract(*, now, last_ts, turn_count, last_turn_count,
                   transcript_len, last_len,
                   min_turns, min_minutes, min_growth_chars) -> bool:
    """Pure gate. Returns True when an incremental extraction is warranted.

    last_ts is None on the first extraction of a session. The timer path
    bypasses the growth check on purpose: a transcript window that has saturated
    at MAX_TRANSCRIPT_CHARS stops growing, and we must still keep capturing."""
    grew = (transcript_len - last_len) >= min_growth_chars
    if last_ts is None:
        return turn_count >= min_turns and grew
    if (now - last_ts) >= min_minutes * 60:
        return True
    return (turn_count - last_turn_count) >= min_turns and grew


def _run_worker():
    """Detached worker: do the actual (slow) extraction, then exit."""
    payload_path = os.environ.get("PSYCHE_STOP_WORKER", "")
    try:
        with open(payload_path) as f:
            payload = json.load(f)
    except Exception:
        return
    try:
        extract_facts(payload, source="stop")
    finally:
        try:
            os.unlink(payload_path)
        except Exception:
            pass


def main():
    # Detached worker mode: spawned by a prior foreground run below.
    if os.environ.get("PSYCHE_STOP_WORKER"):
        _run_worker()
        return

    payload = hc.read_payload()
    if payload.get("stop_hook_active"):
        return  # re-entrant Stop guard
    session_id = payload.get("session_id", "")
    path = payload.get("transcript_path", "")
    if not path or not os.path.exists(path):
        return

    min_turns = _int_env("PSYCHE_STOP_MIN_TURNS", 4)
    min_minutes = _int_env("PSYCHE_STOP_MIN_MINUTES", 10)
    min_growth = _int_env("PSYCHE_STOP_MIN_GROWTH", 800)

    state = hc.read_extract_state(session_id)
    turn_count = count_turns(path)
    transcript_len = len(transcript_text(path))
    now = time.time()

    if not should_extract(
        now=now,
        last_ts=state.get("last_ts"),
        turn_count=turn_count,
        last_turn_count=state.get("last_turn_count", 0),
        transcript_len=transcript_len,
        last_len=state.get("last_len", 0),
        min_turns=min_turns,
        min_minutes=min_minutes,
        min_growth_chars=min_growth,
    ):
        return

    # Gate passed: spawn a detached worker, advance the watermark immediately
    # (prevents the next few turns from each re-triggering), and return fast.
    try:
        fd, tmp = tempfile.mkstemp(prefix="psyche_stop_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            env={**os.environ, "PSYCHE_STOP_WORKER": tmp},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, cwd="/tmp",
        )
        hc.write_extract_state(session_id, {
            "last_ts": now,
            "last_turn_count": turn_count,
            "last_len": transcript_len,
        })
        hc.log(f"stop {session_id}: gate passed, worker spawned "
               f"(turns={turn_count}, len={transcript_len})")
    except Exception as e:
        hc.log(f"stop {session_id}: worker spawn failed: {e}")


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"stop error: {e}")
    sys.exit(0)

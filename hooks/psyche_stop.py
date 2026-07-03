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


# If a spawned worker never clears the in-flight flag (killed, EPERM, hang),
# the session unwedges after this many seconds and the next Stop retries.
INFLIGHT_TTL = 300


def iter_recall_messages(path):
    """Yields (role, text) for each user/assistant message with text content,
    in transcript order. Same content extraction as transcript_text."""
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") not in ("user", "assistant"):
                    continue
                message = entry.get("message") or {}
                role = message.get("role", entry["type"])
                content = message.get("content")
                if isinstance(content, str):
                    texts = [content]
                elif isinstance(content, list):
                    texts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                else:
                    texts = []
                text = "\n".join(t for t in texts if t).strip()
                if text:
                    yield role, text
    except Exception:
        return


def capture_interactions(payload) -> int:
    """Default-on conversation capture for auto-capture topics (the naval
    decision engine): when the session runs inside a directory named after a
    captured topic, every new user/assistant message since the session's
    capture watermark is persisted into that topic's memory_recall table.

    Local SQLite inserts only — cheap enough to run inline on every Stop with
    no gate. Inserts directly via db (not mcp_server.record_interaction_tool:
    importing mcp_server redirects sys.stdout and pulls heavy deps, neither of
    which belongs in a per-turn hook). Returns rows written."""
    import db as _db

    topic = hc.topic_for_cwd(hc.cwd_from_payload(payload), hc.auto_capture_topics())
    if not topic:
        return 0
    session_id = payload.get("session_id", "")
    path = payload.get("transcript_path", "")
    if not path or not os.path.exists(path):
        return 0

    messages = list(iter_recall_messages(path))
    state = hc.read_extract_state(session_id)
    start = int(state.get("recall_count", 0) or 0)
    new = messages[start:]
    if not new:
        return 0

    from datetime import datetime, timezone
    written = 0
    try:
        conn = _db.get_connection(_db.resolve_db_path(f"topic_{topic}.db"))
        try:
            for role, text in new:
                conn.execute(
                    "INSERT INTO memory_recall (session_id, role, content, tool_calls, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, text, None, datetime.now(timezone.utc).isoformat()))
                written += 1
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        hc.log(f"stop {session_id}: capture into topic_{topic} failed: {e}")
        return written
    hc.write_extract_state(session_id, {**state, "recall_count": start + written})
    return written


def _run_worker():
    """Detached worker: run the (slow) extraction. On SUCCESS, commit the target
    watermark so this window isn't re-extracted; on FAILURE, leave the previous
    watermark intact so the next Stop retries it (no silent data loss). Either
    way the in-flight lock is cleared so the session isn't wedged."""
    payload_path = os.environ.get("PSYCHE_STOP_WORKER", "")
    try:
        with open(payload_path) as f:
            payload = json.load(f)
    except Exception:
        return
    session_id = payload.get("session_id", "")
    watermark = payload.get("_watermark")
    try:
        extract_facts(payload, source="stop")
        if watermark:                       # success: advance + clear in-flight
            st = hc.read_extract_state(session_id)  # preserve other keys (e.g. the capture watermark)
            st.pop("inflight_ts", None)
            hc.write_extract_state(session_id, {**st, **watermark})
    except Exception as e:
        hc.log(f"stop {session_id}: extraction failed, will retry next turn: {e}")
        st = hc.read_extract_state(session_id)   # keep old watermark, drop lock
        st.pop("inflight_ts", None)
        hc.write_extract_state(session_id, st)
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

    # Topic auto-capture runs on every stop (no gate — cheap local inserts).
    try:
        captured = capture_interactions(payload)
        if captured:
            hc.log(f"stop {session_id}: captured {captured} messages")
    except Exception as e:
        hc.log(f"stop {session_id}: capture error: {e}")

    min_turns = _int_env("PSYCHE_STOP_MIN_TURNS", 4)
    min_minutes = _int_env("PSYCHE_STOP_MIN_MINUTES", 10)
    min_growth = _int_env("PSYCHE_STOP_MIN_GROWTH", 800)

    state = hc.read_extract_state(session_id)
    turn_count = count_turns(path)
    transcript_len = len(transcript_text(path))
    now = time.time()

    # In-flight guard: if a worker for this session is already running (recent),
    # don't spawn another. The watermark stays un-advanced, so nothing is skipped
    # while we wait — this both throttles pile-ups and replaces the old
    # advance-on-spawn behaviour that silently lost data when a worker failed.
    inflight_ts = state.get("inflight_ts")
    if inflight_ts and (now - inflight_ts) < INFLIGHT_TTL:
        return

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

    # Gate passed: spawn a detached worker carrying the TARGET watermark, and mark
    # the session in-flight. The watermark is committed by the worker only on a
    # successful extraction, so a failed/killed worker is retried next turn.
    try:
        watermark = {"last_ts": now, "last_turn_count": turn_count, "last_len": transcript_len}
        payload["_watermark"] = watermark
        fd, tmp = tempfile.mkstemp(prefix="psyche_stop_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        # Mark in-flight BEFORE spawning so the worker always sees & clears it.
        hc.write_extract_state(session_id, {**state, "inflight_ts": now})
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            env={**os.environ, "PSYCHE_STOP_WORKER": tmp},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, cwd="/tmp",
        )
        hc.log(f"stop {session_id}: gate passed, worker spawned "
               f"(turns={turn_count}, len={transcript_len})")
    except Exception as e:
        st = hc.read_extract_state(session_id)   # spawn failed: drop the lock
        st.pop("inflight_ts", None)
        hc.write_extract_state(session_id, st)
        hc.log(f"stop {session_id}: worker spawn failed: {e}")


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"stop error: {e}")
    sys.exit(0)

"""SessionStart hook: inject standing facts (stable prefix + recent tail, ~2 KB)
plus a compact open-loops line (in-flight hypotheses, active experiments)."""
import os
import sys
import _hook_common as hc


def open_loops(ledger_path=None, knowledge_db=None, cap=600):
    """Compact 'what to explore' tail: in-flight hypotheses + active experiments.
    Empty string when there is nothing open. Never raises."""
    lines = []
    try:
        import brainstorm
        rows = brainstorm.list_hypotheses(ledger_path or brainstorm._ledger_path())
        active = [r for r in rows if r.get("status") in ("researching", "testing")]
        if active:
            lines.append(f"Open hypotheses ({len(active)} in flight):")
            for r in active[:2]:
                lines.append(f"- [{r['id']}] ({r['status']}) {r['text'][:120]}")
    except Exception:
        pass
    try:
        import db as _db
        path = knowledge_db or _db.resolve_db_path("knowledge.db")
        if path and os.path.exists(path):
            conn = _db.get_connection(path)
            rows = conn.execute("SELECT title FROM experiments WHERE status='active' "
                                "ORDER BY created_at DESC LIMIT 2").fetchall()
            conn.close()
            if rows:
                lines.append("Active experiments: " + "; ".join(r[0] for r in rows))
    except Exception:
        pass
    try:
        import decisions
        due = decisions.list_due_decisions(ledger_path)
        if due:
            lines.append(f"Decisions due for scoring ({len(due)}):")
            for r in due[:2]:
                lines.append(f"- [#{r['id']}] {r['situation'][:100]} | predicted: "
                             f"{r['prediction'][:80]} (conf {r['confidence']}, "
                             f"due {r['review_by']})")
    except Exception:
        pass
    return "\n".join(lines)[:cap]


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    health = hc.read_extract_health()
    if health and not health.get("ok"):
        print("WARNING: Psyche memory extraction is FAILING (last attempt "
              f"{health.get('ts', '?')}): {health.get('error', 'unknown')}. "
              "New facts are NOT being stored. Surface this to the user and check "
              "the explicitly configured chat/extraction provider. If Claude CLI "
              "extraction was enabled and the error says not logged in, run "
              "`claude /login` in a terminal.")
    import memzero
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    stable, tail = memzero.standing_fact_rows_split(project=project)
    if not stable and not tail:
        return
    text = memzero.format_facts(stable, max_chars=1500, include_date=False)
    print("Known durable facts about this user/project (Psyche memory):")
    print(text)
    if tail:
        # Placed after the stable block so its churn can't break the cached prefix.
        print("Recent additions (newest first):")
        print(memzero.format_facts(tail, max_chars=600, include_date=True))
    loops = open_loops()
    if loops:
        print("Open loops (Psyche):")
        print(loops)
    rows = stable + tail
    hc.write_ledger(session_id, hc.read_ledger(session_id) | {r["id"] for r in rows})
    h = hc.stable_block_hash(text)
    hc.append_ledger("session_start", session_id, len(rows), len(text), block_hash=h,
                     cwd=hc.cwd_from_payload(payload))
    hc.log(f"session_start {session_id}: injected {len(rows)} facts")


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"session_start error: {e}")
    sys.exit(0)

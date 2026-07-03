"""SessionStart hook: inject standing preference/decision/lesson facts (~1.5 KB cap)."""
import sys
import _hook_common as hc


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    health = hc.read_extract_health()
    if health and not health.get("ok"):
        print("WARNING: Psyche memory extraction is FAILING (last attempt "
              f"{health.get('ts', '?')}): {health.get('error', 'unknown')}. "
              "New facts are NOT being stored. Surface this to the user; if the "
              "error says not logged in, they should run `claude /login` in a "
              "terminal (extraction runs headless on their Claude subscription).")
    import memzero
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    rows = memzero.standing_fact_rows(top=12, project=project, stable=True)
    if not rows:
        return
    text = memzero.format_facts(rows, max_chars=1500, include_date=False)
    print("Known durable facts about this user/project (Psyche memory):")
    print(text)
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

"""UserPromptSubmit hook: inject facts relevant to the prompt (~2.5 KB cap, gated).

Also captures explicit "remember: <fact>" prompts verbatim — instant storage
with no LLM required. Skips trivial prompts and facts already injected this
session. Temporal prompts ("what did we do recently?") re-rank newest-first;
short prompts borrow context from the transcript tail.
"""
import re
import sys
import _hook_common as hc

TEMPORAL_RE = re.compile(
    r"(?i)\b(recent(ly)?|yesterday|today|last (week|session|time|night)|latest|newest"
    r"|what (did|have) we)\b")
CANDIDATES_WHEN_TEMPORAL = 18   # fetch wide, then cut to top 6 by recency


def rank_for_prompt(prompt, results, top=6):
    """Temporal prompts rank candidates newest-first before the cut; everything
    else keeps relevance order."""
    if TEMPORAL_RE.search(prompt or ""):
        results = sorted(results, key=lambda r: r.get("updated_at") or "", reverse=True)
    return results[:top]


def build_query(prompt, transcript_path, max_ctx=700):
    """Short prompts borrow context from the transcript tail; failures degrade
    to the bare prompt."""
    try:
        from psyche_extract import transcript_text
        ctx = transcript_text(transcript_path)
        if ctx:
            return f"{ctx[-max_ctx:]}\n{prompt}"
    except Exception:
        pass
    return prompt


def main():
    payload = hc.read_payload()
    session_id = payload.get("session_id", "")
    prompt = (payload.get("prompt") or "").strip()

    m = re.match(r"(?is)^\s*(?:please\s+)?remember\s*[:,-]\s*(.+)$", prompt)
    if m:
        fact = " ".join(m.group(1).split())
        import memzero
        project = memzero.project_key_for(hc.cwd_from_payload(payload))
        category = "preference" if re.search(r"(?i)\b(prefer|always|never|don'?t)\b", fact) else "fact"
        result = memzero.add_memory(fact, category=category, agent_id="claude-code",
                                    run_id=session_id, project=project)
        if result["duplicate_of"] is not None:
            print(f"(Psyche memory: already stored as fact #{result['duplicate_of']}.)")
        else:
            print(f"(Psyche memory: stored fact #{result['id']} — \"{result['fact']}\". It will be recalled in future sessions across Claude Code, Codex, and Antigravity.)")
            hc.append_ledger("remember_capture", session_id, 1, len(result["fact"]))
        hc.log(f"prompt_submit {session_id}: remember-capture #{result['id']}")
        return

    if prompt.startswith("/") or prompt.startswith("#"):
        return
    import memzero
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    query = prompt
    if len(prompt) < 30:
        query = build_query(prompt, payload.get("transcript_path", ""))
        if query == prompt:
            return                      # no context to lean on; keep old behavior
    top_n = CANDIDATES_WHEN_TEMPORAL if TEMPORAL_RE.search(prompt) else 6
    results = memzero.search_memories(query, top=top_n, project=project)
    if not results and query == prompt:
        wide = build_query(prompt, payload.get("transcript_path", ""))
        if wide != prompt:
            results = memzero.search_memories(wide, top=top_n, project=project)
    results = rank_for_prompt(prompt, results, top=6)
    seen = hc.read_ledger(session_id)
    fresh = [r for r in results if r["id"] not in seen]
    if not fresh:
        return
    formatted = memzero.format_facts(fresh, max_chars=2500)
    print("Relevant facts from Psyche memory:")
    print(formatted)
    hc.write_ledger(session_id, seen | {r["id"] for r in fresh})
    hc.append_ledger("prompt_submit", session_id, len(fresh), len(formatted))
    hc.log(f"prompt_submit {session_id}: injected {len(fresh)} facts")


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"prompt_submit error: {e}")
    sys.exit(0)

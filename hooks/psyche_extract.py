"""PreCompact/SessionEnd hook: extract durable atomic facts from the transcript.

Write-only — injects nothing. Uses Psyche's chat model when configured; when
CHAT_MODEL=none, falls back to headless `claude -p` on the user's subscription
(model set by PSYCHE_EXTRACT_MODEL, default `opus` for higher-quality facts;
requires a one-time `claude /login`). No-ops silently when neither is available.
The near-duplicate guard makes PreCompact + SessionEnd double-firing safe.
"""
import json
import os
import shutil
import subprocess
import sys
import _hook_common as hc

MAX_TRANSCRIPT_CHARS = 12000
# Higher-quality extraction by default; override with PSYCHE_EXTRACT_MODEL=haiku
# for a cheaper/faster pass. opus produces noticeably cleaner atomic facts.
EXTRACT_MODEL = os.environ.get("PSYCHE_EXTRACT_MODEL", "opus")


class _ClaudeCLIChat:
    """LLM shim: embeddings delegate to Psyche's local model; completions go
    through the claude CLI in headless mode on the user's subscription."""

    def __init__(self, base_llm, cli_path):
        self._base = base_llm
        self._cli = cli_path
        self.provider = base_llm.provider
        self.chat_model = f"claude-{EXTRACT_MODEL}-cli"

    def get_embedding(self, text):
        return self._base.get_embedding(text)

    def generate_completion(self, system_instruction, prompt):
        env = dict(os.environ, PSYCHE_MEM_HOOK="1")
        result = subprocess.run(
            [self._cli, "-p", "--model", EXTRACT_MODEL, "--max-turns", "1"],
            input=f"{system_instruction}\n\n---\nTRANSCRIPT:\n{prompt}",
            capture_output=True, text=True, timeout=180, env=env, cwd="/tmp",
        )
        out = (result.stdout or "").strip()
        if result.returncode != 0 or not out or "login" in out.lower()[:60]:
            raise RuntimeError(f"claude CLI extraction failed: {(result.stderr or out)[:200]}")
        return out


def _resolve_llm():
    from llm_client import LLMClient
    llm = LLMClient()
    if getattr(llm, "chat_model", "none") != "none":
        return llm
    cli_path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    if os.path.exists(cli_path):
        return _ClaudeCLIChat(llm, cli_path)
    return llm


def count_turns(path: str) -> int:
    """Counts assistant turns in a JSONL transcript. Missing file / malformed
    lines are skipped; returns 0 on any failure. Used by the Stop-hook gate."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "assistant":
                    n += 1
    except Exception:
        return 0
    return n


def transcript_text(path: str) -> str:
    parts = []
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
                parts.append(f"{role}: {text}")
    return "\n\n".join(parts)[-MAX_TRANSCRIPT_CHARS:]


def extract_facts(payload, *, source="extract"):
    """Extract durable facts from a transcript payload and store them.

    Shared by the PreCompact/SessionEnd flush (this module's main()) and the
    Stop-hook incremental worker (psyche_stop.py). The near-duplicate guard in
    memzero.extract_and_store makes overlapping extraction windows safe.
    Returns the list of stored facts ([] when there was nothing to extract).
    Raises if the underlying LLM call fails — callers that advance a watermark
    must treat that as 'retry later', not 'done'."""
    session_id = payload.get("session_id", "")
    path = payload.get("transcript_path", "")
    if not path or not os.path.exists(path):
        return []
    import memzero
    text = transcript_text(path)
    llm = _resolve_llm()
    project = memzero.project_key_for(hc.cwd_from_payload(payload))
    stored = memzero.extract_and_store(text, agent_id="claude-code", run_id=session_id,
                                       project=project, llm=llm)
    hc.log(f"extract {session_id} ({source}) via {getattr(llm, 'chat_model', '?')}: stored {len(stored)} facts")
    return stored


def main():
    payload = hc.read_payload()
    extract_facts(payload, source=payload.get("hook_event_name", "extract"))


if __name__ == "__main__":
    hc.recursion_guard()
    try:
        main()
    except Exception as e:
        hc.log(f"extract error: {e}")
    sys.exit(0)

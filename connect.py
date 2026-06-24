"""
connect.py — one-command onboarding: wires Psyche's MCP server + memory
protocol into a supported client's config files.
"""

import json
import os
import shutil
import sys

# Repo root resolved at import time so tests can monkeypatch HOME safely
_REPO = os.path.dirname(os.path.abspath(__file__))
# Prefer the project venv's interpreter; fall back to the running interpreter
# (pip/global installs have no .venv, and Windows uses Scripts/python.exe).
_VENV_BIN = (
    os.path.join(_REPO, ".venv", "Scripts", "python.exe")
    if os.name == "nt"
    else os.path.join(_REPO, ".venv", "bin", "python")
)
_VENV_PYTHON = _VENV_BIN if os.path.exists(_VENV_BIN) else sys.executable
_CLI = os.path.join(_REPO, "cli.py")

_MCP_ENTRY = {
    "command": _VENV_PYTHON,
    "args": [_CLI, "start-mcp"],
}

_DEFAULT_PROTOCOL = (
    "## Psyche memory protocol\n"
    "Before starting a task, call `search_memories` for relevant durable facts.\n"
    "When you learn a durable preference, decision, or lesson, call `add_memory` "
    "with one self-contained sentence. Use `search_knowledge` to consult the user's "
    "indexed books and notes, and `retrieve_graph` for concept relationships."
)

_PROTOCOL_BLOCK = None  # loaded lazily


def _get_protocol_block() -> str:
    global _PROTOCOL_BLOCK
    if _PROTOCOL_BLOCK is None:
        proto_path = os.path.join(_REPO, "docs", "memory-protocol.md")
        try:
            with open(proto_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Extract the block after the first "---" separator
            parts = content.split("---", 1)
            _PROTOCOL_BLOCK = parts[1].strip() if len(parts) > 1 else content.strip()
        except FileNotFoundError:
            # docs/ may not ship with every install; degrade gracefully.
            _PROTOCOL_BLOCK = _DEFAULT_PROTOCOL
    return _PROTOCOL_BLOCK


def _backup_once(path: str, dry_run: bool = False) -> str | None:
    """Back up path → path.psyche-bak once. Returns action string or None."""
    bak = path + ".psyche-bak"
    if os.path.exists(path) and not os.path.exists(bak):
        if not dry_run:
            shutil.copy2(path, bak)
        return f"backed up {path} → {bak}"
    return None


def _merge_json_mcp(path: str, entry: dict, dry_run: bool = False) -> str | None:
    """Merge mcpServers.psyche into the JSON file at path.
    Creates the file (and parent dirs) if absent.
    Returns action string if a write was/would be performed, else None."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Could not parse {path}: {exc}. Fix or remove that file and retry "
                f"(a '{path}.psyche-bak' backup may already exist)."
            )
    else:
        data = {}

    mcp_servers = data.get("mcpServers", {})
    if mcp_servers.get("psyche") == entry:
        return None  # already present and identical — nothing to do

    mcp_servers["psyche"] = entry
    data["mcpServers"] = mcp_servers

    if not dry_run:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return f"merged mcpServers.psyche into {path}"


def _append_marked_block(
    path: str,
    start_marker: str,
    end_marker: str,
    block: str,
    dry_run: bool = False,
) -> str | None:
    """Append block between start_marker / end_marker to path.
    Skips if start_marker already present. Creates file if absent.
    Returns action string or None."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if start_marker in content:
            return None  # already present

    section = f"\n{start_marker}\n{block}\n{end_marker}\n"

    if not dry_run:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(section)
    return f"appended psyche protocol block to {path}"


def connect(client: str, dry_run: bool = False) -> list[str]:
    """Wires Psyche into the given client. client in {'claude-code','codex','gemini','antigravity'}
    ('antigravity' is an alias for 'gemini'). Returns a list of human-readable
    actions taken (or would-be-taken when dry_run). Idempotent."""

    if client == "antigravity":
        client = "gemini"

    actions: list[str] = []

    def _add(result):
        if result is not None:
            actions.append(result)

    if client == "claude-code":
        settings_path = os.path.expanduser("~/.claude/settings.json")
        _add(_backup_once(settings_path, dry_run=dry_run))
        _add(_merge_json_mcp(settings_path, _MCP_ENTRY, dry_run=dry_run))
        actions.append(
            "note: Claude Code hooks (pre-tool/post-tool) must be enabled separately via ~/.claude/settings.json hooks section"
        )

    elif client == "codex":
        config_path = os.path.expanduser("~/.codex/config.toml")
        agents_path = os.path.expanduser("~/.codex/AGENTS.md")

        # --- config.toml ---
        _add(_backup_once(config_path, dry_run=dry_run))

        toml_block = (
            "\n# >>> psyche (managed) >>>\n"
            "[mcp_servers.psyche]\n"
            f'command = "{_VENV_PYTHON}"\n'
            f'args = ["{_CLI}", "start-mcp"]\n'
            "# <<< psyche (managed) <<<"
        )

        marker = "# >>> psyche (managed) >>>"
        existing_content = ""
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                existing_content = f.read()

        # Skip if our marker OR a bare [mcp_servers.psyche] table already exists
        # (the npm postinstaller writes the bare table) — appending a second one
        # corrupts the TOML.
        if marker not in existing_content and "[mcp_servers.psyche]" not in existing_content:
            if not dry_run:
                os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
                with open(config_path, "a", encoding="utf-8") as f:
                    f.write(toml_block + "\n")
            actions.append(f"appended psyche MCP block to {config_path}")

        # --- AGENTS.md ---
        _add(
            _append_marked_block(
                agents_path,
                "<!-- psyche:start -->",
                "<!-- psyche:end -->",
                _get_protocol_block(),
                dry_run=dry_run,
            )
        )

    elif client == "gemini":
        mcp_config_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
        gemini_md_path = os.path.expanduser("~/.gemini/GEMINI.md")

        _add(_backup_once(mcp_config_path, dry_run=dry_run))
        _add(_merge_json_mcp(mcp_config_path, _MCP_ENTRY, dry_run=dry_run))
        _add(
            _append_marked_block(
                gemini_md_path,
                "<!-- psyche:start -->",
                "<!-- psyche:end -->",
                _get_protocol_block(),
                dry_run=dry_run,
            )
        )

    else:
        raise ValueError(
            f"Unknown client {client!r}. Choices: claude-code, codex, gemini, antigravity"
        )

    return actions

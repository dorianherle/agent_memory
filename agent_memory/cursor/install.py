"""Install Cursor MCP + hooks into a project so teammates get memory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SERVER_NAME = "agent-memory"
RULE_NAME = "agent-memory.mdc"

HOOK_EVENTS = (
    "sessionStart",
    "beforeSubmitPrompt",
    "afterAgentResponse",
)

RULE_BODY = """---
description: Long-term agent memory (MCP + automatic durability)
alwaysApply: true
---

# Agent Memory

Durability is automatic (Cursor hooks save user prompts and finished
answers). You do **not** need to save every step.

Use **agent-memory** MCP tools for judgment:
- `memory_summarize` — fold finished work so Recent stays short
- `memory_find` / `memory_history` / `memory_graph` — recall detail
- `memory_graph_data` — structured `{nodes, edges, kinds, branches}` for a Cursor canvas
- `memory_branch` — leave a dead end; restores files to a snapshot
- `memory_inspect` / `memory_inspect_done` — look at an old snapshot
- `memory_state` — refresh MEMORY STATE

Read `@.cursor/MEMORY_STATE.md` for the compact current state.
Only use entry ids you can see there or in tool results.
"""


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _hook_command(python: str) -> str:
    return f"{python} -m agent_memory.cursor.hook"


def install_cursor(
    project: str | Path = ".",
    *,
    python: str | None = None,
) -> dict[str, Path]:
    """Merge agent-memory into the project's `.cursor/` config.

    Safe to re-run: merges MCP servers and hook lists without wiping
    unrelated entries.
    """
    root = Path(project).resolve()
    cursor = root / ".cursor"
    cursor.mkdir(parents=True, exist_ok=True)
    py = python or sys.executable
    command = _hook_command(py)
    written: dict[str, Path] = {}

    mcp_path = cursor / "mcp.json"
    mcp = _read_json(mcp_path, {"mcpServers": {}})
    servers = mcp.setdefault("mcpServers", {})
    # Pass the project root via --root in args (absolute). Prefer args over
    # env: some Cursor builds leave ${workspaceFolder} unexpanded in env,
    # which made memory look empty. Teammates should re-run
    # `agent-memory cursor install` so the path matches their machine.
    servers[SERVER_NAME] = {
        "command": py,
        "args": [
            "-m",
            "agent_memory.cursor.mcp",
            "--root",
            str(root),
        ],
    }
    _write_json(mcp_path, mcp)
    written["mcp"] = mcp_path

    hooks_path = cursor / "hooks.json"
    hooks_doc = _read_json(hooks_path, {"version": 1, "hooks": {}})
    hooks_doc["version"] = 1
    hooks = hooks_doc.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = list(hooks.get(event) or [])
        # Drop prior agent-memory hook lines, then append ours.
        entries = [
            entry
            for entry in entries
            if "agent_memory.cursor.hook" not in str(entry.get("command", ""))
        ]
        entries.append({"command": command})
        hooks[event] = entries
    _write_json(hooks_path, hooks_doc)
    written["hooks"] = hooks_path

    rules_dir = cursor / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / RULE_NAME
    rule_path.write_text(RULE_BODY)
    written["rule"] = rule_path

    from agent_memory.core import Memory

    state_path = Memory(root).write_state_file()
    written["state"] = state_path
    return written

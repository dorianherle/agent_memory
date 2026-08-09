"""Sidecar: full memory-related prompt packet per user turn (for live HTML).

Records what *agent-memory* injects around the goal — not Cursor's base
system prompt, and not MCP tool JSON schemas (those are tool defs, not text).

Stored outside the git memory commits. Keyed by entry id; pruned to ids
still present in the graph.
"""

from __future__ import annotations

import json
from pathlib import Path

TURN_PROMPTS_NAME = "turn_prompts.json"

PACKET_NOTE = (
    "How the model learns MCP exists:\n"
    "1) Text in the system/rules prompt — alwaysApply agent-memory.mdc + "
    "sessionStart instructions (included above).\n"
    "2) Tool schemas — Cursor loads .cursor/mcp.json, lists tools from the "
    "MCP server, and attaches name/description/args as callables on the "
    "request (not always visible as plain prose).\n"
    "Section MCP TOOL CATALOG below is the docstring text from (2), "
    "recorded here so you can inspect it."
)


def mcp_tools_catalog() -> str:
    """Plain-text catalog of agent-memory MCP tools (name + docstring)."""
    try:
        from agent_memory.cursor.mcp import _build_server

        server = _build_server()
        tools = getattr(getattr(server, "_tool_manager", None), "_tools", None)
        if not isinstance(tools, dict) or not tools:
            return "(MCP tool manager empty)"
        lines = ["MCP server: agent-memory", ""]
        for name in sorted(tools):
            tool = tools[name]
            doc = (
                getattr(tool, "description", None)
                or getattr(getattr(tool, "fn", None), "__doc__", None)
                or ""
            )
            doc = str(doc).strip() or "(no description)"
            lines.append(f"### {name}")
            lines.append(doc)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    except Exception as error:
        return f"(could not load MCP catalog: {error})\n"


def turn_prompts_path(live_dir: Path) -> Path:
    return Path(live_dir) / TURN_PROMPTS_NAME


def build_packet(
    *,
    instructions: str,
    rule: str,
    mcp_tools: str,
    memory_state: str,
    goal: str,
) -> str:
    """Assemble the memory-related text Cursor injects around the goal."""
    parts = [
        "=== MEMORY INSTRUCTIONS (sessionStart / hooks → prompt text) ===",
        instructions.strip(),
        "",
        "=== ALWAYS-APPLY RULE (.cursor/rules/agent-memory.mdc → prompt text) ===",
        rule.strip(),
        "",
        "=== MCP TOOL CATALOG (docstrings Cursor also sends as tool schemas) ===",
        mcp_tools.strip(),
        "",
        "=== INJECTED MEMORY STATE (beforeSubmitPrompt + state rule) ===",
        memory_state.strip(),
        "",
        "=== USER GOAL ===",
        goal.strip(),
        "",
        PACKET_NOTE,
        "",
    ]
    return "\n".join(parts)


def record_turn_prompt(
    live_dir: Path,
    entry_id: str,
    *,
    goal: str,
    memory_state: str,
    instructions: str = "",
    rule: str = "",
    mcp_tools: str = "",
    # Back-compat with older callers that only passed injected=
    injected: str | None = None,
) -> Path:
    """Remember the memory prompt packet for this user entry."""
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    path = turn_prompts_path(live_dir)
    data: dict = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except json.JSONDecodeError:
            data = {}

    state_text = (injected if injected is not None else memory_state) or ""
    tools_text = mcp_tools or mcp_tools_catalog()
    packet = build_packet(
        instructions=instructions,
        rule=rule,
        mcp_tools=tools_text,
        memory_state=state_text,
        goal=goal,
    )
    data[entry_id] = {
        "goal": goal,
        "instructions": instructions,
        "rule": rule,
        "mcp_tools": tools_text,
        "memory_state": state_text,
        # Keep "injected" alias for older HTML shells / tests.
        "injected": state_text,
        "packet": packet,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_turn_prompts(live_dir: Path) -> dict[str, dict]:
    path = turn_prompts_path(live_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def attach_turn_prompts(nodes: list[dict], live_dir: Path) -> None:
    """Merge sidecar packets onto matching nodes; prune stale ids."""
    turns = load_turn_prompts(live_dir)
    if not turns:
        return
    alive = {node.get("id") for node in nodes}
    changed = False
    for node in nodes:
        packet = turns.get(node.get("id") or "")
        if not packet:
            continue
        node["goal"] = packet.get("goal") or node.get("body") or ""
        node["instructions"] = packet.get("instructions") or ""
        node["rule"] = packet.get("rule") or ""
        node["mcp_tools"] = packet.get("mcp_tools") or ""
        node["memory_state"] = (
            packet.get("memory_state") or packet.get("injected") or ""
        )
        node["injected"] = node["memory_state"]
        node["packet"] = packet.get("packet") or ""
    stale = [key for key in turns if key not in alive]
    if stale:
        for key in stale:
            turns.pop(key, None)
        changed = True
    if changed:
        turn_prompts_path(live_dir).write_text(
            json.dumps(turns, indent=2) + "\n", encoding="utf-8"
        )

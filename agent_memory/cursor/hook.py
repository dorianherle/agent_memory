"""Cursor hook entrypoint: goal setup + live HTML refresh + agent identity.

Reads JSON from stdin (Cursor hook protocol). Event comes from
`hook_event_name` in the payload.

- sessionStart        → instructions + agent id; refresh live HTML
- beforeSubmitPrompt  → create the goal once; refresh live HTML
- afterAgentResponse  → refresh live HTML (conversation is not durable memory)
- subagentStart       → register subagent session name
- subagentStop        → save subagent summary under that agent name

Memory is retrieved on demand via MCP tools (`memory_find`, `memory_history`)
rather than rewriting a dynamic Cursor state rule on every prompt.
"""

from __future__ import annotations

import json
import sys

from agent_memory.cursor.common import get_memory
from agent_memory.identity import (
    DEFAULT_AGENT,
    ENV_AGENT,
    USER_AGENT,
    lookup_session_agent,
    register_session,
    resolve_agent,
    subagent_label,
)

CURSOR_INSTRUCTIONS = """
Project memory via agent-memory MCP. The first request establishes the goal;
ordinary conversation is not copied into memory.
Match the current request. Greetings, thanks, and casual small talk need only a
brief conversational reply. A bare "what's up?" is not a project-status request.
Do not load skills, search memory, inspect files, narrate checks, recap projects,
or propose tasks unless the user asks for work or context. An old goal is not a
new request to resume work.

Query memory_find or memory_history on demand when investigating prior context,
decisions, or dead ends. Call memory_save for dead ends, non-obvious decisions,
user constraints, and checkpoints before risky edits. Distill the useful fact;
do not copy a raw prompt or answer. On a completed milestone, call
memory_summarize with a verified outcome and key evidence to group its commits.
Do not invent ids.
Pass agent=<your name> on memory_save / memory_summarize.
""".strip()


def _read_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}
    return data if isinstance(data, dict) else {}


def _emit(payload: dict | None = None) -> None:
    sys.stdout.write(json.dumps(payload or {}))


def _session_id(data: dict) -> str | None:
    for key in ("session_id", "conversation_id", "subagent_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _agent_for_event(memory, data: dict) -> str:
    """Resolve which agent is acting for this hook payload."""
    sid = _session_id(data)
    mapped = lookup_session_agent(memory._git_dir, sid)
    if mapped:
        return mapped
    return resolve_agent(None)


def _refresh_dashboard(memory) -> None:
    """Best-effort live HTML rewrite after durable events."""
    try:
        memory.write_live_html()
    except Exception:
        pass


def _memory_rule_text(memory) -> str:
    """Static alwaysApply tool-guide rule."""
    path = memory._path / ".cursor" / "rules" / "agent-memory.mdc"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _payload_root(data: dict) -> str | None:
    roots = data.get("workspace_roots")
    if isinstance(roots, list):
        for candidate in roots:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    for key in ("workspace_root", "cwd"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def handle(event: str, data: dict) -> dict:
    memory = get_memory(fallback_root=_payload_root(data))
    try:
        return _handle_with_memory(event, data, memory)
    finally:
        memory.close()


def _agent_instructions(agent: str) -> str:
    return (
        f"{CURSOR_INSTRUCTIONS}\n\n"
        f"Your memory agent name: @{agent}\n"
        f"Pass agent={agent!r} on memory_save / memory_summarize when possible."
    )


def _handle_with_memory(event: str, data: dict, memory) -> dict:
    if event == "sessionStart":
        sid = _session_id(data) or data.get("session_id")
        existing = lookup_session_agent(memory._git_dir, sid)
        agent = existing or DEFAULT_AGENT
        if sid and not existing:
            register_session(
                memory._git_dir,
                sid,
                agent=agent,
                agent_type=agent,
            )
        html_note = ""
        try:
            html_path = memory.write_live_html()
            html_note = (
                f"\nLive HTML (auto-refreshes in browser): {html_path}"
            )
        except Exception:
            pass
        state_block = ""
        try:
            if memory._git_dir.exists() and memory.state():
                query = next(
                    (
                        str(data[k]).strip()
                        for k in ("prompt", "query", "message", "text")
                        if isinstance(data.get(k), str) and str(data[k]).strip()
                    ),
                    None,
                )
                packet = memory.render_packet(query)
                if packet.strip():
                    state_block = f"\n\n# MEMORY STATE\n{packet}"
        except Exception:
            pass
        context = f"{_agent_instructions(agent)}{html_note}{state_block}"
        return {
            "additional_context": context,
            "env": {ENV_AGENT: agent},
        }

    if event == "beforeSubmitPrompt":
        from agent_memory.core import _SMALL_TALK_RE

        raw_prompt = data.get("prompt")
        cleaned = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
        entry_id = None
        if cleaned and not _SMALL_TALK_RE.match(cleaned):
            entry_id = memory.ensure_goal(cleaned, agent=USER_AGENT)
        if entry_id:
            try:
                from agent_memory.live_html import default_live_dir
                from agent_memory.turn_prompts import (
                    mcp_tools_catalog,
                    record_turn_prompt,
                )

                agent = _agent_for_event(memory, data)
                record_turn_prompt(
                    default_live_dir(memory),
                    entry_id,
                    goal=cleaned,
                    memory_state=memory.render_packet(cleaned),
                    instructions=_agent_instructions(agent),
                    rule=_memory_rule_text(memory),
                    mcp_tools=mcp_tools_catalog(),
                )
            except (Exception, SystemExit):
                pass
        if cleaned and not _SMALL_TALK_RE.match(cleaned):
            _refresh_dashboard(memory)
        return {
            "continue": True,
        }

    if event == "afterAgentResponse":
        # Finished answers belong to the conversation. Agents explicitly save
        # only durable knowledge through memory_save / memory_summarize.
        if memory._git_dir.exists() and memory.state():
            _refresh_dashboard(memory)
        return {}

    if event == "subagentStart":
        sub_id = str(data.get("subagent_id") or "").strip()
        sub_type = str(data.get("subagent_type") or "subagent").strip()
        task = str(data.get("task") or data.get("description") or "").strip()
        parent = str(data.get("parent_conversation_id") or "").strip() or None
        if not sub_id:
            return {"permission": "allow"}
        name = subagent_label(sub_type, sub_id)
        register_session(
            memory._git_dir,
            sub_id,
            agent=name,
            agent_type=sub_type,
            task=task,
            parent_id=parent,
        )
        # Parent notes the spawn so the planner timeline shows handoff.
        parent_agent = (
            lookup_session_agent(memory._git_dir, parent) if parent else None
        ) or DEFAULT_AGENT
        title = task.split("\n", 1)[0][:120] or sub_type
        memory.save(
            f"Spawned @{name}: {title}",
            {
                "subagent": True,
                "subagent_id": sub_id,
                "subagent_type": sub_type,
                "event": "start",
                "auto": True,
            },
            agent=parent_agent,
        )
        _refresh_dashboard(memory)
        return {"permission": "allow"}

    if event == "subagentStop":
        sub_type = str(data.get("subagent_type") or "subagent").strip()
        # Prefer registered id from transcript path / any id field.
        sub_id = str(
            data.get("subagent_id")
            or data.get("agent_id")
            or ""
        ).strip()
        status = str(data.get("status") or "completed").strip()
        description = str(data.get("description") or "").strip()
        summary = str(data.get("summary") or "").strip()
        task = str(data.get("task") or "").strip()
        name = (
            lookup_session_agent(memory._git_dir, sub_id)
            if sub_id
            else None
        ) or subagent_label(sub_type, sub_id or None)
        body = summary or description or task or f"{sub_type} {status}"
        body = body.strip()[:2000]
        memory.save(
            f"[{status}] {body}",
            {
                "subagent": True,
                "subagent_id": sub_id or None,
                "subagent_type": sub_type,
                "event": "stop",
                "status": status,
                "auto": True,
            },
            agent=name,
        )
        _refresh_dashboard(memory)
        return {}

    return {}


def main() -> None:
    from agent_memory.cursor.common import _strip_root_argv, remember_root

    remember_root()
    _strip_root_argv()
    data = _read_payload()
    event = str(data.get("hook_event_name") or "").strip()
    if not event and len(sys.argv) > 1:
        event = sys.argv[1]
    try:
        _emit(handle(event, data))
    except (Exception, SystemExit) as error:
        # Fail open: never block the agent loop on memory errors.
        sys.stderr.write(f"agent-memory hook error: {error}\n")
        _emit({})


if __name__ == "__main__":
    main()

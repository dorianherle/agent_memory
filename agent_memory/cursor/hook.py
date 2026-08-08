"""Cursor hook entrypoint: durability + state injection + agent identity.

Reads JSON from stdin (Cursor hook protocol). Event comes from
`hook_event_name` in the payload.

- sessionStart        → inject MEMORY STATE, set AGENT_MEMORY_AGENT, canvas
- beforeSubmitPrompt  → save_user(prompt) as @user
- afterAgentResponse  → auto_save(text) tagged with current agent
- subagentStart       → register subagent session name
- subagentStop        → save subagent summary under that agent name
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
You have long-term project memory via the agent-memory MCP tools.
Durability is automatic — do not try to "save" every step.
Use tools for judgment only:
- memory_summarize when a chunk of work is finished (keeps Recent short)
- memory_find / memory_history / memory_graph to recall detail
- memory_branch to leave a dead end; memory_inspect to look at an old snapshot
- memory_write_canvas to refresh the live dashboard
Always call memory_inspect_done before answering if you inspected.
Prefer facts from MEMORY STATE or tool results; do not invent entry ids.
Every memory entry is tagged with an agent name (@main, @explore·…, @user).
When calling memory tools, pass agent=<your name> if the tool asks; your
session agent name is also in AGENT_MEMORY_AGENT.
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
    """Best-effort live canvas rewrite after durable events."""
    try:
        memory.write_state_file()
        memory.write_canvas(name="agent-memory-live")
    except Exception:
        # Never block the agent loop on dashboard failures.
        try:
            memory.write_state_file()
        except Exception:
            pass


def handle(event: str, data: dict) -> dict:
    memory = get_memory()

    if event == "sessionStart":
        sid = _session_id(data) or data.get("session_id")
        agent = lookup_session_agent(memory._git_dir, sid) or DEFAULT_AGENT
        if sid and not lookup_session_agent(memory._git_dir, sid):
            register_session(
                memory._git_dir,
                sid,
                agent=agent,
                agent_type=agent,
            )
        state_path = memory.write_state_file()
        try:
            canvas_path = memory.write_canvas(name="agent-memory-live")
            canvas_note = f"\nLive dashboard: {canvas_path}"
        except Exception:
            canvas_note = ""
        context = (
            f"{CURSOR_INSTRUCTIONS}\n\n"
            f"Your memory agent name: @{agent}\n"
            f"Pass agent={agent!r} on memory_summarize when possible.\n\n"
            f"MEMORY STATE\n{memory.render_state()}\n\n"
            f"Also available at @{state_path.relative_to(memory._path)}"
            f"{canvas_note}"
        )
        return {
            "additional_context": context,
            "env": {ENV_AGENT: agent},
        }

    if event == "beforeSubmitPrompt":
        prompt = data.get("prompt") or ""
        if isinstance(prompt, str) and prompt.strip():
            memory.save_user(prompt.strip(), agent=USER_AGENT)
            _refresh_dashboard(memory)
        return {}

    if event == "afterAgentResponse":
        text = data.get("text") or ""
        if isinstance(text, str) and text.strip():
            who = _agent_for_event(memory, data)
            memory.auto_save(text.strip(), agent=who)
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
    data = _read_payload()
    event = str(data.get("hook_event_name") or "").strip()
    if not event and len(sys.argv) > 1:
        event = sys.argv[1]
    try:
        _emit(handle(event, data))
    except Exception as error:
        # Fail open: never block the agent loop on memory errors.
        sys.stderr.write(f"agent-memory hook error: {error}\n")
        _emit({})


if __name__ == "__main__":
    main()

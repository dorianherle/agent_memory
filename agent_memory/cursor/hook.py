"""Cursor hook entrypoint: durability + state injection + agent identity.

Reads JSON from stdin (Cursor hook protocol). Event comes from
`hook_event_name` in the payload.

- sessionStart        → instructions + agent id; refresh state rule/canvas
- beforeSubmitPrompt  → save_user(prompt); refresh alwaysApply MEMORY STATE
- afterAgentResponse  → auto_save(text) tagged with current agent
- subagentStart       → register subagent session name
- subagentStop        → save subagent summary under that agent name

MEMORY STATE lands in the model prompt via the gitignored alwaysApply rule
``.cursor/rules/agent-memory-state.mdc`` (rewritten every durable write).
beforeSubmitPrompt only points at that rule — it does not paste the
packet again. Live UI is HTML only.
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
Read the skill `.cursor/skills/agent-memory/SKILL.md` before multi-step
work: it covers when to retrieve, how to write entries that stay useful,
and snapshot safety for branch/inspect.
Hooks auto-save user prompts and finished answers at turn boundaries.

For any stretch of work that might outlast this turn — investigations,
multi-step edits, experiments — call memory_save at real milestones
(findings, decisions, working checkpoints). Prefer a few solid saves
over silence; if the session dies, only checkpoints survive.
Skip pure noise (e.g. "opened file X") but do not wait until the final
answer if useful progress already exists.

Use tools for judgment and checkpoints:
- memory_save — durable mid-turn checkpoint (pass agent)
- memory_summarize when a chunk of work is finished (keeps Recent short)
- memory_delete — hide specific noise entries from Recent (pass ids)
- memory_find to recall detail (query= uses Gemini when configured)
- memory_branch to restore a saved snapshot (label summarizes current work); memory_inspect to look at an old snapshot
Always call memory_inspect_done before answering if you inspected.
Prefer facts from MEMORY STATE (always-applied rule) or tool results; do not
invent entry ids.
Every memory entry is tagged with an agent name (@agent, @explore·…, @user).
When calling memory tools, pass agent=<your name> if the tool asks; your
session agent name is also in AGENT_MEMORY_AGENT.
A self-updating HTML live view is also kept at `.agent_memory/live/index.html`
(open in a browser; it polls for updates).
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
        memory.write_state_file()
        memory.write_live_html()
    except Exception:
        try:
            memory.write_state_file()
        except Exception:
            pass


def _memory_rule_text(memory) -> str:
    """Static alwaysApply tool-guide rule (not the live state rule)."""
    path = memory._path / ".cursor" / "rules" / "agent-memory.mdc"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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
        rule_path = memory._path / ".cursor" / "rules" / "agent-memory-state.mdc"
        html_note = ""
        try:
            html_path = memory.write_live_html()
            html_note = (
                f"\nLive HTML (auto-refreshes in browser): {html_path}"
            )
        except Exception:
            pass
        context = (
            f"{CURSOR_INSTRUCTIONS}\n\n"
            f"Your memory agent name: @{agent}\n"
            f"Pass agent={agent!r} on memory_save / memory_summarize when possible.\n\n"
            f"MEMORY STATE is injected every turn via the alwaysApply rule "
            f"`{rule_path.relative_to(memory._path)}` "
            f"(also @{state_path.relative_to(memory._path)})."
            f"{html_note}"
        )
        return {
            "additional_context": context,
            "env": {ENV_AGENT: agent},
        }

    if event == "beforeSubmitPrompt":
        prompt = data.get("prompt") or ""
        entry_id = None
        if isinstance(prompt, str) and prompt.strip():
            entry_id = memory.save_user(prompt.strip(), agent=USER_AGENT)
            _refresh_dashboard(memory)
        else:
            memory.write_state_file()
        # One delivery channel: the alwaysApply rule. The hook only
        # points at it so memory state is delivered once.
        rule_rel = ".cursor/rules/agent-memory-state.mdc"
        state = memory.render_state()
        pointer = (
            f"MEMORY STATE is in the alwaysApply rule `{rule_rel}`."
        )
        if entry_id and isinstance(prompt, str) and prompt.strip():
            try:
                from agent_memory.live_html import default_live_dir
                from agent_memory.turn_prompts import (
                    mcp_tools_catalog,
                    record_turn_prompt,
                )

                agent = _agent_for_event(memory, data)
                instructions = (
                    f"{CURSOR_INSTRUCTIONS}\n\n"
                    f"Your memory agent name: @{agent}\n"
                    f"Pass agent={agent!r} on memory_save / "
                    f"memory_summarize when possible."
                )
                record_turn_prompt(
                    default_live_dir(memory),
                    entry_id,
                    goal=prompt.strip(),
                    memory_state=state,
                    instructions=instructions,
                    rule=_memory_rule_text(memory),
                    mcp_tools=mcp_tools_catalog(),
                )
                memory.write_live_html()
            except Exception:
                pass
        return {
            "continue": True,
            "additional_context": pointer,
        }

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

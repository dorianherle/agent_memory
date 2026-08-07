"""Cursor hook entrypoint: durability + state injection.

Reads JSON from stdin (Cursor hook protocol). Event comes from
`hook_event_name` in the payload.

- sessionStart        → inject MEMORY STATE + write MEMORY_STATE.md
- beforeSubmitPrompt  → save_user(prompt)
- afterAgentResponse  → auto_save(text)
"""

from __future__ import annotations

import json
import sys

from agent_memory.cursor.common import get_memory

CURSOR_INSTRUCTIONS = """
You have long-term project memory via the agent-memory MCP tools.
Durability is automatic — do not try to "save" every step.
Use tools for judgment only:
- memory_summarize when a chunk of work is finished (keeps Recent short)
- memory_find / memory_history / memory_graph to recall detail
- memory_branch to leave a dead end; memory_inspect to look at an old snapshot
Always call memory_inspect_done before answering if you inspected.
Prefer facts from MEMORY STATE or tool results; do not invent entry ids.
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


def handle(event: str, data: dict) -> dict:
    memory = get_memory()

    if event == "sessionStart":
        state_path = memory.write_state_file()
        context = (
            f"{CURSOR_INSTRUCTIONS}\n\n"
            f"MEMORY STATE\n{memory.render_state()}\n\n"
            f"Also available at @{state_path.relative_to(memory._path)}"
        )
        return {"additional_context": context}

    if event == "beforeSubmitPrompt":
        prompt = data.get("prompt") or ""
        if isinstance(prompt, str) and prompt.strip():
            memory.save_user(prompt.strip())
            memory.write_state_file()
        return {}

    if event == "afterAgentResponse":
        text = data.get("text") or ""
        if isinstance(text, str) and text.strip():
            memory.auto_save(text.strip())
            memory.write_state_file()
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

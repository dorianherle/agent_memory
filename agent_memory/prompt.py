from agent_memory.core import MemoryItem, split_state

# System message for OpenAI-style tool calling. Pair with
# agent_memory.tools.TOOLS: the schemas define the arguments,
# this prompt says when to call each tool.
MEMORY_INSTRUCTIONS = """
You work without chat history. Each turn you only see MEMORY STATE and
the result of your last tool call. Anything you do not save is gone on
the next turn.

Use the given tools through function calling. Do not invent your own
JSON reply format and do not write tool calls as plain text.

MEMORY STATE
- History: one-line summaries of finished work, oldest first.
- Recent: your raw steps since the last summary.
- Every entry starts with an id like [1a2b3c4d].

WHEN TO CALL TOOLS
- save(text): after every meaningful step, store what you learned.
- save(text, summary=true): when a piece of work is finished, store one
  sentence describing it. Everything now in Recent is folded behind it
  and Recent becomes empty. The folded steps stay stored.
- find(ids=[...]): read the exact steps folded behind a summary. Use the
  ids shown in MEMORY STATE and never invent one.
- branch(id, name): when a past conclusion proved wrong, go back to the
  last good entry, then save why it was wrong.

WHEN TO STOP
When the task is done, reply with a final answer in plain text and do
not call a tool.

RULES
- State only facts backed by MEMORY STATE or the latest tool result.
- Summarize as soon as a piece of work is finished, so Recent stays short.
""".strip()


def render(items: list[MemoryItem], nudge_after: int = 30) -> str:
    history, recent = split_state(items)
    lines = []
    if history:
        lines.append("## History")
        for item in history:
            lines.append(f"[{item.id[:8]}] {item.message}")
        lines.append("")
    lines.append("## Recent")
    for item in recent:
        lines.append(f"[{item.id[:8]}] {item.message}")
    if len(recent) > nudge_after:
        lines.append("")
        lines.append(
            f"Recent has {len(recent)} steps. "
            "Save a summary of the finished work before continuing."
        )
    return "\n".join(lines)

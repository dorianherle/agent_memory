from agent_memory.core import MemoryItem

SYSTEM_PROMPT = """
The state above lists your whole history: summaries of finished work
under History, then your raw recent steps under Recent. Every line
starts with an id.

Rules:
- When you finish a subtask, save a one-sentence summary that covers
  the steps of that subtask. Those steps fold behind the summary.
- To see the detail behind a summary, investigate(id).
- If you realize a past decision was wrong, revise(id, reason): you
  go back to that point on a new branch and the reason is kept as a
  note. Nothing is deleted.
- Only use ids you can see in the state. Never guess an id.
- If Recent grows long, summarize whatever is finished.
""".strip()


def render(items: list[MemoryItem], nudge_after: int = 30) -> str:
    last_summary = -1
    for index, item in enumerate(items):
        if "covers" in item.payload:
            last_summary = index

    recent = items[last_summary + 1 :]
    lines = []
    if last_summary >= 0:
        lines.append("## History")
        for item in items[: last_summary + 1]:
            lines.append(f"[{item.id[:8]}] {item.message}")
        lines.append("")
    lines.append("## Recent")
    for item in recent:
        lines.append(f"[{item.id[:8]}] {item.message}")
    if len(recent) > nudge_after:
        lines.append("")
        lines.append(
            f"Recent has {len(recent)} steps. "
            "Summarize whatever is finished before continuing."
        )
    return "\n".join(lines)

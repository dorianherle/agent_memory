from agent_memory.core import MemoryItem, split_state

# System message for OpenAI-style tool calling. Pair with
# agent_memory.tools.MEMORY_TOOL: the schema says what each action does
# and which arguments it takes, this prompt says when to reach for them.
MEMORY_INSTRUCTIONS = """
You work without chat history. Each turn you only see MEMORY STATE and
the result of your last tool call. Anything you do not save is gone on
the next turn.

Use the memory tool through function calling. Do not invent your own
JSON reply format and do not write tool calls as plain text.

MEMORY STATE
- Branches: one line per branch: its id, name and the summary it was
  left at. The branch you are on is marked "you are here".
- History: one-line summaries of finished work, oldest first. A
  summary starts with "Summary:"; find its id to unfold the steps
  behind it.
- Recent: your raw steps since the last summary. Lines starting with
  "user:" are the user's own words, recorded automatically.
- Every entry starts with an id like [1a2b3c4d]. Use only ids you can
  see there and never invent one.

WHEN TO CALL memory
Pick action on the single memory tool:
- save after every meaningful step, or its result is lost.
- save with summary set to true as soon as a piece of work is finished,
  so Recent stays short.
- find when you need the exact detail behind a summary. Set
  all_branches to true to also search entries on other branches.
- history when the user asks what happened: the full timeline of this
  branch, with folded steps shown under their summary.
- graph when the user wants the whole tree: every branch and entry in
  one picture. Set html to true to also write an explorable HTML page
  for the user; give them its path.
- branch to move to any entry: back past a dead end, or forward to a
  branch shown under Branches. Pass label to fold the current branch
  behind that one-line summary and restore every working file to the
  target entry's snapshot.
- inspect to temporarily set every working file to how it was at an
  entry, to look around. inspect_done returns you to your branch.
  Always call inspect_done before answering; edits made while
  inspecting are discarded.

WHEN TO STOP
When the task is done, reply with a final answer in plain text and do
not call a tool.

RULES
- State only facts backed by MEMORY STATE or the latest tool result.
- Never save a claim about work or files you did not verify this turn.
  If you cannot reach or verify something, save that instead.
""".strip()


def _line(item: MemoryItem) -> str:
    prefix = ""
    if item.payload.get("covers"):
        prefix = "Summary: "
    elif item.payload.get("user"):
        prefix = "user: "
    return f"[{item.id[:8]}] {prefix}{item.message}"


def render_timeline(items: list[MemoryItem]) -> str:
    """The full history: raw steps indented under the summary that
    folded them, everything oldest first."""
    by_id = {item.id: item for item in items}
    covered = {c for item in items for c in item.payload.get("covers", [])}
    lines = []
    for item in items:
        if item.id in covered:
            continue
        lines.append(_line(item))
        for child_id in item.payload.get("covers", []):
            child = by_id.get(child_id)
            if child is not None:
                lines.append(f"    {_line(child)}")
    return "\n".join(lines) or "Nothing saved yet."


def render(
    items: list[MemoryItem],
    branches: list[dict] | None = None,
    inspecting: dict | None = None,
    nudge_after: int = 30,
) -> str:
    history, recent = split_state(items)
    lines = []
    if inspecting:
        lines.append(
            f"INSPECTING [{inspecting['id'][:8]}]: files are at that "
            f"snapshot. Call memory with action=inspect_done to return "
            f"to '{inspecting['branch']}'."
        )
        lines.append("")
    if branches and len(branches) > 1:
        lines.append("## Branches")
        for branch in branches:
            if branch["current"]:
                lines.append(f"* {branch['name']} (you are here)")
            else:
                item = branch["item"]
                summary = item.message.split("\n")[0][:100]
                lines.append(f"[{item.id[:8]}] {branch['name']}: {summary}")
        lines.append("")
    if history:
        lines.append("## History")
        for item in history:
            lines.append(_line(item))
        lines.append("")
    lines.append("## Recent")
    for item in recent:
        lines.append(_line(item))
    if len(recent) > nudge_after:
        lines.append("")
        lines.append(
            f"Recent has {len(recent)} steps. "
            "Save a summary of the finished work before continuing."
        )
    return "\n".join(lines)

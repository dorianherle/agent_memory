import re

from agent_memory.core import (
    MemoryItem,
    split_goals,
    all_open_questions,
    split_open_questions,
    split_state,
)

SUBJECT_MAX = 100
RECENT_CAP = 8
CHECKPOINT_NUDGE_AFTER = 8
FIND_DEFAULT_K = 8
FIND_BODY_MAX = 800
FIND_CHILD_CAP = 6
HISTORY_CAP = 16
SUBSTRING_FALLBACK = "(substring fallback — set GEMINI_API_KEY)"
_NARRATION = re.compile(
    r"^(i\s+)?("
    r"investigated|looked(\s+into|\s+at)|"
    r"opened|opening|"
    r"now i|i will|let me|going to"
    r")\b",
    re.IGNORECASE,
)

# System message for OpenAI-style tool calling. Pair with
# agent_memory.tools.MEMORY_TOOL: the schema says what each action does
# and which arguments it takes, this prompt says when to reach for them.
MEMORY_INSTRUCTIONS = """
You work without chat history. Each turn you only see MEMORY STATE and
the last tool result. Unsaved work is gone next turn. Use function calling.

State: Goal, Steering, open questions, Recent headlines. Use only ids
you can see. [stale] means that note's files changed.

memory actions: save (summary=true to fold Recent); find (ids= for
bodies); history; open_questions; delete; branch/inspect (restores
files — call inspect_done before answering).
Facts only from MEMORY STATE or the latest tool result.
""".strip()


def save_hygiene(text: str) -> str | None:
    """Warn when a judgment save will be a bad headline in state."""
    head = (text or "").strip().split("\n", 1)[0].strip()
    if len(head) > SUBJECT_MAX:
        return (
            f"Warning: first line is {len(head)} chars; "
            f"only {SUBJECT_MAX} appear in MEMORY STATE."
        )
    if _NARRATION.match(head):
        return (
            "Warning: headline reads like narration — "
            "state a finding or decision."
        )
    return None


def _is_checkpoint(item: MemoryItem) -> bool:
    payload = item.payload
    if payload.get("user") or payload.get("auto"):
        return False
    if payload.get("open_question") is True:
        return False
    return True


def _goal(items: list[MemoryItem]) -> MemoryItem | None:
    found = split_goals(items)
    return found[0] if found else None


def _steering(
    items: list[MemoryItem],
    goal: MemoryItem | None,
) -> MemoryItem | None:
    """Latest user note that is not the original goal."""
    goal_id = goal.id if goal else None
    users = [
        item
        for item in items
        if item.payload.get("user") and item.id != goal_id
    ]
    return users[-1] if users else None


def _item_view(item: MemoryItem, stale_ids: set[str] | None = None) -> dict:
    """Full-body entry for the live prompt panel (not the 100-char subject)."""
    from agent_memory.identity import DEFAULT_AGENT

    stale_ids = stale_ids or set()
    return {
        "id": item.id,
        "agent": item.payload.get("agent") or DEFAULT_AGENT,
        "text": item.message or "",
        "stale": item.id in stale_ids,
        "depends_on": list(item.payload.get("depends_on") or []),
    }


def _recent_findings(items: list[MemoryItem]) -> list[MemoryItem]:
    """Recent work notes: same filter as compact MEMORY STATE Recent."""
    _, recent = split_state(items)
    hide = {item.id for item in all_open_questions(items)}
    goal = _goal(items)
    steer = _steering(items, goal)
    pinned = {item.id for item in (goal, steer) if item}
    return [
        item
        for item in recent
        if item.id not in hide
        and item.id not in pinned
        and not item.payload.get("user")
        and not item.payload.get("auto")
    ]


def current_prompt(
    items: list[MemoryItem],
    *,
    stale_ids: set[str] | None = None,
) -> dict:
    """Structured current prompt with full bodies (for the live page)."""
    stale_ids = stale_ids or set()
    goal = _goal(items)
    steer = _steering(items, goal)
    return {
        "goal": _item_view(goal) if goal else None,
        "steering": _item_view(steer) if steer else None,
        "questions": [_item_view(item) for item in split_open_questions(items)],
        "findings": [
            _item_view(item, stale_ids)
            for item in _recent_findings(items)[-RECENT_CAP:]
        ],
    }


def _id8(item_id: str) -> str:
    return f"[{item_id[:8]}]"


def _headline(item: MemoryItem) -> str:
    head = (item.message or "").strip().split("\n", 1)[0].strip()
    if len(head) > SUBJECT_MAX:
        head = head[: SUBJECT_MAX - 1].rstrip() + "…"
    return head


def _entry_line(item: MemoryItem, stale_ids: set[str], extra: str = "") -> str:
    mark = "[stale] " if item.id in stale_ids else ""
    return f"{_id8(item.id)} {mark}{_headline(item)}{extra}"


def _checkpoint_age(recent: list[MemoryItem]) -> int | None:
    if not recent:
        return None
    age = 0
    for item in reversed(recent):
        if _is_checkpoint(item):
            return age
        age += 1
    return age


def _subject(
    item: MemoryItem,
    max_len: int = SUBJECT_MAX,
    stale: bool = False,
) -> str:
    """One-line commit subject for prompt injection (not the full body)."""
    from agent_memory.identity import DEFAULT_AGENT, USER_AGENT

    agent = item.payload.get("agent") or DEFAULT_AGENT
    prefix = ""
    if item.payload.get("covers"):
        prefix = "Summary: "
    elif item.payload.get("user"):
        prefix = "user: "
        agent = USER_AGENT
    who = "" if agent in (USER_AGENT, "") else f"@{agent} "
    mark = "[stale] " if stale else ""
    # First line only — treat multi-paragraph agent dumps as commit bodies.
    head = (item.message or "").strip().split("\n", 1)[0].strip()
    if len(head) > max_len:
        head = head[: max_len - 1].rstrip() + "…"
    return f"[{item.id[:8]}] {mark}{who}{prefix}{head}"


def _line(item: MemoryItem) -> str:
    """Full message line (for timelines / expanded views)."""
    from agent_memory.identity import DEFAULT_AGENT, USER_AGENT

    agent = item.payload.get("agent") or DEFAULT_AGENT
    prefix = ""
    if item.payload.get("covers"):
        prefix = "Summary: "
    elif item.payload.get("user"):
        prefix = "user: "
        agent = USER_AGENT
    who = "" if agent in (USER_AGENT, "") else f"@{agent} "
    return f"[{item.id[:8]}] {who}{prefix}{item.message}"


def _transcript(item: MemoryItem) -> bool:
    payload = item.payload
    return bool(
        payload.get("auto")
        or payload.get("user")
        or payload.get("open_question")
    )


def _capped_line(item: MemoryItem, limit: int = FIND_BODY_MAX) -> str:
    text = _line(item)
    if len(text) <= limit:
        return text
    extra = len(text) - limit + 1
    return text[: limit - 1].rstrip() + f"… ({extra} more)"


def _child_lines(kids: list[MemoryItem]) -> list[str]:
    visible = kids[:FIND_CHILD_CAP]
    lines = [f"    {_subject(child)}" for child in visible]
    hidden = len(kids) - len(visible)
    if hidden:
        lines.append(f"    (+{hidden} more)")
    return lines


def render_timeline(items: list[MemoryItem], *, full: bool = False) -> str:
    """History: work notes, oldest first.

    Default skips chat transcript (user/auto) and folded children.
    full=True keeps every body, including folded steps.
    """
    fmt = _line if full else _subject
    by_id = {item.id: item for item in items}
    covered = {c for item in items for c in item.payload.get("covers", [])}
    lines = []
    kept: list[MemoryItem] = []
    for item in items:
        if item.id in covered:
            continue
        if not full and _transcript(item):
            continue
        kept.append(item)
    if not full and len(kept) > HISTORY_CAP:
        lines.append(
            f"(+{len(kept) - HISTORY_CAP} earlier — "
            "memory_find or history full=true)"
        )
        kept = kept[-HISTORY_CAP:]
    for item in kept:
        lines.append(fmt(item))
        if full:
            for child_id in item.payload.get("covers", []):
                child = by_id.get(child_id)
                if child is not None:
                    lines.append(f"    {fmt(child)}")
    return "\n".join(lines) or "Nothing saved yet."


def render_find(
    items: list[MemoryItem],
    *,
    full_ids: set[str] | None = None,
    children: dict[str, list[MemoryItem]] | None = None,
) -> str:
    """Search hits. Full body only for explicit ids; others are subjects.

    A summary hit lists a few covered child headlines underneath.
    """
    full_ids = full_ids or set()
    children = children or {}
    lines = []
    for item in items:
        if item.id in full_ids:
            lines.append(_capped_line(item))
        else:
            lines.append(_subject(item))
        lines.extend(_child_lines(children.get(item.id, [])))
    return "\n".join(lines) or "Nothing found."


def render(
    items: list[MemoryItem],
    branches: list[dict] | None = None,
    inspecting: dict | None = None,
    nudge_after: int = 30,
    *,
    compact: bool = False,
    stale_ids: set[str] | None = None,
) -> str:
    """Render MEMORY STATE.

    compact=True (prompt injection): goal, steering, open questions,
    current-branch marker, newest RECENT_CAP subjects.
    """
    history, recent = split_state(items)
    stale_ids = stale_ids or set()

    def line_for(item: MemoryItem) -> str:
        if compact:
            return _subject(item, stale=item.id in stale_ids)
        return _line(item)

    lines = []
    if compact:
        questions = split_open_questions(items)
        hide = {item.id for item in all_open_questions(items)}
        goal = _goal(items)
        steer = _steering(items, goal)
        recent = [item for item in recent if item.id not in hide]
        steps = recent
        recent = _recent_findings(items)
        if goal:
            lines.append("## Goal")
            lines.append(_entry_line(goal, set()))
            lines.append("")
        if steer:
            lines.append("## Steering")
            lines.append(_entry_line(steer, set()))
            lines.append("")
        if questions:
            lines.append("## Open questions")
            for question in questions:
                deps = "".join(
                    f" {_id8(item_id)}"
                    for item_id in question.payload.get("depends_on", [])
                )
                lines.append(_entry_line(question, set(), deps))
            lines.append("")
    if inspecting:
        lines.append(
            f"INSPECTING [{inspecting['id'][:8]}]: files are at that "
            f"snapshot. Call memory with action=inspect_done to return "
            f"to '{inspecting['branch']}'."
        )
        lines.append("")
    if branches and len(branches) > 1:
        if compact:
            current = next((b for b in branches if b["current"]), None)
            if current:
                lines.append(f"* {current['name']} (you are here)")
                lines.append("")
        else:
            lines.append("## Branches")
            for branch in branches:
                if branch["current"]:
                    lines.append(f"* {branch['name']} (you are here)")
                else:
                    item = branch["item"]
                    summary = item.message.split("\n")[0][:SUBJECT_MAX]
                    lines.append(
                        f"[{item.id[:8]}] {branch['name']}: {summary}"
                    )
            lines.append("")
    if compact:
        visible = recent[-RECENT_CAP:]
        hidden = len(recent) - len(visible)
        lines.append("## Recent")
        for item in visible:
            lines.append(line_for(item))
        if hidden:
            lines.append(
                f"(+{hidden} earlier — memory_find / memory_history)"
            )
        age = _checkpoint_age(steps)
        if age is not None and age >= CHECKPOINT_NUDGE_AFTER:
            lines.append(f"last @agent checkpoint {age} steps ago")
        if len(recent) > nudge_after:
            lines.append("")
            lines.append(
                f"Recent has {len(recent)} steps. "
                "Summarize the finished work before continuing."
            )
        return "\n".join(lines)
    if history:
        lines.append("## History")
        for item in history:
            lines.append(line_for(item))
        lines.append("")
    lines.append("## Recent")
    for item in recent:
        lines.append(line_for(item))
    if len(recent) > nudge_after:
        lines.append("")
        lines.append(
            f"Recent has {len(recent)} steps. "
            "Summarize the finished work before continuing."
        )
    return "\n".join(lines)

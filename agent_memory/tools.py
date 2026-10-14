from agent_memory.core import Memory
from agent_memory.prompt import (
    FIND_DEFAULT_K,
    SUBSTRING_FALLBACK,
    _transcript,
    render_find,
    render_timeline,
)

ACTIONS = (
    "save",
    "find",
    "delete",
    "history",
    "graph",
    "branch",
    "inspect",
    "inspect_done",
    "open_questions",
    "goal_create",
    "goal_list",
    "goal_use",
)
GOAL_ACTIONS = ("goal_create", "goal_list", "goal_use")

MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "memory",
        "strict": True,
        "description": (
            "Project memory. Actions: save (summary=true folds Recent); "
            "find (headlines; ids= for bodies); history; "
            "open_questions; delete; graph; branch/inspect "
            "(restores files; inspect_done to return); "
            "goal_create / goal_list / goal_use (isolated goal id + tree). "
            "Pass goal= to target a goal workspace; omit it for this project notebook."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "Which memory operation to run.",
                },
                "text": {
                    "type": ["string", "null"],
                    "description": (
                        "save: what to remember. "
                        "find: match a substring of the entry text. "
                    ),
                },
                "payload": {
                    "type": ["object", "null"],
                    "description": (
                        "save: optional metadata to filter on later. "
                        "find: match saved metadata."
                    ),
                    "additionalProperties": True,
                },
                "summary": {
                    "type": ["boolean", "null"],
                    "description": (
                        "save only: true to fold Recent behind this text."
                    ),
                },
                "ids": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "find / delete / open_questions: entry ids. "
                        "open_questions: notes the question stands on, "
                        "or questions to close."
                    ),
                },
                "query": {
                    "type": ["string", "null"],
                    "description": (
                        "find only: search by meaning. Needs an "
                        "embedding provider."
                    ),
                },
                "all_branches": {
                    "type": ["boolean", "null"],
                    "description": (
                        "find only: true to search every branch, not "
                        "just the current one."
                    ),
                },
                "html": {
                    "type": ["boolean", "null"],
                    "description": (
                        "graph only: true to also write an explorable "
                        "HTML page the user can open in a browser."
                    ),
                },
                "id": {
                    "type": ["string", "null"],
                    "description": (
                        "branch / inspect / goal_use: entry or goal id."
                    ),
                },
                "name": {
                    "type": ["string", "null"],
                    "description": "branch: new branch name.",
                },
                "label": {
                    "type": ["string", "null"],
                    "description": (
                        "branch only, required: summary of the work being left behind."
                    ),
                },
                "full": {
                    "type": ["boolean", "null"],
                    "description": (
                        "find/history: true for complete bodies. "
                        "Default is headlines."
                    ),
                },
                "agent": {
                    "type": ["string", "null"],
                    "description": (
                        "save only: who is writing this entry "
                        "(main, explore·abc123, …). Defaults to "
                        "AGENT_MEMORY_AGENT or main."
                    ),
                },
                "goal": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional goal id. Routes this call to that "
                        "goal's isolated workspace. Omit for the "
                        "project notebook. cwd=workspace needs no goal=."
                    ),
                },
            },
            "required": [
                "action",
                "text",
                "payload",
                "summary",
                "ids",
                "query",
                "all_branches",
                "html",
                "id",
                "name",
                "label",
                "full",
                "agent",
                "goal",
            ],
            "additionalProperties": False,
        },
    },
}

MEMORY_TOOLS = [MEMORY_TOOL]
MEMORY_TOOL_NAME = "memory"


# Shown on the live page. Reads that fire constantly (history) stay off it.
_LOGGED_ACTIONS = {
    "find",
    "save",
    "summarize",
    "delete",
    "branch",
    "inspect",
    "inspect_done",
    "open_questions",
    "goal_create",
    "goal_use",
}


def _clip(value, limit: int = 120) -> str:
    return str(value or "").split("\n", 1)[0].strip()[:limit]


def _id_list(raw) -> str:
    if not raw:
        return ""
    ids = [raw] if isinstance(raw, str) else list(raw)
    return ", ".join(str(item)[:8] for item in ids[:6])


def _action_detail(arguments: dict) -> str:
    action = arguments.get("action") or ""
    if action == "find":
        shown = _id_list(arguments.get("ids"))
        if shown:
            return "ids=" + shown
        if arguments.get("query"):
            return "query=" + _clip(arguments.get("query"), 100)
        if arguments.get("text"):
            return "text=" + _clip(arguments.get("text"), 100)
        return "headlines"
    if action == "save":
        return _clip(arguments.get("text"))
    if action == "delete":
        return "ids=" + _id_list(arguments.get("ids") or arguments.get("id"))
    if action == "branch":
        return _clip(arguments.get("label") or arguments.get("name"))
    if action == "open_questions":
        text = _clip(arguments.get("text"))
        return text or "close"
    if action in ("inspect", "goal_use"):
        return _clip(arguments.get("id"), 12)
    if action == "goal_create":
        return _clip(arguments.get("text"))
    return ""


def _note_action(memory: Memory, arguments: dict) -> None:
    """Record a tool call for the live page. Never fails the tool."""
    action = arguments.get("action") or ""
    if action == "save" and arguments.get("summary"):
        action = "summarize"
    if action not in _LOGGED_ACTIONS:
        return
    try:
        from agent_memory.live_html import record_action

        record_action(memory, action, _action_detail(arguments))
    except Exception:
        return


def call_tool(memory: Memory, name: str, arguments: dict) -> str:
    """Run the memory tool and return the text result for the model.

        call_tool(memory, "memory", {"action": "save", "text": "...", ...})
    """
    if name != MEMORY_TOOL_NAME:
        result = f"Unknown tool {name}."
        memory._last_tool_result = result
        return result
    arguments = dict(arguments or {})
    try:
        memory = _bound_memory(memory, arguments)
        result = _run_action(memory, arguments)
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as error:
        result = f"Error: {error}"
    else:
        _note_action(memory, arguments)
    memory._last_tool_result = result
    return result


def _bound_memory(memory: Memory, arguments: dict) -> Memory:
    goal = arguments.get("goal")
    action = arguments.get("action")
    if not goal or action in GOAL_ACTIONS:
        return memory
    from agent_memory.goals import memory_for

    return memory_for(memory._path, goal)


def _require(arguments: dict, *keys: str) -> None:
    missing = [key for key in keys if not arguments.get(key)]
    if missing:
        raise ValueError(
            "missing required argument(s) for this action: "
            + ", ".join(missing)
        )


def _run_action(memory: Memory, arguments: dict) -> str:
    action = arguments.get("action")
    if action not in ACTIONS:
        raise ValueError(
            f"unknown action {action!r}; "
            f"expected one of: {', '.join(ACTIONS)}"
        )
    if action == "goal_create":
        from agent_memory.goals import bind_packet, create_goal, format_created, host_project

        _require(arguments, "text")
        record = create_goal(host_project(memory._path), arguments["text"])
        return format_created(record, bind_packet(record["project"], record["id"]))
    if action == "goal_list":
        from agent_memory.goals import format_goal_list, host_project

        return format_goal_list(host_project(memory._path))
    if action == "goal_use":
        from agent_memory.goals import bind_packet, host_project

        _require(arguments, "id")
        return bind_packet(host_project(memory._path), arguments["id"])
    if action == "save":
        _require(arguments, "text")
        payload = dict(arguments.get("payload") or {})
        agent = arguments.get("agent")
        item_id = memory.save(
            arguments["text"],
            payload,
            summary=bool(arguments.get("summary") or False),
            agent=agent,
        )
        return f"Saved [{item_id[:8]}]."
    if action == "find":
        branch = None if arguments.get("all_branches") else "current"
        queried = bool(arguments.get("query"))
        explicit = bool(arguments.get("ids"))
        raw_k = arguments.get("limit") or arguments.get("k")
        k = int(raw_k) if raw_k is not None else (50 if explicit else FIND_DEFAULT_K)
        pool_k = k if explicit else max(k * 4, FIND_DEFAULT_K)
        items = memory.find(
            arguments.get("query"),
            ids=arguments.get("ids"),
            text=arguments.get("text"),
            payload=arguments.get("payload"),
            k=pool_k,
            branch=branch,
        )
        used_substring = memory._find_used_substring
        full = bool(arguments.get("full"))
        if not explicit:
            items = [
                item
                for item in items
                if not _transcript(item) or (full and item.payload.get("goal"))
            ]
            covered_by = {}
            if branch == "current":
                for state_item in memory.state():
                    for cid in state_item.payload.get("covers", []):
                        covered_by[cid] = state_item
            for item in items:
                for cid in item.payload.get("covers", []):
                    covered_by.setdefault(cid, item)
            if covered_by:
                folded = []
                seen_ids = set()
                for item in items:
                    target = covered_by.get(item.id, item)
                    if target.id not in seen_ids:
                        seen_ids.add(target.id)
                        folded.append(target)
                items = folded
            items = items[:k]
        cover_ids = [
            child_id
            for item in items
            for child_id in item.payload.get("covers", [])
        ]
        kids = (
            memory.find(ids=cover_ids, k=len(cover_ids), branch=branch)
            if cover_ids
            else []
        )
        by_id = {item.id: item for item in kids}
        children = {
            item.id: [
                by_id[child_id]
                for child_id in item.payload.get("covers", [])
                if child_id in by_id
            ]
            for item in items
            if item.payload.get("covers")
        }
        stale_ids = memory._stale_ids(items) if branch == "current" else set()
        result = render_find(
            items,
            full_ids={item.id for item in items} if explicit else set(),
            children=children,
            full=bool(arguments.get("full")),
            stale_ids=stale_ids,
        )
        if queried and used_substring:
            result = f"{result}\n\n{SUBSTRING_FALLBACK}"
        return result
    if action == "delete":
        raw = arguments.get("ids") or arguments.get("id")
        if not raw:
            raise ValueError(
                "missing required argument(s) for this action: ids"
            )
        targets = [raw] if isinstance(raw, str) else list(raw)
        hidden = []
        for item_id in targets:
            target = memory._resolve(item_id)
            memory.delete(target)
            hidden.append(target[:8])
        return "Deleted [" + "], [".join(hidden) + "]."
    if action == "history":
        hist = memory.history()
        return render_timeline(
            hist,
            full=bool(arguments.get("full")),
            stale_ids=memory._stale_ids(hist),
        )
    if action == "open_questions":
        if not (arguments.get("text") or "").strip():
            closed = memory.close_questions(arguments.get("ids"))
            if not closed:
                return "No open questions."
            shown = " ".join(f"[{item_id[:8]}]" for item_id in closed)
            return f"Closed {shown}."
        before = {item.id for item in memory.open_questions()}
        item_id = memory.open_question(
            arguments["text"],
            depends_on=arguments.get("ids"),
            agent=arguments.get("agent"),
        )
        if item_id in before:
            return f"Open question [{item_id[:8]}] already open."
        deps = "".join(f" [{d[:8]}]" for d in (arguments.get("ids") or []))
        return f"Open question [{item_id[:8]}] added.{deps}"
    if action == "graph":
        result = memory.graph()
        if arguments.get("html"):
            page = memory.export_html()
            result += f"\n\nExplorable version written to {page}"
        return result
    if action == "branch":
        _require(arguments, "id", "name", "label")
        before = memory._branch_name()
        memory.branch(arguments["id"], arguments["name"], arguments["label"])
        after = memory._branch_name()
        landed = memory.state()[-1]
        result = (
            f"You are now at [{landed.id[:8]}] on branch '{after}'. "
            "Working files were restored to this snapshot. Save why you moved."
        )
        if before and before != after:
            summary_id = memory._refs()[before]
            result = f"Summarized '{before}' as [{summary_id[:8]}]. " + result
        return result
    if action == "inspect":
        _require(arguments, "id")
        item = memory.inspect(arguments["id"])
        home = memory.inspecting()["branch"]
        first_line = item.message.split("\n")[0][:80]
        return (
            f"Files are now exactly as at [{item.id[:8]}] {first_line}. "
            f"Call memory with action=inspect_done to return to '{home}'."
        )
    if action == "inspect_done":
        branch = memory.inspect_done()
        return f"Back on '{branch}', files restored to its latest state."
    raise ValueError(f"unhandled action {action!r}")

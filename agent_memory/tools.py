from agent_memory.core import Memory
from agent_memory.prompt import (
    FIND_DEFAULT_K,
    SUBSTRING_FALLBACK,
    render_find,
    render_timeline,
    save_hygiene,
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
)

MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "memory",
        "strict": True,
        "description": (
            "Long-term memory for this agent. Pick an action:\n"
            "- save: store a step. Set summary=true when a piece of work "
            "is finished so Recent is folded behind this text (still "
            "retrievable with find).\n"
            "- open_questions: add an unsolved question (and the notes "
            "it stands on). Same text already open is a no-op. Empty "
            "text closes all, or the ids you pass.\n"
            "- find: search stored entries. Search returns headlines "
            "(plus child headlines under a summary). Pass ids for full "
            "bodies. An explicit id is found even on another branch.\n"
            "- delete: hide specific entries from Recent, find, and the "
            "live timeline. Use for noise that should not have been "
            "saved. Snapshots stay in git; the delete commit itself "
            "does not appear in Recent.\n"
            "- history: timeline of the current branch, oldest first, "
            "headlines with folded steps under their summary. Set full "
            "to true for complete bodies.\n"
            "- graph: visual tree of every branch and entry (like "
            "git log --graph). Set html=true to also write an "
            "explorable HTML page.\n"
            "- branch: restore the snapshot given by id; label summarizes "
            "the work being left behind. Restores working files.\n"
            "- inspect: set every working file to how it was at an "
            "entry, to look around. Read-only; call inspect_done to "
            "return.\n"
            "- inspect_done: return from inspect; current branch and "
            "its files are checked back out."
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
                        "find / delete / open_questions: entry ids as "
                        "shown in MEMORY STATE. open_questions: notes "
                        "the question stands on, or questions to close."
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
                        "branch / inspect: entry id from MEMORY STATE."
                    ),
                },
                "name": {
                    "type": ["string", "null"],
                    "description": "branch only: short name for the new branch.",
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
                        "history only: true for complete bodies. "
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
            ],
            "additionalProperties": False,
        },
    },
}

MEMORY_TOOLS = [MEMORY_TOOL]
MEMORY_TOOL_NAME = "memory"


def call_tool(memory: Memory, name: str, arguments: dict) -> str:
    """Run the memory tool and return the text result for the model.

        call_tool(memory, "memory", {"action": "save", "text": "...", ...})
    """
    if name != MEMORY_TOOL_NAME:
        result = f"Unknown tool {name}."
        memory._last_tool_result = result
        return result
    try:
        result = _run_action(memory, dict(arguments or {}))
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        result = f"Error: {error}"
    memory._last_tool_result = result
    return result


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
        result = f"Saved [{item_id[:8]}]."
        warn = save_hygiene(arguments["text"])
        return f"{result}\n{warn}" if warn else result
    if action == "find":
        branch = None if arguments.get("all_branches") else "current"
        queried = bool(arguments.get("query"))
        explicit = bool(arguments.get("ids"))
        items = memory.find(
            arguments.get("query"),
            ids=arguments.get("ids"),
            text=arguments.get("text"),
            payload=arguments.get("payload"),
            k=50 if explicit else FIND_DEFAULT_K,
            branch=branch,
        )
        used_substring = memory._find_used_substring
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
        result = render_find(
            items,
            full_ids={item.id for item in items} if explicit else set(),
            children=children,
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
        return render_timeline(
            memory.history(), full=bool(arguments.get("full"))
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

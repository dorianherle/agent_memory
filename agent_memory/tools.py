from agent_memory.core import Memory
from agent_memory.prompt import _line, render_timeline

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save",
            "strict": True,
            "description": (
                "Store a step in memory. Set summary to true when a piece "
                "of work is finished: everything in Recent is folded behind "
                "this text and stays retrievable with find."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What to remember.",
                    },
                    "payload": {
                        "type": ["object", "null"],
                        "description": "Optional metadata to filter on later.",
                        "additionalProperties": True,
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "True to fold Recent behind this text.",
                    },
                },
                "required": ["text", "payload", "summary"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find",
            "strict": True,
            "description": (
                "Read stored entries, including the steps folded behind a "
                "summary. An explicit id is found even on another branch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Entry ids as shown in MEMORY STATE.",
                    },
                    "text": {
                        "type": ["string", "null"],
                        "description": "Match a substring of the entry text.",
                    },
                    "payload": {
                        "type": ["object", "null"],
                        "description": "Match saved metadata.",
                        "additionalProperties": True,
                    },
                    "query": {
                        "type": ["string", "null"],
                        "description": (
                            "Search by meaning. Needs an embedding provider."
                        ),
                    },
                    "all_branches": {
                        "type": "boolean",
                        "description": (
                            "True to search every branch, not just the "
                            "current one."
                        ),
                    },
                },
                "required": ["ids", "text", "payload", "query", "all_branches"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history",
            "strict": True,
            "description": (
                "The full timeline of the current branch, oldest first: "
                "every entry, with folded steps shown under the summary "
                "that folded them. Use it to show the user everything."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph",
            "strict": True,
            "description": (
                "A visual graph of every branch and entry at once, like "
                "git log --graph. Use it to show the user the whole tree."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "branch",
            "strict": True,
            "description": (
                "Move to any entry on a branch: back past a dead end, or "
                "forward to a branch shown under Branches. The current "
                "branch is folded behind your summary and every working "
                "file is restored to the target entry's snapshot. Nothing "
                "is deleted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Entry id from MEMORY STATE to move to.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short name for the new branch.",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "One line summing up the current branch before "
                            "leaving it. It becomes the branch's label "
                            "under Branches."
                        ),
                    },
                },
                "required": ["id", "name", "summary"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "strict": True,
            "description": (
                "Set every working file to exactly how it was at an entry, "
                "to look around. Read-only: edits made while inspecting are "
                "discarded. Call inspect_done to return."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Entry id from MEMORY STATE to inspect.",
                    },
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_done",
            "strict": True,
            "description": (
                "Return from inspect: the current branch and its files are "
                "checked back out."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def call_tool(memory: Memory, name: str, arguments: dict) -> str:
    """Run one memory tool call and return the text result for the model."""
    try:
        result = _run_tool(memory, name, arguments)
    except (ValueError, RuntimeError) as error:
        result = f"Error: {error}"
    memory._last_tool_result = result
    return result


def _run_tool(memory: Memory, name: str, arguments: dict) -> str:
    if name == "save":
        item_id = memory.save(
            arguments["text"],
            arguments.get("payload"),
            summary=arguments.get("summary", False),
        )
        return f"Saved [{item_id[:8]}]."
    if name == "find":
        branch = None if arguments.get("all_branches") else "current"
        items = memory.find(
            arguments.get("query"),
            ids=arguments.get("ids"),
            text=arguments.get("text"),
            payload=arguments.get("payload"),
            k=50,
            branch=branch,
        )
        covered = [c for item in items for c in item.payload.get("covers", [])]
        items += memory.find(ids=covered, k=50, branch=branch)
        lines = "\n".join(_line(item) for item in items)
        return lines or "Nothing found."
    if name == "history":
        return render_timeline(memory.history())
    if name == "graph":
        return memory.graph()
    if name == "branch":
        before = memory._branch_name()
        memory.branch(arguments["id"], arguments["name"], arguments.get("summary"))
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
    if name == "inspect":
        item = memory.inspect(arguments["id"])
        home = memory.inspecting()["branch"]
        first_line = item.message.split("\n")[0][:80]
        return (
            f"Files are now exactly as at [{item.id[:8]}] {first_line}. "
            f"Call inspect_done to return to '{home}'."
        )
    if name == "inspect_done":
        branch = memory.inspect_done()
        return f"Back on '{branch}', files restored to its latest state."
    return f"Unknown tool {name}."

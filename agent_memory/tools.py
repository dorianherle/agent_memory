from agent_memory.core import Memory

TOOLS = [
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
                    "summary": {
                        "type": "boolean",
                        "description": "True to fold Recent behind this text.",
                    },
                },
                "required": ["text", "summary"],
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
                "summary."
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
                    "query": {
                        "type": ["string", "null"],
                        "description": (
                            "Search by meaning. Needs an embedding provider."
                        ),
                    },
                },
                "required": ["ids", "text", "query"],
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
                "Go back to an entry on a new branch when later work proved "
                "wrong. Nothing is deleted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Entry id from MEMORY STATE to return to.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Short name for the new branch.",
                    },
                },
                "required": ["id", "name"],
                "additionalProperties": False,
            },
        },
    },
]


def call_tool(memory: Memory, name: str, arguments: dict) -> tuple[Memory, str]:
    if name == "save":
        memory.save(
            arguments["text"],
            summary=arguments.get("summary", False),
            allow_empty=True,
        )
        return memory, "Saved."

    if name == "find":
        items = memory.find(
            arguments.get("query"),
            ids=arguments.get("ids"),
            text=arguments.get("text"),
            k=50,
        )
        covered = [c for item in items for c in item.payload.get("covers", [])]
        items += memory.find(ids=covered, k=50)
        lines = "\n".join(f"[{item.id[:8]}] {item.message}" for item in items)
        return memory, lines or "Nothing found."

    if name == "branch":
        memory = memory.branch(arguments["id"], arguments["name"])
        return memory, "You are back at that entry on a new branch. Save why."

    return memory, f"Unknown tool {name}."

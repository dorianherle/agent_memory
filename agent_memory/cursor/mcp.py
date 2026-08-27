"""MCP server: judgment + checkpoint tools for Cursor Agent.

Hooks auto-save at turn boundaries (user prompt / finished answer).
`memory_save` lets the agent checkpoint mid-turn during long work.

Works with mcp SDK v1 (`FastMCP`) and v2 (`MCPServer`).
"""

from __future__ import annotations

from agent_memory.cursor.common import get_memory
from agent_memory.prompt import save_hygiene
from agent_memory.tools import call_tool


def _mcp_server(name: str):
    """Return a high-level MCP server instance for the installed SDK."""
    try:
        from mcp.server import MCPServer

        return MCPServer(name)
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP(name)
    except ImportError as error:
        raise SystemExit(
            "Cursor MCP needs the `mcp` package. Install with:\n"
            '  pip install "agent-memory[cursor]"\n'
            f"Import error: {error}"
        ) from error


def _build_server():
    mcp = _mcp_server("agent-memory")

    @mcp.tool()
    def memory_state() -> str:
        """Compact MEMORY STATE for the agent (not for showing the user). Live HTML is at .agent_memory/live/index.html."""
        memory = get_memory()
        memory.write_state_file()
        return memory.render_state()

    @mcp.tool()
    def memory_find(
        query: str | None = None,
        text: str | None = None,
        ids: list[str] | None = None,
        all_branches: bool = False,
    ) -> str:
        """Search stored memory. Use query for meaning (falls back to substring
        match when no embedding provider is configured, or when embeddings
        fail — e.g. missing GEMINI_API_KEY — and says so), text for substring,
        or ids from MEMORY STATE.
        """
        return call_tool(
            get_memory(),
            "memory",
            {
                "action": "find",
                "query": query,
                "text": text,
                "ids": ids,
                "all_branches": all_branches,
            },
        )

    @mcp.tool()
    def memory_save(text: str, agent: str | None = None) -> str:
        """Checkpoint meaningful mid-turn progress before the turn ends.

        Hooks only save at turn boundaries (user prompt / finished answer).
        During long work, call this at milestones — findings, decisions,
        working checkpoints — so progress survives if the session dies.
        Prefer a few solid saves over silence; skip pure noise.
        Pass agent to tag who wrote it (defaults to AGENT_MEMORY_AGENT or agent).
        Refreshes the live HTML monitor automatically.
        """
        memory = get_memory()
        item_id = memory.save(text, agent=agent)
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        who = memory.find(ids=item_id, k=1)
        from agent_memory.identity import DEFAULT_AGENT

        label = who[0].payload.get("agent", DEFAULT_AGENT) if who else DEFAULT_AGENT
        result = f"Saved [{item_id[:8]}] @{label}."
        warn = save_hygiene(text)
        return f"{result}\n{warn}" if warn else result

    @mcp.tool()
    def memory_summarize(text: str, agent: str | None = None) -> str:
        """Fold finished work behind one line so Recent stays short. Still retrievable with memory_find.

        Pass agent to tag who did the work (defaults to AGENT_MEMORY_AGENT or agent).
        Refreshes the live HTML monitor automatically.
        """
        memory = get_memory()
        item_id = memory.summarize(text, agent=agent)
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        who = memory.find(ids=item_id, k=1)
        from agent_memory.identity import DEFAULT_AGENT

        label = who[0].payload.get("agent", DEFAULT_AGENT) if who else DEFAULT_AGENT
        result = f"Summarized [{item_id[:8]}] @{label}."
        warn = save_hygiene(text)
        return f"{result}\n{warn}" if warn else result

    @mcp.tool()
    def memory_delete(ids: list[str]) -> str:
        """Hide specific entries from Recent, find, and the live timeline.

        Use for noise that should not have been saved — not for finished
        work (summarize that). Snapshots stay in git; the delete commit
        itself does not appear in Recent. Pass ids from MEMORY STATE.
        Refreshes the live HTML monitor automatically.
        """
        memory = get_memory()
        result = call_tool(
            memory,
            "memory",
            {"action": "delete", "ids": ids},
        )
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        return result

    @mcp.tool()
    def memory_open_questions(
        text: str = "",
        depends_on: list[str] | None = None,
        ids: list[str] | None = None,
    ) -> str:
        """Add or close open questions.

        With text: add a question (depends_on = notes it stands on).
        Same text already open is a no-op. You can have several.
        Empty text closes all, or only ``ids``.
        """
        memory = get_memory()
        result = call_tool(
            memory,
            "memory",
            {
                "action": "open_questions",
                "text": text,
                "ids": depends_on or ids,
            },
        )
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        return result

    @mcp.tool()
    def memory_history() -> str:
        """Full timeline text for the agent (summaries with folded steps). Live UI: .agent_memory/live/index.html."""
        return call_tool(get_memory(), "memory", {"action": "history"})

    @mcp.tool()
    def memory_branch(id: str, name: str, label: str) -> str:
        """Leave a dead end. label is required: what went wrong. Restores working files to id."""
        memory = get_memory()
        result = call_tool(
            memory,
            "memory",
            {"action": "branch", "id": id, "name": name, "label": label},
        )
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        return result

    @mcp.tool()
    def memory_inspect(id: str) -> str:
        """Temporarily set working files to how they were at an entry. Call memory_inspect_done after."""
        return call_tool(get_memory(), "memory", {"action": "inspect", "id": id})

    @mcp.tool()
    def memory_inspect_done() -> str:
        """Leave inspect mode and restore the current branch's files."""
        memory = get_memory()
        result = call_tool(memory, "memory", {"action": "inspect_done"})
        memory.write_state_file()
        return result

    return mcp


def _strip_root_argv() -> None:
    """Remove our ``--root`` flag so the MCP SDK does not see unknown args."""
    import sys

    args = sys.argv[1:]
    cleaned = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == "--root":
            skip = True
            continue
        if arg.startswith("--root="):
            continue
        cleaned.append(arg)
    sys.argv = [sys.argv[0], *cleaned]


def main() -> None:
    # Pin root from argv first, then drop the flag before stdio serve.
    from agent_memory.cursor.common import remember_root

    remember_root()
    _strip_root_argv()
    _build_server().run()


if __name__ == "__main__":
    main()

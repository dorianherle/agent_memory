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
    def memory_find(
        query: str | None = None,
        text: str | None = None,
        ids: list[str] | None = None,
        all_branches: bool = False,
    ) -> str:
        """Search. query=/text= work headlines; ids= bodies (capped). Transcript omitted unless ids=."""
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
        """Mid-turn checkpoint. Pass agent. Skip noise."""
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
        """Fold finished work into one line. Pass agent."""
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
        """Hide noise ids from Recent/find. Snapshots stay in git."""
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
        """Add a question, or empty text to close ids/all."""
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
    def memory_history(full: bool = False) -> str:
        """Work-note headlines. full=True for the whole transcript."""
        return call_tool(
            get_memory(), "memory", {"action": "history", "full": full}
        )

    @mcp.tool()
    def memory_branch(id: str, name: str, label: str) -> str:
        """Summarize this line, restore files to id."""
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
        """Restore files to id. Call memory_inspect_done after."""
        return call_tool(get_memory(), "memory", {"action": "inspect", "id": id})

    @mcp.tool()
    def memory_inspect_done() -> str:
        """Leave inspect; restore the branch."""
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

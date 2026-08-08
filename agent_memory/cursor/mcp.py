"""MCP server: judgment/navigation tools for Cursor Agent.

Durability (auto.save) is handled by hooks — not exposed here.

Works with mcp SDK v1 (`FastMCP`) and v2 (`MCPServer`).
"""

from __future__ import annotations

from agent_memory.cursor.common import get_memory
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
        """Compact MEMORY STATE for the agent (not for showing the user). Prefer memory_write_canvas to display memory."""
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
        match when no embedding provider is configured), text for substring,
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
    def memory_summarize(text: str, agent: str | None = None) -> str:
        """Fold finished work behind one line so Recent stays short. Still retrievable with memory_find.

        Pass agent to tag who did the work (defaults to AGENT_MEMORY_AGENT or main).
        Refreshes the live canvas automatically.
        """
        memory = get_memory()
        item_id = memory.summarize(text, agent=agent)
        memory.write_state_file()
        try:
            memory.write_canvas(name="agent-memory-live")
        except Exception:
            pass
        who = memory.find(ids=item_id, k=1)
        label = who[0].payload.get("agent", "main") if who else "main"
        return f"Summarized [{item_id[:8]}] @{label}."

    @mcp.tool()
    def memory_history() -> str:
        """Full timeline text for the agent. To show memory to the user, call memory_write_canvas instead."""
        return call_tool(get_memory(), "memory", {"action": "history"})

    @mcp.tool()
    def memory_write_canvas(name: str = "agent-memory-live") -> str:
        """Write/refresh the live memory dashboard (only UI to show the user).

        Opens as agent-memory-live.canvas.tsx beside chat. Click a summary to
        drill into its subgraph; breadcrumbs go back. Hooks also refresh this
        file on save. Returns the absolute path — tell the user to open it.
        """
        path = get_memory().write_canvas(name=name or "agent-memory-live")
        return f"Live memory dashboard: {path}"

    @mcp.tool()
    def memory_branch(id: str, name: str, label: str | None = None) -> str:
        """Move to an entry (dead end or another branch). Restores working files to that snapshot."""
        memory = get_memory()
        result = call_tool(
            memory,
            "memory",
            {"action": "branch", "id": id, "name": name, "label": label},
        )
        memory.write_state_file()
        try:
            memory.write_canvas(name="agent-memory-live")
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

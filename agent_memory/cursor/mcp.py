"""MCP server: judgment/navigation tools for Cursor Agent.

Durability (auto.save) is handled by hooks — not exposed here.
"""

from __future__ import annotations

from agent_memory.cursor.common import get_memory
from agent_memory.tools import call_tool


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise SystemExit(
            "Cursor MCP needs the optional dependency. Install with:\n"
            '  pip install "agent-memory[cursor]"'
        ) from error

    mcp = FastMCP("agent-memory")

    @mcp.tool()
    def memory_state() -> str:
        """Show the current MEMORY STATE (branches, history, recent)."""
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
        """Search stored memory. Use query for meaning, text for substring, or ids from MEMORY STATE."""
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
    def memory_summarize(text: str) -> str:
        """Fold finished work behind one line so Recent stays short. Still retrievable with memory_find."""
        memory = get_memory()
        item_id = memory.summarize(text)
        memory.write_state_file()
        return f"Summarized [{item_id[:8]}]."

    @mcp.tool()
    def memory_history() -> str:
        """Full timeline of the current branch, with folded steps under their summary."""
        return call_tool(get_memory(), "memory", {"action": "history"})

    @mcp.tool()
    def memory_graph(html: bool = False) -> str:
        """Branch tree of every entry. Set html=true to write an explorable HTML page."""
        return call_tool(
            get_memory(), "memory", {"action": "graph", "html": html}
        )

    @mcp.tool()
    def memory_graph_data() -> str:
        """Structured memory graph JSON for building a Cursor canvas or custom UI.

        Returns {nodes, edges, kinds, branches}. Same extraction as the HTML
        graph — use this when the agent should render a .canvas.tsx (or other
        view) instead of opening graph.html. Does not write any files.
        """
        import json

        return json.dumps(get_memory().graph_data(), indent=2)

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


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()

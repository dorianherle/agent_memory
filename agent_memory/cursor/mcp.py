"""MCP server: judgment + checkpoint tools for Cursor Agent.

Hooks establish the initial goal and refresh state. `memory_save` lets the
agent preserve selected durable knowledge during long work.

Works with mcp SDK v1 (`FastMCP`) and v2 (`MCPServer`).
"""

from __future__ import annotations

from agent_memory.cursor.common import _strip_root_argv, get_memory, remember_root
from agent_memory.identity import resolve_agent
from agent_memory.live_html import record_action
from agent_memory.tools import call_tool


def _refresh_live(memory) -> None:
    try:
        memory.write_live_html()
    except Exception:
        pass


def _record_and_save(
    memory,
    text: str,
    *,
    action: str,
    verb: str,
    agent: str | None = None,
    payload: dict | None = None,
    summary: bool = False,
) -> str:
    item_id = memory.save(text, payload, summary=summary, agent=agent)
    record_action(memory, action, text.split("\n", 1)[0][:120])
    _refresh_live(memory)
    label = resolve_agent(agent, payload)
    return f"{verb} [{item_id[:8]}] @{label}."


def _mcp_server(name: str):
    """Return a high-level MCP server instance for the installed SDK."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            from mcp.server.fastmcp import FastMCP

            return FastMCP(name)
        except ImportError:
            pass
        try:
            from mcp.server import MCPServer

            return MCPServer(name)
        except ImportError as error:
            raise SystemExit(
                "Cursor MCP needs the `mcp` package. Install with:\n"
                '  pip install "agent-memory[cursor]"\n'
                f"Import error: {error}"
            ) from error


def _build_server():
    mcp = _mcp_server("agent-memory")

    @mcp.tool()
    def memory_goal_create(text: str) -> str:
        """Create a goal id and isolated git tree. Any IDE. cwd=workspace needs no goal=."""
        with get_memory() as memory:
            return call_tool(
                memory, "memory", {"action": "goal_create", "text": text}
            )

    @mcp.tool()
    def memory_goal_list() -> str:
        """List goal ids, titles, workspaces."""
        with get_memory() as memory:
            return call_tool(memory, "memory", {"action": "goal_list"})

    @mcp.tool()
    def memory_goal_use(id: str) -> str:
        """Bind packet for a goal id. Does not set a process-global current goal."""
        with get_memory() as memory:
            return call_tool(
                memory, "memory", {"action": "goal_use", "id": id}
            )

    @mcp.tool()
    def memory_find(
        query: str | None = None,
        text: str | None = None,
        ids: list[str] | None = None,
        all_branches: bool = False,
        full: bool = False,
        limit: int | None = None,
        goal: str | None = None,
    ) -> str:
        """Search. query=/text= work headlines (or full bodies with full=True); ids= bodies (capped unless full=True). Transcript omitted unless ids=."""
        with get_memory(goal) as memory:
            return call_tool(
                memory,
                "memory",
                {
                    "action": "find",
                    "query": query,
                    "text": text,
                    "ids": ids,
                    "all_branches": all_branches,
                    "full": full,
                    "limit": limit,
                },
            )

    @mcp.tool()
    def memory_save(
        text: str,
        agent: str | None = None,
        goal: str | None = None,
        payload: dict | None = None,
        files: list[str] | None = None,
        handoff: bool = False,
    ) -> str:
        """Save a finding, dead end, decision, or checkpoint. Pass files=[...] or handoff=True when applicable."""
        merged = dict(payload or {})
        if files:
            merged["files"] = [str(f) for f in files if str(f).strip()]
        if handoff:
            merged["handoff"] = True
        with get_memory(goal) as memory:
            return _record_and_save(
                memory,
                text,
                action="save",
                verb="Saved",
                agent=agent,
                payload=merged or None,
            )

    @mcp.tool()
    def memory_summarize(text: str, agent: str | None = None, goal: str | None = None) -> str:
        """Fold finished work into one line. Pass agent."""
        with get_memory(goal) as memory:
            return _record_and_save(
                memory,
                text,
                action="summarize",
                verb="Summarized",
                agent=agent,
                summary=True,
            )

    @mcp.tool()
    def memory_delete(ids: list[str], goal: str | None = None) -> str:
        """Hide noise ids from Recent/find. Snapshots stay in git."""
        with get_memory(goal) as memory:
            result = call_tool(
                memory,
                "memory",
                {"action": "delete", "ids": ids},
            )
            _refresh_live(memory)
            return result

    @mcp.tool()
    def memory_open_questions(
        text: str = "",
        depends_on: list[str] | None = None,
        ids: list[str] | None = None,
        goal: str | None = None,
    ) -> str:
        """Add a question, or empty text to close ids/all."""
        with get_memory(goal) as memory:
            result = call_tool(
                memory,
                "memory",
                {
                    "action": "open_questions",
                    "text": text,
                    "ids": depends_on or ids,
                },
            )
            _refresh_live(memory)
            return result

    @mcp.tool()
    def memory_history(full: bool = False, goal: str | None = None) -> str:
        """Work-note headlines. full=True for the whole transcript."""
        with get_memory(goal) as memory:
            return call_tool(
                memory, "memory", {"action": "history", "full": full}
            )

    @mcp.tool()
    def memory_branch(id: str, name: str, label: str, goal: str | None = None) -> str:
        """Summarize this line, restore files to id."""
        with get_memory(goal) as memory:
            result = call_tool(
                memory,
                "memory",
                {"action": "branch", "id": id, "name": name, "label": label},
            )
            _refresh_live(memory)
            return result

    @mcp.tool()
    def memory_inspect(id: str, goal: str | None = None) -> str:
        """Restore files to id. Call memory_inspect_done after."""
        with get_memory(goal) as memory:
            return call_tool(memory, "memory", {"action": "inspect", "id": id})

    @mcp.tool()
    def memory_inspect_done(goal: str | None = None) -> str:
        """Leave inspect; restore the branch."""
        with get_memory(goal) as memory:
            return call_tool(memory, "memory", {"action": "inspect_done"})

    return mcp


def main() -> None:
    # Pin root from argv first, then drop the flag before stdio serve.
    remember_root()
    _strip_root_argv()
    _build_server().run()


if __name__ == "__main__":
    main()

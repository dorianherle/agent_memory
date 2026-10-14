"""MCP server: judgment + checkpoint tools for Cursor Agent.

Hooks establish the initial goal and refresh state. `memory_save` lets the
agent preserve selected durable knowledge during long work.

Works with mcp SDK v1 (`FastMCP`) and v2 (`MCPServer`).
"""

from __future__ import annotations

from agent_memory.cursor.common import get_memory
from agent_memory.prompt import save_hygiene, summary_guidance
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
    def memory_goal_create(text: str) -> str:
        """Create a goal id and isolated git tree. Any IDE. cwd=workspace needs no goal=."""
        return call_tool(
            get_memory(), "memory", {"action": "goal_create", "text": text}
        )

    @mcp.tool()
    def memory_goal_list() -> str:
        """List goal ids, titles, workspaces."""
        return call_tool(get_memory(), "memory", {"action": "goal_list"})

    @mcp.tool()
    def memory_goal_use(id: str) -> str:
        """Bind packet for a goal id. Does not set a process-global current goal."""
        return call_tool(
            get_memory(), "memory", {"action": "goal_use", "id": id}
        )

    @mcp.tool()
    def memory_model(
        operation: str = "get",
        data: dict | None = None,
        id: str | None = None,
        base: str | None = None,
        target: str | None = None,
        agent: str | None = None,
        goal: str | None = None,
        full: bool = False,
    ) -> str:
        """Task model: get (full=True adds fixtures), propose(data=model, base=active id), check(id), accept(id), reduce, predict(data=inputs, target), handoff(data=model or {observations:[cases], residuals:[rules]} or null to reuse, base=active id). Rules accept 1-line formulas (when="x in ['a','b']", then="before != after"); specific rules override broader defaults. Handoff checks, accepts, reduces, and records a challenge prediction."""
        memory = get_memory(goal)
        result = call_tool(memory, "memory", {
            "action": "model_" + operation, "payload": data,
            "id": base if operation in ("propose", "handoff") else id,
            "name": target, "agent": agent, "full": full,
        })
        if operation in ("propose", "accept", "reduce", "handoff") and not result.startswith("Error:"):
            memory.write_state_file()
            try:
                memory.write_live_html()
            except Exception:
                pass
        return result

    @mcp.tool()
    def memory_find(
        query: str | None = None,
        text: str | None = None,
        ids: list[str] | None = None,
        all_branches: bool = False,
        full: bool = False,
        goal: str | None = None,
    ) -> str:
        """Search. query=/text= work headlines (or full bodies with full=True); ids= bodies (capped unless full=True). Transcript omitted unless ids=."""
        return call_tool(
            get_memory(goal),
            "memory",
            {
                "action": "find",
                "query": query,
                "text": text,
                "ids": ids,
                "all_branches": all_branches,
                "full": full,
            },
        )

    @mcp.tool()
    def memory_save(text: str, agent: str | None = None, goal: str | None = None) -> str:
        """Save a finding. After a completed milestone, call memory_summarize to fold its commits."""
        memory = get_memory(goal)
        item_id = memory.save(text, agent=agent)
        try:
            from agent_memory.live_html import record_action

            record_action(memory, "save", text.split("\n", 1)[0][:120])
        except Exception:
            pass
        memory.write_state_file()
        try:
            memory.write_live_html()
        except Exception:
            pass
        who = memory.find(ids=item_id, k=1)
        from agent_memory.identity import DEFAULT_AGENT

        label = who[0].payload.get("agent", DEFAULT_AGENT) if who else DEFAULT_AGENT
        result = f"Saved [{item_id[:8]}] @{label}.\n{summary_guidance()}"
        warn = save_hygiene(text)
        return f"{result}\n{warn}" if warn else result

    @mcp.tool()
    def memory_summarize(text: str, agent: str | None = None, goal: str | None = None) -> str:
        """Fold finished work into one line. Pass agent."""
        memory = get_memory(goal)
        item_id = memory.summarize(text, agent=agent)
        try:
            from agent_memory.live_html import record_action

            record_action(memory, "summarize", text.split("\n", 1)[0][:120])
        except Exception:
            pass
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
    def memory_delete(ids: list[str], goal: str | None = None) -> str:
        """Hide noise ids from Recent/find. Snapshots stay in git."""
        memory = get_memory(goal)
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
        goal: str | None = None,
    ) -> str:
        """Add a question, or empty text to close ids/all."""
        memory = get_memory(goal)
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
    def memory_history(full: bool = False, goal: str | None = None) -> str:
        """Work-note headlines. full=True for the whole transcript."""
        return call_tool(
            get_memory(goal), "memory", {"action": "history", "full": full}
        )

    @mcp.tool()
    def memory_branch(id: str, name: str, label: str, goal: str | None = None) -> str:
        """Summarize this line, restore files to id."""
        memory = get_memory(goal)
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
    def memory_inspect(id: str, goal: str | None = None) -> str:
        """Restore files to id. Call memory_inspect_done after."""
        return call_tool(get_memory(goal), "memory", {"action": "inspect", "id": id})

    @mcp.tool()
    def memory_inspect_done(goal: str | None = None) -> str:
        """Leave inspect; restore the branch."""
        memory = get_memory(goal)
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

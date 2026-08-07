"""Cursor IDE integration: MCP tools + hooks.

Memory owns durability (hooks auto-save). The agent owns judgment tools
via MCP (find, summarize, branch, inspect, …).
"""

from agent_memory.cursor.install import install_cursor

__all__ = ["install_cursor"]

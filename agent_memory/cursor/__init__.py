"""Cursor IDE integration: MCP tools + hooks.

Hooks auto-save at turn boundaries. MCP adds mid-turn checkpoints
(`memory_save`) plus judgment tools (find, summarize, branch, inspect, …).
"""

from agent_memory.cursor.install import install_cursor

__all__ = ["install_cursor"]

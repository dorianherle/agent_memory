"""Cursor IDE integration: MCP tools + hooks.

Hooks establish the goal and refresh state. MCP adds selective checkpoints
(`memory_save`) plus judgment tools (find, summarize, branch, inspect, …).
"""

from agent_memory.cursor.install import install_cursor

__all__ = ["install_cursor"]

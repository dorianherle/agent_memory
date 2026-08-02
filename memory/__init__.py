"""Git-backed memory for long-running research agents.

Search lives in .semantic and is imported separately, so the core stays
dependency-free and usable without an API key.
"""

from memory.git_memory import CommitEntry, Episode, Memory, Tag

__all__ = ["CommitEntry", "Episode", "Memory", "Tag"]

from __future__ import annotations

import os
from pathlib import Path

from agent_memory.core import Memory

ENV_ROOT = "AGENT_MEMORY_ROOT"


def memory_root() -> Path:
    """Project root that owns `.agent_memory/`."""
    raw = os.environ.get(ENV_ROOT)
    if raw:
        return Path(raw).resolve()
    return Path.cwd().resolve()


def get_memory() -> Memory:
    return Memory(memory_root())

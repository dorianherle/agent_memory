from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_memory.core import Memory

ENV_ROOT = "AGENT_MEMORY_ROOT"

_resolved_root: Path | None = None


def _usable_root(raw: str | None) -> Path | None:
    """Return a resolved root, or None if missing / unexpanded / useless."""
    if not raw:
        return None
    text = raw.strip()
    # Cursor sometimes leaves placeholders literal in MCP env.
    if "${" in text or text.startswith("$"):
        return None
    path = Path(text).expanduser()
    try:
        return path.resolve()
    except OSError:
        return None


def resolve_root(argv: list[str] | None = None) -> Path:
    """Resolve the project root without touching the process-wide cache."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--root" in args:
        index = args.index("--root")
        if index + 1 < len(args):
            root = _usable_root(args[index + 1])
            if root is not None:
                return root
    for arg in args:
        if arg.startswith("--root="):
            root = _usable_root(arg.split("=", 1)[1])
            if root is not None:
                return root
    root = _usable_root(os.environ.get(ENV_ROOT))
    if root is not None:
        return root
    return Path.cwd().resolve()


def remember_root(argv: list[str] | None = None) -> Path:
    """Resolve and pin the root for this process (call before stripping argv)."""
    global _resolved_root
    _resolved_root = resolve_root(argv)
    return _resolved_root


def memory_root(argv: list[str] | None = None) -> Path:
    """Project root that owns `.agent_memory/`.

    Uses the pinned root when ``remember_root()`` was called (MCP server),
    otherwise resolves from ``--root`` / env / cwd each time.
    """
    if argv is None and _resolved_root is not None:
        return _resolved_root
    return resolve_root(argv)


def get_memory() -> Memory:
    root = memory_root()
    # Gemini key + AGENT_MEMORY_PROVIDER from cursor install.
    from agent_memory.cursor.install import apply_credentials_env

    apply_credentials_env(root)
    return Memory(root)

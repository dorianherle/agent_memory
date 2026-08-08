"""Install Cursor MCP + hooks into a project so teammates get memory."""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

SERVER_NAME = "agent-memory"
RULE_NAME = "agent-memory.mdc"
CREDENTIALS_NAME = "credentials.env"

PROVIDER_GEMINI = "agent_memory.providers.gemini:GeminiProvider"

HOOK_EVENTS = (
    "sessionStart",
    "beforeSubmitPrompt",
    "afterAgentResponse",
    "subagentStart",
    "subagentStop",
)

RULE_BODY = """---
description: Long-term agent memory (MCP + automatic durability)
alwaysApply: true
---

# Agent Memory

Durability is automatic (Cursor hooks save user prompts and finished
answers). You do **not** need to save every step.

Subagents share the same memory store. Every entry is tagged with an
`agent` name (`user`, `main`, `explore·abc123`, …) so the live canvas
can color who said what.

Use **agent-memory** MCP tools for judgment:
- `memory_summarize` — fold finished work so Recent stays short (pass `agent`)
- `memory_find` / `memory_history` / `memory_graph` — recall detail
  (`query=` uses Gemini embeddings when configured at install)
- `memory_graph_data` — structured `{nodes, edges, kinds, branches, agents}`
- `memory_write_canvas` — refresh the live who-said-what dashboard
- `memory_branch` — leave a dead end; restores files to a snapshot
- `memory_inspect` / `memory_inspect_done` — look at an old snapshot
- `memory_state` — refresh MEMORY STATE

Read `@.cursor/MEMORY_STATE.md` for the compact current state.
Only use entry ids you can see there or in tool results.
"""


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _hook_command(python: str) -> str:
    return f"{python} -m agent_memory.cursor.hook"


def credentials_path(root: Path) -> Path:
    from agent_memory.core import HIDDEN_DIR

    return Path(root).resolve() / HIDDEN_DIR / CREDENTIALS_NAME


def write_credentials(path: Path, values: dict[str, str]) -> None:
    """Write KEY=value lines. Mode 0600 when the OS allows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items() if value]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_credentials(root: Path) -> dict[str, str]:
    """Load ``.agent_memory/credentials.env`` into a dict (does not mutate os.environ)."""
    path = credentials_path(root)
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def apply_credentials_env(root: Path) -> dict[str, str]:
    """Load credentials into ``os.environ`` for keys that are not already set."""
    loaded = load_credentials(root)
    for key, value in loaded.items():
        if value and not os.environ.get(key):
            os.environ[key] = value
    return loaded


def gemini_env(api_key: str) -> dict[str, str]:
    return {
        "AGENT_MEMORY_PROVIDER": PROVIDER_GEMINI,
        "GEMINI_API_KEY": api_key.strip(),
    }


def _prompt_gemini_key() -> str | None:
    """Interactive Gemini setup. Returns API key or None to skip."""
    print()
    print("Semantic memory_find uses Gemini embeddings.")
    print("  1) Configure Gemini now (GEMINI_API_KEY)")
    print("  2) Skip — substring search only")
    print()
    default = "1" if os.getenv("GEMINI_API_KEY") else "1"
    choice = input(f"Choose [1/2] (default {default}): ").strip() or default
    if choice not in {"1", "gemini", "y", "yes"}:
        return None

    existing = (os.getenv("GEMINI_API_KEY") or "").strip()
    if existing:
        use = input(
            "GEMINI_API_KEY is already set in this shell. Use it? [Y/n]: "
        ).strip().lower()
        if use in {"", "y", "yes"}:
            return existing
    key = getpass.getpass("Paste Gemini API key (input hidden): ").strip()
    return key or None


def resolve_gemini(
    *,
    api_key: str | None = None,
    skip_gemini: bool = False,
    interactive: bool = False,
) -> dict[str, str]:
    """Return env map for Gemini, or ``{}`` when skipped / missing key."""
    if skip_gemini:
        return {}
    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    if key:
        return gemini_env(key)
    if interactive and sys.stdin.isatty():
        prompted = _prompt_gemini_key()
        if prompted:
            return gemini_env(prompted)
    return {}


def ensure_gitignore(root: Path) -> Path | None:
    """Make sure ``.agent_memory/`` is ignored by the project git repo."""
    gitignore = Path(root) / ".gitignore"
    line = ".agent_memory/"
    if gitignore.exists():
        text = gitignore.read_text()
        if any(
            entry.strip() in {line, ".agent_memory", "**/.agent_memory/"}
            for entry in text.splitlines()
        ):
            return None
        suffix = "" if text.endswith("\n") or not text else "\n"
        gitignore.write_text(f"{text}{suffix}\n# agent-memory store + Gemini credentials\n{line}\n")
    else:
        gitignore.write_text(f"# agent-memory store + Gemini credentials\n{line}\n")
    return gitignore


def install_cursor(
    project: str | Path = ".",
    *,
    python: str | None = None,
    gemini_api_key: str | None = None,
    skip_gemini: bool = False,
    interactive: bool | None = None,
) -> dict[str, Path]:
    """Merge agent-memory into the project's `.cursor/` config.

    Safe to re-run: merges MCP servers and hook lists without wiping
    unrelated entries.

    Gemini setup (default on a TTY unless ``skip_gemini`` / key flags):
    prompts for ``GEMINI_API_KEY``, writes ``.agent_memory/credentials.env``,
    and injects the same env into ``.cursor/mcp.json`` so MCP can embed.
    """
    root = Path(project).resolve()
    cursor = root / ".cursor"
    cursor.mkdir(parents=True, exist_ok=True)
    py = python or sys.executable
    command = _hook_command(py)
    written: dict[str, Path] = {}

    if interactive is None:
        interactive = (
            not skip_gemini
            and gemini_api_key is None
            and sys.stdin.isatty()
        )
    provider_env = resolve_gemini(
        api_key=gemini_api_key,
        skip_gemini=skip_gemini,
        interactive=interactive,
    )

    mcp_path = cursor / "mcp.json"
    mcp = _read_json(mcp_path, {"mcpServers": {}})
    servers = mcp.setdefault("mcpServers", {})
    server: dict = {
        "command": py,
        "args": [
            "-m",
            "agent_memory.cursor.mcp",
            "--root",
            str(root),
        ],
    }
    # Non-secret only in mcp.json (often committed). The API key lives in
    # gitignored ``.agent_memory/credentials.env`` and is loaded at runtime.
    if provider_env.get("AGENT_MEMORY_PROVIDER"):
        server["env"] = {
            "AGENT_MEMORY_PROVIDER": provider_env["AGENT_MEMORY_PROVIDER"],
        }
    servers[SERVER_NAME] = server
    _write_json(mcp_path, mcp)
    written["mcp"] = mcp_path

    hooks_path = cursor / "hooks.json"
    hooks_doc = _read_json(hooks_path, {"version": 1, "hooks": {}})
    hooks_doc["version"] = 1
    hooks = hooks_doc.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = list(hooks.get(event) or [])
        entries = [
            entry
            for entry in entries
            if "agent_memory.cursor.hook" not in str(entry.get("command", ""))
        ]
        entries.append({"command": command})
        hooks[event] = entries
    _write_json(hooks_path, hooks_doc)
    written["hooks"] = hooks_path

    rules_dir = cursor / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule_path = rules_dir / RULE_NAME
    rule_path.write_text(RULE_BODY)
    written["rule"] = rule_path

    from agent_memory.core import Memory

    memory = Memory(root)
    written["state"] = memory.write_state_file()

    cred_path = credentials_path(root)
    if provider_env:
        write_credentials(cred_path, provider_env)
        written["credentials"] = cred_path
    elif skip_gemini and cred_path.exists():
        # Explicit skip: clear so hooks/MCP don't keep a stale key.
        write_credentials(cred_path, {})
        written["credentials"] = cred_path

    ignore = ensure_gitignore(root)
    if ignore is not None:
        written["gitignore"] = ignore

    return written

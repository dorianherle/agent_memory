"""Install Cursor MCP + hooks into a project so teammates get memory."""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

SERVER_NAME = "agent-memory"
RULE_NAME = "agent-memory.mdc"
SDK_GUIDE_NAME = "agent-memory-sdk.md"
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

Hooks auto-save user prompts and finished answers at turn boundaries.
During long mid-turn work, call `memory_save` for durable checkpoints
(milestones, findings, decisions). Do **not** save every trivial step.

Subagents share the same memory store. Every entry is tagged with an
`agent` name (`user`, `main`, `explore·abc123`, …) so the live canvas
can color who said what.

Use **agent-memory** MCP tools for judgment and checkpoints:
- `memory_save` — mid-turn checkpoint before the turn ends (pass `agent`)
- `memory_summarize` — fold finished work so Recent stays short (pass `agent`)
- `memory_find` — recall detail (`query=` uses Gemini when configured)
- `memory_write_canvas` — Cursor canvas live dashboard
- Browser monitor: `.agent_memory/live/index.html` (auto-created; self-refreshing)
- `memory_branch` — leave a dead end; restores files to a snapshot
- `memory_inspect` / `memory_inspect_done` — look at an old snapshot
- `memory_state` — agent context only (do not dump graphs/history in chat to “show” memory)

Read `@.cursor/MEMORY_STATE.md` for the compact current state.
Only use entry ids you can see there or in tool results.
"""

SDK_GUIDE_BODY = """# Agent Memory + Cursor SDK

`agent-memory cursor install` already wrote the project files this needs:

- `.cursor/mcp.json` — `agent-memory` MCP server
- `.cursor/hooks.json` — automatic saves (loaded from this project `cwd`)

## Local SDK (required opt-in for MCP)

Local Cursor SDK agents **do not** load `.cursor/mcp.json` by default.
Pass project settings so `memory_*` tools appear:

### Python

```python
from dotenv import load_dotenv
from cursor_sdk import Agent, LocalAgentOptions

load_dotenv()  # CURSOR_API_KEY

with Agent.create(
    model="composer-2.5",
    local=LocalAgentOptions(
        cwd=".",  # this project (has .cursor/mcp.json)
        setting_sources=["project"],  # ← loads agent-memory MCP
    ),
) as agent:
    result = agent.send("What do we already know?").wait()
    print(result.result)
```

### TypeScript

```ts
import { Agent } from "@cursor/sdk";

const agent = Agent.create({
  model: { id: "composer-2.5" },
  local: {
    cwd: process.cwd(),
    settingSources: ["project"], // ← loads agent-memory MCP
  },
});
```

## What you get

| Piece | How it activates |
|-------|------------------|
| MCP `memory_*` tools (including mid-turn `memory_save`) | `setting_sources=["project"]` / `settingSources: ["project"]` |
| Hooks auto-save (turn boundaries only) | automatic when `cwd` is this project (no settingSources needed) |

Inline `mcp_servers` / `mcpServers` also works if you prefer not to use ambient project settings.

Cloud SDK agents load project MCP/hooks from the repo differently — see Cursor SDK docs.
"""


class GeminiSetupError(ValueError):
    """Raised when Gemini was neither configured nor explicitly skipped."""


def sdk_next_steps_lines() -> list[str]:
    """Lines printed after install so SDK users do not miss setting_sources."""
    return [
        "Next (Cursor IDE):",
        "  Reload MCP in Cursor Settings → Tools & MCP",
        "  Start an Agent chat — hooks auto-save; use memory_* tools",
        "",
        "Next (Cursor SDK — local, no wrap):",
        "  Local agents ignore .cursor/mcp.json unless you opt in:",
        '    Python: LocalAgentOptions(cwd=".", setting_sources=["project"])',
        '    TypeScript: local: { cwd: ".", settingSources: ["project"] }',
        f"  Full recipe: .cursor/{SDK_GUIDE_NAME}",
        "  Hooks still load automatically from this project's .cursor/hooks.json",
    ]


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


def load_dotenv_key(root: Path, name: str = "GEMINI_API_KEY") -> str | None:
    """Read a single key from project ``.env`` if present (no overwrite of os.environ)."""
    path = Path(root).resolve() / ".env"
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() == name:
            value = value.strip().strip("'").strip('"')
            return value or None
    return None


def gemini_env(api_key: str) -> dict[str, str]:
    return {
        "AGENT_MEMORY_PROVIDER": PROVIDER_GEMINI,
        "GEMINI_API_KEY": api_key.strip(),
    }


def _find_gemini_key(
    root: Path,
    *,
    api_key: str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(key, source)`` from flag, env, credentials, or project ``.env``."""
    if api_key and api_key.strip():
        return api_key.strip(), "flag"
    env = (os.getenv("GEMINI_API_KEY") or "").strip()
    if env:
        return env, "environment"
    cred = (load_credentials(root).get("GEMINI_API_KEY") or "").strip()
    if cred:
        return cred, "credentials.env"
    dotenv = load_dotenv_key(root)
    if dotenv:
        return dotenv, ".env"
    return None, None


def _prompt_gemini_choice(root: Path, existing: str | None, source: str | None) -> dict[str, str]:
    """Always ask: configure Gemini or explicitly skip. Never silent."""
    print()
    print("=== Gemini embeddings (required choice) ===")
    print("Semantic memory_find(query=...) needs a Gemini API key.")
    print("  1) Configure Gemini")
    print("  2) Skip embeddings (substring search only) — type 2 to confirm")
    print()
    if existing and source:
        print(f"Found GEMINI_API_KEY via {source}.")
    choice = ""
    while choice not in {"1", "2"}:
        choice = input("Choose 1 or 2: ").strip()
        if choice not in {"1", "2"}:
            print("Please enter 1 (configure) or 2 (skip).")

    if choice == "2":
        print("Skipping Gemini — memory_find will use substring match only.")
        return {}

    if existing:
        use = input(f"Use the key from {source}? [Y/n]: ").strip().lower()
        if use in {"", "y", "yes"}:
            return gemini_env(existing)
    key = getpass.getpass("Paste Gemini API key (input hidden): ").strip()
    if not key:
        raise GeminiSetupError(
            "No Gemini API key entered. Re-run and choose 1 with a key, "
            "or choose 2 to skip embeddings."
        )
    return gemini_env(key)


def resolve_gemini(
    root: Path,
    *,
    api_key: str | None = None,
    skip_gemini: bool = False,
    interactive: bool = False,
) -> dict[str, str]:
    """Resolve Gemini env — never silently omit the decision.

    - ``skip_gemini``: explicit off
    - interactive TTY: always prompt (configure vs skip)
    - non-interactive: need flag/env/.env/credentials, else raise
    """
    if skip_gemini:
        return {}

    existing, source = _find_gemini_key(root, api_key=api_key)

    if interactive and sys.stdin.isatty():
        return _prompt_gemini_choice(root, existing, source)

    if existing:
        if source and source != "flag":
            print(f"Using GEMINI_API_KEY from {source}.")
        return gemini_env(existing)

    raise GeminiSetupError(
        "Gemini setup is required (no silent skip).\n"
        "  agent-memory cursor install --gemini-api-key \"$GEMINI_API_KEY\"\n"
        "  # or put GEMINI_API_KEY in the project .env\n"
        "  # or explicitly opt out:\n"
        "  agent-memory cursor install --skip-gemini"
    )


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
        gitignore.write_text(
            f"{text}{suffix}\n# agent-memory store + Gemini credentials\n{line}\n"
        )
    else:
        gitignore.write_text(
            f"# agent-memory store + Gemini credentials\n{line}\n"
        )
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
    unrelated entries. Also writes ``.cursor/agent-memory-sdk.md`` so
    Cursor SDK users know to pass ``setting_sources=["project"]``.

    Gemini is not skipped silently: a TTY always prompts (configure vs
    skip); non-interactive installs need ``--gemini-api-key`` /
    ``GEMINI_API_KEY`` / project ``.env`` / existing credentials, or
    explicit ``--skip-gemini``.
    """
    root = Path(project).resolve()
    cursor = root / ".cursor"
    cursor.mkdir(parents=True, exist_ok=True)
    py = python or sys.executable
    command = _hook_command(py)
    written: dict[str, Path] = {}

    if interactive is None:
        # Always prefer asking when we can — never default to silent skip.
        interactive = not skip_gemini and sys.stdin.isatty()

    provider_env = resolve_gemini(
        root,
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
    else:
        # Explicit skip / no provider: drop stale provider env from mcp.json.
        server.pop("env", None)
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

    sdk_guide_path = cursor / SDK_GUIDE_NAME
    sdk_guide_path.write_text(SDK_GUIDE_BODY)
    written["sdk_guide"] = sdk_guide_path

    from agent_memory.core import Memory

    memory = Memory(root)
    written["state"] = memory.write_state_file()

    cred_path = credentials_path(root)
    if provider_env:
        write_credentials(cred_path, provider_env)
        written["credentials"] = cred_path
    elif skip_gemini:
        write_credentials(cred_path, {})
        written["credentials"] = cred_path

    ignore = ensure_gitignore(root)
    if ignore is not None:
        written["gitignore"] = ignore

    return written

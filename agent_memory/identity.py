"""Agent identity on memory entries (who said what).

Every saved item gets ``payload["agent"]`` so shared memory across the
parent agent and subagents can be colored and filtered in the dashboard.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ENV_AGENT = "AGENT_MEMORY_AGENT"
SESSIONS_FILE = "agent_sessions.json"

DEFAULT_AGENT = "main"
USER_AGENT = "user"

# Stable category keys for Cursor canvas ``theme.category`` / HTML fallback.
AGENT_COLORS = (
    "blue",
    "green",
    "orange",
    "purple",
    "cyan",
    "pink",
    "yellow",
    "gray",
)

# Hex used by the standalone HTML graph (canvas uses theme tokens instead).
AGENT_COLOR_HEX = {
    "blue": "#7BAFE9",
    "green": "#3FA266",
    "orange": "#D08770",
    "purple": "#9386F2",
    "cyan": "#81A1C1",
    "pink": "#B48EAD",
    "yellow": "#F1B467",
    "gray": "#A0A0A0",
}


def normalize_agent(name: str | None) -> str:
    """Short, filesystem-safe agent label."""
    text = (name or "").strip()
    if not text:
        return DEFAULT_AGENT
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^A-Za-z0-9._\-·]+", "", text)
    return text[:48] or DEFAULT_AGENT


def resolve_agent(
    explicit: str | None = None,
    payload: dict | None = None,
) -> str:
    """Pick agent name: explicit → payload → env → main."""
    if explicit and str(explicit).strip():
        return normalize_agent(str(explicit))
    if payload:
        nested = payload.get("agent")
        if nested and str(nested).strip():
            return normalize_agent(str(nested))
    env = os.environ.get(ENV_AGENT)
    if env and env.strip():
        return normalize_agent(env)
    return DEFAULT_AGENT


def agent_color_key(name: str) -> str:
    """Map an agent name to a stable category color key."""
    normalized = normalize_agent(name)
    if normalized == USER_AGENT:
        return "blue"
    if normalized == DEFAULT_AGENT:
        return "green"
    digest = sum(ord(ch) for ch in normalized)
    # Skip reserved blue/green so other agents don't collide with user/main.
    pool = AGENT_COLORS[2:]
    return pool[digest % len(pool)]


def agent_color_hex(name: str) -> str:
    return AGENT_COLOR_HEX[agent_color_key(name)]


def display_name(agent: str, *, kind: str | None = None) -> str:
    """Human label for timelines."""
    if kind == "user" or agent == USER_AGENT:
        return "user"
    return agent


def sessions_path(git_dir: Path) -> Path:
    return Path(git_dir) / SESSIONS_FILE


def load_sessions(git_dir: Path) -> dict:
    path = sessions_path(git_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_sessions(git_dir: Path, data: dict) -> None:
    path = sessions_path(git_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def register_session(
    git_dir: Path,
    session_id: str,
    *,
    agent: str,
    agent_type: str | None = None,
    task: str | None = None,
    parent_id: str | None = None,
) -> str:
    """Remember which agent owns a conversation/subagent session."""
    name = normalize_agent(agent)
    sessions = load_sessions(git_dir)
    sessions[session_id] = {
        "agent": name,
        "type": agent_type or name,
        "task": (task or "")[:200],
        "parent_id": parent_id,
    }
    save_sessions(git_dir, sessions)
    return name


def lookup_session_agent(git_dir: Path, session_id: str | None) -> str | None:
    if not session_id:
        return None
    row = load_sessions(git_dir).get(session_id)
    if not isinstance(row, dict):
        return None
    return normalize_agent(row.get("agent") or DEFAULT_AGENT)


def subagent_label(subagent_type: str, subagent_id: str | None = None) -> str:
    """Stable short name for a Task subagent."""
    base = normalize_agent(subagent_type or "subagent")
    if not subagent_id:
        return base
    short = re.sub(r"[^A-Za-z0-9]", "", subagent_id)[:6]
    return normalize_agent(f"{base}·{short}" if short else base)


def ensure_agent_payload(
    payload: dict | None,
    *,
    agent: str | None = None,
    default: str = DEFAULT_AGENT,
) -> dict:
    """Return a copy of payload with ``agent`` set."""
    out = dict(payload or {})
    if agent and str(agent).strip():
        out["agent"] = normalize_agent(str(agent))
        return out
    if out.get("agent"):
        out["agent"] = normalize_agent(str(out["agent"]))
        return out
    env = os.environ.get(ENV_AGENT)
    if env and env.strip():
        out["agent"] = normalize_agent(env)
        return out
    out["agent"] = normalize_agent(default)
    return out

"""Goal spaces: an id plus an isolated tree. Memory(workspace) is the store."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_memory.core import HIDDEN_DIR, Memory

GOALS_DIR = "goals"
REGISTRY = "goals.json"
MARKER = "GOAL"
COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    HIDDEN_DIR,
    ".agent_memory_archive_*",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    "*.pyc",
)


def host_project(path: str | Path) -> Path:
    """Project that owns the goal registry, or path itself."""
    path = Path(path).resolve()
    for candidate in [path, *path.parents]:
        if (candidate / HIDDEN_DIR / REGISTRY).exists():
            return candidate
    parts = path.parts
    if HIDDEN_DIR in parts and GOALS_DIR in parts:
        index = parts.index(HIDDEN_DIR)
        if index + 2 < len(parts) and parts[index + 1] == GOALS_DIR:
            return Path(*parts[:index])
    return path


def _hidden(project: Path) -> Path:
    return Path(project).resolve() / HIDDEN_DIR


def _registry_path(project: Path) -> Path:
    return _hidden(project) / REGISTRY


def _load(project: Path) -> list[dict]:
    path = _registry_path(project)
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return list(data.get("goals") or [])


def _dump(project: Path, goals: list[dict]) -> None:
    hidden = _hidden(project)
    hidden.mkdir(parents=True, exist_ok=True)
    _registry_path(project).write_text(json.dumps({"goals": goals}, indent=2) + "\n")


def _new_id(existing: set[str]) -> str:
    for _ in range(16):
        item_id = secrets.token_hex(4)
        if item_id not in existing:
            return item_id
    raise RuntimeError("could not allocate a goal id")


def _title(text: str) -> str:
    head = (text or "").strip().split("\n", 1)[0].strip()
    if len(head) > 90:
        head = head[:89].rsplit(" ", 1)[0].rstrip() + "…"
    return head or "untitled"


def _is_git(project: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _has_head(project: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    return result.returncode == 0


def _checkout(project: Path, goal_id: str, workspace: Path) -> str:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    branch = f"goal/{goal_id}"
    if _is_git(project) and _has_head(project):
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "worktree",
                "add",
                "-b",
                branch,
                str(workspace),
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return branch
    shutil.copytree(project, workspace, ignore=COPY_IGNORE, symlinks=True)
    return ""


def create_goal(project: str | Path, text: str) -> dict:
    """Make a goal id and isolated tree. Notebook lives in that workspace."""
    project = host_project(project)
    text = (text or "").strip()
    if not text:
        raise ValueError("goal text is required")
    goals = _load(project)
    goal_id = _new_id({item["id"] for item in goals})
    workspace = _hidden(project) / GOALS_DIR / goal_id / "workspace"
    branch = _checkout(project, goal_id, workspace)
    with Memory(workspace) as memory:
        memory.ensure_goal(text)
    (workspace / HIDDEN_DIR / MARKER).write_text(goal_id + "\n")
    record = {
        "id": goal_id,
        "title": _title(text),
        "goal": text,
        "workspace": str(workspace.relative_to(project)),
        "branch": branch,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    goals.append(record)
    _dump(project, goals)
    return resolve_goal(project, goal_id)


def resolve_goal(project: str | Path, goal_id: str) -> dict:
    project = host_project(project)
    prefix = (goal_id or "").strip().lower()
    if not prefix:
        raise ValueError("unknown memory id ''")
    matches = [
        item
        for item in _load(project)
        if item["id"].startswith(prefix) or item["id"][:8] == prefix[:8]
    ]
    if not matches:
        raise ValueError(f"unknown goal id {goal_id!r}")
    if len(matches) > 1 and not any(item["id"] == prefix for item in matches):
        raise ValueError(f"ambiguous goal id {goal_id!r}")
    record = next((item for item in matches if item["id"] == prefix), matches[0])
    workspace = (project / record["workspace"]).resolve()
    return {**record, "project": str(project), "workspace": str(workspace)}


def list_goals(project: str | Path) -> list[dict]:
    project = host_project(project)
    return [resolve_goal(project, item["id"]) for item in _load(project)]


def memory_for(path: str | Path, goal_id: str | None = None) -> Memory:
    """Goal workspace store, or Memory(path) when goal_id is omitted."""
    path = Path(path).resolve()
    if not goal_id:
        return Memory(path)
    return Memory(resolve_goal(path, goal_id)["workspace"])


def bind_packet(project: str | Path, goal_id: str, question: str | None = None) -> str:
    record = resolve_goal(project, goal_id)
    with Memory(record["workspace"]) as memory:
        packet = memory.render_packet(question)
    branch = record.get("branch") or "(copy)"
    return (
        f"Goal [{record['id']}] {record['title']}\n"
        f"Workspace: {record['workspace']}\n"
        f"Branch: {branch}\n\n"
        f"{packet}"
    )


def format_goal_list(project: str | Path) -> str:
    rows = list_goals(project)
    if not rows:
        return "No goals."
    lines = []
    for row in rows:
        lines.append(f"[{row['id']}] {row['title']}")
        lines.append(f"  workspace: {row['workspace']}")
        if row.get("branch"):
            lines.append(f"  branch: {row['branch']}")
    return "\n".join(lines)


def format_created(record: dict, packet: str) -> str:
    branch = record.get("branch") or "(copy)"
    return (
        f"Created goal [{record['id']}] {record['title']}\n"
        f"Workspace: {record['workspace']}\n"
        f"Branch: {branch}\n\n"
        f"{packet}"
    )

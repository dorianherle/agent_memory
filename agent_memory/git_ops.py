"""Git plumbing, commit-record framing, exclude maintenance, and file-drift analysis."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

HIDDEN_DIR = ".agent_memory"
ARCHIVE_PREFIX = ".agent_memory_archive_"
PAYLOAD_PREFIX = "Agent-Memory-Payload: "
OPERATION_KEY = "_agent_memory"
FIELD_SEPARATOR = "\x00"
RECORD_SEPARATOR = "\x02"
GENERATED_STATE_FILES = (
    ".cursor/MEMORY_STATE.md",
    ".cursor/rules/agent-memory-state.mdc",
)

EXCLUDE_ENTRIES = (
    f"{HIDDEN_DIR}/",
    f"{ARCHIVE_PREFIX}*/",
    ".git/",
    ".svn/",
    ".hg/",
    ".env",
    "__pycache__/",
    ".pytest_cache/",
    "node_modules/",
    ".venv/",
    "venv/",
    ".tox/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".next/",
    *GENERATED_STATE_FILES,
)

_SKIP_SCAN_DIRS = frozenset(
    entry.rstrip("/")
    for entry in EXCLUDE_ENTRIES
    if entry.endswith("/") and "*" not in entry
)

_FILE_TOKEN_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|cpp|c|h|hpp|json|yaml|yml|toml|sql|sh|md|txt|cfg|ini)\b"
)
_GLOBAL_DECISION_RE = re.compile(
    r"^(decision|constraint|policy|convention|architecture)\s*:",
    re.IGNORECASE,
)


def nested_repo_excludes(root: Path) -> list[str]:
    """Paths of nested git checkouts to keep out of ``git add -A``."""
    found: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        current = Path(dirpath)
        if current != root and (".git" in dirs or ".git" in files):
            try:
                rel = current.relative_to(root).as_posix().rstrip("/") + "/"
                found.append(rel)
            except ValueError:
                pass
            dirs.clear()
            continue
        dirs[:] = [
            d
            for d in dirs
            if d not in _SKIP_SCAN_DIRS and not d.startswith(ARCHIVE_PREFIX)
        ]
    return sorted(set(found))


def ensure_exclude(
    path: Path, root: Path, *, scan_nested: bool = True
) -> list[str]:
    """Keep ``info/exclude`` up to date (merge, never wipe user lines)."""
    raw = path.read_text() if path.exists() else ""
    existing = [line.rstrip("\n") for line in raw.splitlines()]
    known = {line.strip() for line in existing if line.strip()}
    lines = list(existing)
    for entry in EXCLUDE_ENTRIES:
        if entry not in known:
            lines.append(entry)
    nested = nested_repo_excludes(root) if scan_nested else []
    for entry in nested:
        if entry not in known:
            lines.append(entry)
            known.add(entry)
    updated = "\n".join(lines).rstrip() + "\n"
    if updated != raw:
        path.write_text(updated)
    return nested


def parse_record(raw: str) -> dict:
    item_id, parents, timestamp, body = raw.split(FIELD_SEPARATOR, 3)
    marker = f"\n\n{PAYLOAD_PREFIX}"
    if marker in body:
        message, payload_text = body.rsplit(marker, 1)
        payload = json.loads(payload_text.strip())
    else:
        message, payload = body, {}
    payload = dict(payload)
    operation = payload.pop(OPERATION_KEY, {})
    message_format = operation.get("message_format")
    if message_format == "json-v1":
        message = json.loads(message)
    return {
        "id": item_id.strip(),
        "parents": parents.split(),
        "datetime": datetime.fromisoformat(timestamp),
        "message": (
            message
            if message_format in ("verbatim-v1", "json-v1")
            else message.strip()
        ),
        "payload": payload,
        "kind": operation.get("operation", "put"),
        "target_id": operation.get("target"),
    }


def read_records(
    run_git: Callable[..., str],
    new_tips: list[str],
    old_tips: list[str],
) -> list[dict]:
    if not new_tips:
        return []
    arguments = [
        "log",
        "--reverse",
        "--format=%H%x00%P%x00%cI%x00%B%x02",
        *new_tips,
    ]
    if old_tips:
        arguments.extend(["--not", *old_tips])
    raw = run_git(*arguments)
    try:
        return [
            parse_record(record)
            for record in raw.split(RECORD_SEPARATOR)
            if record.strip()
        ]
    except ValueError:
        id_arguments = list(arguments)
        id_arguments[2] = "--format=%H"
        ids = run_git(*id_arguments).splitlines()
        return [
            parse_record(
                run_git(
                    "show",
                    "--no-patch",
                    "--format=%H%x00%P%x00%cI%x00%B",
                    item_id,
                )
            )
            for item_id in ids
        ]


def head_file_history(
    run_git: Callable[..., str],
    head: str,
    commit_files_cache: dict[str, set[str]],
) -> tuple[dict[str, set[str]], set[str], dict[str, str]]:
    raw = run_git(
        "log", "--root", "--pretty=format:\x02%H", "--name-only", head, check=False
    )
    commit_order: list[str] = []
    commit_files: dict[str, set[str]] = {}
    known_files: set[str] = set()
    for block in raw.split(RECORD_SEPARATOR):
        if not block.strip():
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        cid = lines[0]
        files = set(lines[1:]) - set(GENERATED_STATE_FILES)
        commit_order.append(cid)
        commit_files[cid] = files
        commit_files_cache[cid] = files
        known_files |= files
    changed_commits: dict[str, set[str]] = {}
    acc: set[str] = set()
    for cid in commit_order:
        changed_commits[cid] = set(acc)
        acc |= commit_files[cid]
    by_base: dict[str, list[str]] = {}
    for rel in known_files:
        base = rel.rsplit("/", 1)[-1]
        by_base.setdefault(base, []).append(rel)
    unique_basenames = {
        base: paths[0] for base, paths in by_base.items() if len(paths) == 1
    }
    return changed_commits, known_files, unique_basenames


def mentioned_files(
    root: Path,
    message: str,
    known_files: set[str] | None = None,
    unique_basenames: dict[str, str] | None = None,
) -> set[str]:
    found: set[str] = set()
    known = known_files or set()
    basenames = unique_basenames or {}
    for raw in _FILE_TOKEN_RE.findall(message or ""):
        candidate = raw.strip().lstrip("./")
        if not candidate or candidate in GENERATED_STATE_FILES:
            continue
        target = (root / candidate).resolve()
        try:
            rel = target.relative_to(root).as_posix()
        except ValueError:
            continue
        if target.is_file() or rel in known:
            found.add(rel)
        elif "/" not in rel and rel in basenames:
            found.add(basenames[rel])
    return found


def stale_files(memory, items: list) -> dict[str, list[str]]:
    """Map item id -> snapshot or declared/mentioned files that changed since item."""
    drifted: dict[str, list[str]] = {}
    if not items or memory.inspecting() is not None:
        return drifted
    head = memory._run("rev-parse", "HEAD", check=False)
    if not head:
        return drifted
    unique_items = [
        item
        for item in {entry.id: entry for entry in items}.values()
        if not item.payload.get("user") and not item.payload.get("open_question")
    ]
    if not unique_items:
        return drifted
    changed_commits, known_files, unique_basenames = memory._head_file_history(head)
    item_files: dict[str, set[str]] = {}
    all_explicit: set[str] = set()
    for item in unique_items:
        declared = {
            str(name).strip()
            for name in (item.payload.get("files") or [])
            if str(name).strip()
        }
        mentioned = mentioned_files(
            memory._path, item.message, known_files, unique_basenames
        )
        explicit = declared | mentioned
        all_explicit.update(explicit)
        if declared or _GLOBAL_DECISION_RE.match((item.message or "").strip()):
            files = explicit
        else:
            files = memory._committed_files(item.id) | explicit
        if files:
            item_files[item.id] = files
    if not item_files:
        return drifted
    untracked = set()
    if all_explicit:
        untracked = set(
            memory._run(
                "ls-files", "--others", "-z", "--", *sorted(all_explicit), check=False
            ).split("\0")
        )
    wt_diff = {
        name.strip()
        for name in memory._run(
            "diff", "--name-only", head, "--", check=False
        ).splitlines()
        if name.strip()
    }
    for item_id, files in item_files.items():
        if item_id in changed_commits:
            later = changed_commits[item_id] | wt_diff
        else:
            later = {
                name.strip()
                for name in memory._run(
                    "diff", "--name-only", item_id, "--", *sorted(files), check=False
                ).splitlines()
                if name.strip()
            }
        changed = sorted(files & (later | untracked))
        if changed:
            drifted[item_id] = changed
    return drifted

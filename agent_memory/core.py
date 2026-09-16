from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from agent_memory.catalog import Catalog
from agent_memory.providers.base import EmbeddingProvider

HIDDEN_DIR = ".agent_memory"
ARCHIVE_PREFIX = ".agent_memory_archive_"
MARKER = "AGENT_MEMORY"
LOCK_FILE = "AGENT_LOCK"
INSPECT_FILE = "INSPECTING"
PAYLOAD_PREFIX = "Agent-Memory-Payload: "
OPERATION_KEY = "_agent_memory"
FIELD_SEPARATOR = "\x00"
RECORD_SEPARATOR = "\x02"
GENERATED_STATE_FILES = (".cursor/MEMORY_STATE.md", ".cursor/rules/agent-memory-state.mdc")


def _first_line(text: str, max_len: int) -> str:
    head = (text or "").strip().split("\n", 1)[0].strip()
    if len(head) > max_len:
        head = head[: max_len - 1].rstrip() + "…"
    return head

# Never snapshot VCS metadata into the memory store. The host project's
# `.git/` and any nested clone would otherwise be picked up by `git add -A`
# (agent-memory uses its own `--git-dir`, so those look like normal folders).
EXCLUDE_ENTRIES = (
    f"{HIDDEN_DIR}/",
    f"{ARCHIVE_PREFIX}*/",
    ".git/",
    ".svn/",
    ".hg/",
    ".env",
    "__pycache__/",
    ".pytest_cache/",
    *GENERATED_STATE_FILES,
)


@dataclass
class MemoryItem:
    id: str
    message: str
    payload: dict
    datetime: datetime
    score: float | None = None


def split_state(items: list[MemoryItem]) -> tuple[list[MemoryItem], list[MemoryItem]]:
    """Split a state into history (up to the last summary) and recent steps."""
    last_summary = -1
    for index, item in enumerate(items):
        if "covers" in item.payload:
            last_summary = index
    return items[: last_summary + 1], items[last_summary + 1 :]


OPEN_QUESTION_KEY = "open_question"
GOAL_KEY = "goal"


def all_open_questions(items: list[MemoryItem]) -> list[MemoryItem]:
    """Every live open-question entry, including same-text twins."""
    return [item for item in items if item.payload.get(OPEN_QUESTION_KEY) is True]


def question_key(text: str) -> str:
    """Deterministic identity for an open question (case and spacing)."""
    return " ".join((text or "").split()).casefold()


def split_open_questions(items: list[MemoryItem]) -> list[MemoryItem]:
    """Open questions for the packet: first-wins on the same question_key."""
    seen: set[str] = set()
    unique: list[MemoryItem] = []
    for item in all_open_questions(items):
        key = question_key(item.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def split_goals(items: list[MemoryItem]) -> list[MemoryItem]:
    """The original job. Marked goal=True, else the oldest user entry."""
    marked = [item for item in items if item.payload.get(GOAL_KEY) is True]
    if marked:
        return marked[:1]
    users = [item for item in items if item.payload.get("user")]
    return users[:1] if users else []


class Memory:
    def __init__(
        self,
        path: str | Path,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self._path = Path(path).resolve()
        self._git_dir = self._path / HIDDEN_DIR
        self._provider = provider
        self._search = None
        self._catalog = None
        self._last_tool_result = "Nothing yet."
        self._find_used_substring = False
        self._init()

    def _run(self, *args: str, check: bool = True) -> str:
        command = [
            "git",
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._path}",
            *args,
        ]
        result = subprocess.run(
            command,
            cwd=self._path,
            capture_output=True,
            text=True,
        )
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def _init(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        if not (self._git_dir / MARKER).exists():
            self._run("init", "-b", "main")
            (self._git_dir / MARKER).write_text("")
            self._run("config", "user.email", "agent@localhost")
            self._run("config", "user.name", "Agent Memory")

        exclude = self._git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_exclude(exclude)
        # Opening a reader must never restore another agent's working files.
        # An interrupted inspection remains explicit until inspect_done().
        self._catalog = Catalog(self._git_dir)
        self._sync_catalog()

    def _ensure_exclude(self, path: Path) -> None:
        """Keep info/exclude up to date (merge, never wipe user lines)."""
        existing: list[str] = []
        if path.exists():
            existing = [
                line.rstrip("\n")
                for line in path.read_text().splitlines()
            ]
        known = {line.strip() for line in existing if line.strip()}
        lines = list(existing)
        for entry in EXCLUDE_ENTRIES:
            if entry not in known:
                lines.append(entry)
        for entry in self._nested_repo_excludes():
            if entry not in known:
                lines.append(entry)
                known.add(entry)
        path.write_text("\n".join(lines).rstrip() + "\n")

    def _nested_repo_excludes(self) -> list[str]:
        """Paths of nested git checkouts to keep out of ``git add -A``.

        A directory containing ``.git`` is treated as a gitlink/submodule
        candidate; excluding only ``.git/`` is not enough — the whole
        nested project must be skipped or summarize/save fails.
        """
        found: list[str] = []
        root = self._path
        skip_roots = {self._git_dir.resolve()}
        for meta in root.rglob(".git"):
            try:
                resolved = meta.resolve()
            except OSError:
                continue
            if any(
                resolved == skip or skip in resolved.parents
                for skip in skip_roots
            ):
                continue
            parent = meta.parent if meta.name == ".git" else meta
            if parent.resolve() == root.resolve():
                continue
            try:
                rel = parent.relative_to(root).as_posix().rstrip("/") + "/"
            except ValueError:
                continue
            found.append(rel)
        return sorted(set(found))

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with open(self._git_dir / LOCK_FILE, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _has_commits(self) -> bool:
        return bool(self._run("rev-parse", "--verify", "HEAD", check=False))

    def _branch_name(self) -> str:
        return self._run("branch", "--show-current", check=False)

    def _home_branch(self) -> str:
        """The current branch, or the branch to return to while inspecting."""
        marker = self.inspecting()
        if marker is not None:
            return marker["branch"]
        return self._branch_name()

    def _refs(self) -> dict[str, str]:
        raw = self._run(
            "for-each-ref",
            "--format=%(refname:short)%00%(objectname)%02",
            "refs/heads",
        )
        refs = {}
        for record in raw.split(RECORD_SEPARATOR):
            if record.strip():
                name, tip = record.split(FIELD_SEPARATOR, 1)
                refs[name.strip()] = tip.strip()
        return refs

    def _resolve(self, item_id: str) -> str:
        resolved = self._run(
            "rev-parse",
            "--verify",
            f"{item_id}^{{commit}}",
            check=False,
        )
        if not resolved:
            raise ValueError(f"unknown memory id {item_id!r}")
        return resolved

    def _parse_record(self, raw: str) -> dict:
        item_id, parents, timestamp, body = raw.split(FIELD_SEPARATOR, 3)
        marker = f"\n\n{PAYLOAD_PREFIX}"
        if marker in body:
            message, payload_text = body.rsplit(marker, 1)
            payload = json.loads(payload_text.strip())
        else:
            message, payload = body, {}
        payload = dict(payload)
        operation = payload.pop(OPERATION_KEY, {})
        return {
            "id": item_id.strip(),
            "parents": parents.split(),
            "datetime": datetime.fromisoformat(timestamp),
            "message": message.strip(),
            "payload": payload,
            "kind": operation.get("operation", "put"),
            "target_id": operation.get("target"),
        }

    def _read_records(
        self,
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
        raw = self._run(*arguments)
        return [
            self._parse_record(record)
            for record in raw.split(RECORD_SEPARATOR)
            if record.strip()
        ]

    def _sync_catalog(self) -> None:
        refs = self._refs()
        known = self._catalog.known_refs()
        if refs == known:
            return
        changed_tips = [
            tip for name, tip in refs.items()
            if known.get(name) != tip
        ]
        records = self._read_records(changed_tips, list(known.values()))
        self._catalog.add_memories(records)
        self._catalog.update_refs(refs)

    def _row_item(self, row, score: float | None = None) -> MemoryItem:
        return MemoryItem(
            id=row["id"],
            message=row["message"],
            payload=json.loads(row["payload"]),
            datetime=datetime.fromtimestamp(row["committed_us"] / 1_000_000).astimezone(),
            score=score,
        )

    def _item(self, item_id: str) -> MemoryItem:
        self._sync_catalog()
        full_id = self._resolve(item_id)
        row = self._catalog.get(full_id)
        if row is None:
            self._sync_catalog()
            row = self._catalog.get(full_id)
        if row is None:
            raise ValueError(f"unknown memory id {item_id!r}")
        return self._row_item(row)

    def _searcher(self):
        if self._search is None:
            from agent_memory.search import Search

            self._search = Search(self._catalog, self._git_dir, self._provider)
        return self._search

    def _queue_embedding(self, item_id: str) -> None:
        if self._provider is None and not os.getenv("AGENT_MEMORY_PROVIDER"):
            return
        row = self._catalog.get(item_id)
        if row["kind"] != "delete":
            self._searcher().queue_document(
                row["content_hash"],
                row["document"],
            )

    def save(
        self,
        message: str,
        payload: dict | None = None,
        allow_empty: bool = True,
        summary: bool = False,
        _operation: dict | None = None,
        agent: str | None = None,
    ) -> str:
        if self.inspecting() is not None:
            raise RuntimeError(
                "cannot save while inspecting; call inspect_done first"
            )
        from agent_memory.identity import DEFAULT_AGENT, ensure_agent_payload

        payload = payload or {}
        if OPERATION_KEY in payload:
            raise ValueError(f"{OPERATION_KEY!r} is reserved")
        stored_payload = ensure_agent_payload(
            payload, agent=agent, default=DEFAULT_AGENT
        )
        if summary:
            allow_empty = True
            # Goal, latest steering, and open questions are not finished work.
            state = self.state()
            recent = [item for item in state if "covers" not in item.payload]
            goal = split_goals(state)
            goal_id = goal[0].id if goal else None
            users = [
                item
                for item in recent
                if item.payload.get("user") and item.id != goal_id
            ]
            steer_id = users[-1].id if users else None
            recent = [
                item
                for item in recent
                if item.payload.get(OPEN_QUESTION_KEY) is not True
                and item.id != goal_id
                and item.id != steer_id
            ]
            if recent:
                stored_payload["covers"] = [item.id for item in recent]
        if _operation is not None:
            stored_payload[OPERATION_KEY] = _operation
        body = f"{message}\n\n{PAYLOAD_PREFIX}{json.dumps(stored_payload)}"
        with self._lock():
            if self.inspecting() is not None:
                raise RuntimeError("cannot save while inspecting; call inspect_done first")
            # Refresh excludes each save: nested clones may appear mid-session.
            self._ensure_exclude(self._git_dir / "info" / "exclude")
            # Also untrack state already captured by older saves; keep local files.
            self._run("rm", "--cached", "--ignore-unmatch", "--", *GENERATED_STATE_FILES)
            self._run("add", "-A")
            arguments = ["commit"]
            if allow_empty:
                arguments.append("--allow-empty")
            self._run(*arguments, "-m", body)
            item_id = self._run("rev-parse", "HEAD")
            self._sync_catalog()
        self._queue_embedding(item_id)
        return item_id

    def find(
        self,
        query: str | None = None,
        *,
        ids: str | list[str] | None = None,
        text: str | None = None,
        payload: dict | None = None,
        k: int = 10,
        since: datetime | None = None,
        until: datetime | None = None,
        branch: str | None = "current",
        max_tokens: int | None = None,
        hybrid: bool = True,
        dedupe: bool = True,
        recency_weight: float = 0.05,
        include_historical: bool = False,
    ) -> list[MemoryItem]:
        if k < 1:
            raise ValueError("k must be at least 1")
        self._sync_catalog()
        branch_name = self._home_branch() if branch == "current" else branch
        wanted = None
        if ids is not None:
            wanted = [ids] if isinstance(ids, str) else ids
            # Short ids from a rendered state resolve to full ids;
            # unknown ids stay as given and simply match nothing.
            wanted = [
                self._run(
                    "rev-parse", "--verify", f"{item}^{{commit}}", check=False
                ) or item
                for item in wanted
            ]
            # An explicit id that is not reachable from the current branch
            # widens the search to every branch; nothing saved is ever out
            # of reach. Ids reachable here keep the branch-local view.
            if branch == "current" and branch_name and wanted:
                reachable = self._catalog.select(
                    branch=branch_name,
                    ids=wanted,
                    include_historical=True,
                )
                if len(reachable) < len(set(wanted)):
                    branch_name = None

        self._find_used_substring = False
        use_semantic = query is not None and (
            self._provider is not None
            or bool(os.getenv("AGENT_MEMORY_PROVIDER"))
        )
        if use_semantic:
            try:
                ranked = self._searcher().find(
                    query=query,
                    branch=branch_name,
                    ids=wanted,
                    text=text,
                    payload=payload,
                    since=since,
                    until=until,
                    k=k,
                    hybrid=hybrid,
                    dedupe=dedupe,
                    recency_weight=recency_weight,
                    include_historical=include_historical,
                )
                items = [self._row_item(row, score) for row, score in ranked]
            except Exception:
                # Embedding stack optional; missing API key / provider errors
                # fall back to local FTS.
                use_semantic = False
                self._find_used_substring = False
        if not use_semantic:
            if query is not None:
                items = self._fts_items(
                    query,
                    branch=branch_name,
                    ids=wanted,
                    text=text,
                    payload=payload,
                    since=since,
                    until=until,
                    include_historical=include_historical,
                    k=k,
                )
            else:
                rows = self._catalog.select(
                    branch=branch_name,
                    ids=wanted,
                    text=text,
                    payload=payload,
                    since=since,
                    until=until,
                    include_historical=include_historical,
                    limit=k,
                )
                items = [self._row_item(row) for row in rows]
            if dedupe:
                seen = set()
                items = [
                    item for item in items
                    if not (item.message in seen or seen.add(item.message))
                ]

        if max_tokens is not None:
            selected = []
            used = 0
            for item in items:
                tokens = max(1, (len(item.message) + 3) // 4)
                if used + tokens > max_tokens:
                    continue
                selected.append(item)
                used += tokens
            items = selected
        return items

    def _fts_items(
        self,
        query: str,
        *,
        branch: str | None,
        ids: list[str] | None,
        text: str | None,
        payload: dict | None,
        since: datetime | None,
        until: datetime | None,
        include_historical: bool,
        k: int,
    ) -> list[MemoryItem]:
        hits = self._catalog.fts(query, max(k * 4, 16))
        if not hits:
            return []
        ordered = [memory_id for memory_id, _score in hits]
        if ids is not None:
            wanted = set(ids)
            ordered = [memory_id for memory_id in ordered if memory_id in wanted]
        if not ordered:
            return []
        rows = self._catalog.select(
            branch=branch,
            ids=ordered,
            text=text,
            payload=payload,
            since=since,
            until=until,
            include_historical=include_historical,
            limit=None,
        )
        by_id = {row["id"]: row for row in rows}
        return [
            self._row_item(by_id[memory_id])
            for memory_id in ordered
            if memory_id in by_id
        ][:k]

    def state(self) -> list[MemoryItem]:
        self._sync_catalog()
        rows = self._catalog.state(self._home_branch())
        return [self._row_item(row) for row in rows]

    def history(self) -> list[MemoryItem]:
        """Every entry on the current branch, oldest first, folded or not."""
        self._sync_catalog()
        rows = self._catalog.select(branch=self._home_branch())
        return [self._row_item(row) for row in reversed(rows)]

    def export_html(self, path: str | Path | None = None) -> Path:
        """Write an explorable HTML page of the whole store and return it."""
        from agent_memory.viz import export_html

        return export_html(self, path)

    def graph_data(self) -> dict:
        """Structured graph ``{nodes, edges, kinds, branches, agents}``."""
        from agent_memory.viz import graph_data

        return graph_data(self)

    def write_canvas(
        self,
        path: str | Path | None = None,
        *,
        name: str = "agent-memory-live",
    ) -> Path:
        """Write a Cursor ``.canvas.tsx`` live dashboard and return it."""
        from agent_memory.canvas import write_canvas

        return write_canvas(self, path, name=name)

    def write_live_html(
        self,
        path: str | Path | None = None,
        *,
        force_shell: bool = False,
    ) -> Path:
        """Write / refresh the self-updating browser HTML live monitor."""
        from agent_memory.live_html import write_live_html

        return write_live_html(self, path, force_shell=force_shell)

    def graph(self) -> str:
        """An ASCII graph of every branch and entry, newest first."""
        from agent_memory.prompt import _subject

        self._sync_catalog()
        raw = self._run(
            "log", "--graph", "--all", "--format=%H%x00%D", check=False
        )
        lines = []
        for line in raw.splitlines():
            if FIELD_SEPARATOR not in line:
                lines.append(line)
                continue
            left, refs = line.split(FIELD_SEPARATOR, 1)
            prefix, sha = left[:-40], left[-40:]
            row = self._catalog.get(sha)
            text = f"{prefix}{_subject(self._row_item(row))}"
            if refs.strip():
                text += f" ({refs.strip()})"
            lines.append(text)
        return "\n".join(lines) or "Nothing saved yet."

    def branches(self) -> list[dict]:
        """Every branch with its name, tip entry and whether it is current."""
        self._sync_catalog()
        current = self._home_branch()
        result = []
        for name, tip in sorted(self._refs().items()):
            row = self._catalog.get(tip)
            result.append(
                {
                    "name": name,
                    "item": self._row_item(row),
                    "current": name == current,
                }
            )
        return result

    def save_user(self, text: str, *, agent: str | None = None) -> str | None:
        """Record the user's own words as an entry.

        Skipped while inspecting and when the newest user entry already
        holds the same text (compact mode resends the same prompt).
        """
        from agent_memory.identity import USER_AGENT

        if self.inspecting() is not None:
            return None
        # Keep durable evidence intact; prompt rendering owns the size limit.
        text = text.strip()
        if not text:
            return None
        latest = self.find(payload={"user": True}, k=1)
        if latest and latest[0].message == text:
            return None
        payload = {"user": True}
        if not split_goals(self.state()):
            payload[GOAL_KEY] = True
        return self.save(
            text,
            payload,
            agent=agent or USER_AGENT,
        )

    def auto_save(
        self,
        text: str,
        *,
        agent: str | None = None,
    ) -> str | None:
        """Runtime durability save for a finished agent turn.

        Called by hooks/wrappers — not by the model. Skips empty text,
        inspect mode, and an identical consecutive auto entry.
        """
        from agent_memory.identity import DEFAULT_AGENT, resolve_agent

        # Keep durable evidence intact; prompt rendering owns the size limit.
        text = text.strip()
        if not text or self.inspecting() is not None:
            return None
        who = resolve_agent(agent)
        state = self.state()
        if state:
            last = state[-1]
            if (
                last.message == text
                and last.payload.get("auto")
                and last.payload.get("agent", DEFAULT_AGENT) == who
            ):
                return None
        return self.save(text, {"auto": True}, agent=who)

    def summarize(self, text: str, *, agent: str | None = None) -> str:
        """Fold Recent behind one line. Model-owned compaction."""
        return self.save(text, summary=True, agent=agent)

    def open_question(
        self,
        text: str,
        depends_on: list[str] | None = None,
        *,
        agent: str | None = None,
    ) -> str:
        """Add an open question (and the notes it stands on)."""
        if self.inspecting() is not None:
            raise RuntimeError(
                "cannot save while inspecting; call inspect_done first"
            )
        text = text.strip()
        if not text:
            raise ValueError("text required; use close_questions to clear")
        key = question_key(text)
        for existing in self.open_questions():
            if question_key(existing.message) == key:
                return existing.id
        payload: dict = {OPEN_QUESTION_KEY: True}
        resolved = [self._resolve(item_id) for item_id in depends_on or []]
        if resolved:
            payload["depends_on"] = resolved
        return self.save(text, payload, allow_empty=True, agent=agent)

    def open_questions(self) -> list[MemoryItem]:
        """Live open questions on this branch."""
        return split_open_questions(self.state())

    def close_questions(self, ids: list[str] | None = None) -> list[str]:
        """Close listed open questions, or all of them. Returns closed ids."""
        raw = all_open_questions(self.state())
        current = {item.id: item for item in raw}
        if ids:
            chosen = [self._resolve(item_id) for item_id in ids]
            bad = next((target for target in chosen if target not in current), None)
            if bad:
                raise ValueError(f"not an open question: {bad[:8]}")
            keys = {question_key(current[target].message) for target in chosen}
            targets = [
                item.id for item in raw if question_key(item.message) in keys
            ]
        else:
            targets = list(current)
        closed = []
        for target in targets:
            self.save(
                "Closed open question",
                {},
                allow_empty=True,
                _operation={"operation": "delete", "target": target},
            )
            closed.append(target)
        return closed

    def _stale_ids(self, items: list[MemoryItem]) -> set[str]:
        """Ids whose snapshot files or declared dependencies changed, including unsaved edits.

        Explicit file dependencies also cover note-only saves. Inspect mode
        skips this — HEAD is the detached snapshot, not the live tree.
        """
        stale: set[str] = set()
        if not items or self.inspecting() is not None:
            return stale
        head = self._run("rev-parse", "HEAD", check=False)
        if not head:
            return stale
        for item in {entry.id: entry for entry in items}.values():
            names = self._run(
                "show", "--pretty=format:", "--name-only", item.id, check=False
            )
            files = {name for name in names.splitlines() if name.strip()}
            files.update(item.payload.get("files") or [])
            if not files:
                continue
            later = {
                name
                for name in self._run(
                    "diff", "--name-only", item.id, "--", check=False
                ).splitlines()
                if name.strip()
            }
            if files & later:
                stale.add(item.id)
        return stale

    def prompt_payload(self) -> dict:
        """Current prompt: compact injected text plus full-body sections.

        ``injected`` is what the model sees. Goal / steering / findings
        keep full bodies for the live HTML monitor.
        """
        from agent_memory.prompt import (
            RECENT_CAP,
            _goal,
            _steering,
            current_prompt,
            render,
        )

        items = self.state()
        _, recent = split_state(items)
        hide = {item.id for item in all_open_questions(items)}
        recent = [item for item in recent if item.id not in hide]
        sample = list(recent[-RECENT_CAP:])
        goal = _goal(items)
        if goal:
            sample.append(goal)
        steer = _steering(items, goal)
        if steer:
            sample.append(steer)
        sample.extend(split_open_questions(items))
        stale_ids = self._stale_ids(sample)
        payload = current_prompt(
            items, stale_ids=stale_ids
        )
        text = render(
            items,
            self.branches(),
            self.inspecting(),
            compact=True,
            stale_ids=stale_ids,
        )
        payload["injected"] = text
        return payload

    def render_state(self) -> str:
        """Compact MEMORY STATE for SDK / tests (Goal, Steering, Recent)."""
        return self.prompt_payload()["injected"]

    def related_notes(self, question: str, *, k: int | None = None, _state=None) -> list:
        """Work notes matching the question via FTS. Skips transcript and Goal."""
        from agent_memory.prompt import (
            RELATED_CAP,
            _goal,
            _handoff,
            _transcript,
        )

        question = (question or "").strip()
        if not question:
            return []
        cap = RELATED_CAP if k is None else k
        state = self.state() if _state is None else _state
        skip = {item.id for item in (_goal(state), _handoff(state)) if item}
        limit = max(cap * 3, 8)
        while True:
            items = self.find(query=question, k=limit, branch="current")
            related = [item for item in items if item.id not in skip and not _transcript(item)]
            if len(related) >= cap or len(items) < limit:
                return related[:cap]
            # Transcript hits must not crowd useful work out of the result set.
            # This grows local retrieval only, never the model-visible packet.
            limit *= 2

    def render_packet(self, question: str | None = None, *, max_chars: int = 1200) -> str:
        """Goal / Handoff / questions / Related for this question."""
        from agent_memory.prompt import _latest_user_text, _goal, _steering, _handoff, render_packet

        items = self.state()
        query = (question or "").strip() or _latest_user_text(items)
        related = self.related_notes(query, _state=items) if query else []
        # Check only candidates that can enter the bounded packet, not all history.
        goal = _goal(items)
        candidates = [entry for entry in (goal, _steering(items, goal), _handoff(items)) if entry]
        candidates += split_open_questions(items)[:8] + related
        return render_packet(
            items,
            related,
            self.branches(),
            self.inspecting(),
            query=query,
            stale_ids=self._stale_ids(candidates),
            max_chars=max_chars,
        )

    def write_state_rule(
        self,
        path: str | Path | None = None,
        *,
        packet: str | None = None,
    ) -> Path:
        """Write MEMORY STATE into an alwaysApply Cursor rule.

        Cursor reloads always-apply rules each turn. Embedding live state
        here is the reliable IDE injection path (beforeSubmitPrompt cannot
        inject context). File is meant to be gitignored.
        """
        target = (
            Path(path)
            if path
            else self._path / ".cursor" / "rules" / "agent-memory-state.mdc"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        body = (
            "---\n"
            "description: Live MEMORY STATE (auto-updated; do not edit)\n"
            "alwaysApply: true\n"
            "---\n\n"
            "# MEMORY STATE\n\n"
            f"```\n{packet if packet is not None else self.render_packet()}\n```\n"
        )
        target.write_text(body)
        return target

    def write_state_file(self, path: str | Path | None = None) -> Path:
        """Write MEMORY STATE markdown + alwaysApply rule for prompt injection."""
        target = Path(path) if path else self._path / ".cursor" / "MEMORY_STATE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        packet = self.render_packet()
        body = (
            "# MEMORY STATE\n\n"
            f"```\n{packet}\n```\n"
        )
        target.write_text(body)
        self.write_state_rule(packet=packet)
        return target

    @property
    def tools(self) -> list[dict]:
        """OpenAI-style tool schema: pass as `tools=` (plus your own)."""
        from agent_memory.tools import MEMORY_TOOLS

        return MEMORY_TOOLS

    def call(self, name_or_args, arguments: dict | None = None) -> str:
        """Run a memory tool call from the model.

            memory.call(json.loads(call.function.arguments))
            memory.call(call.function.name, json.loads(call.function.arguments))
        """
        from agent_memory.tools import call_tool

        if arguments is None:
            return call_tool(self, "memory", name_or_args)
        return call_tool(self, name_or_args, arguments)

    def messages(self, prompt: str) -> list[dict[str, str]]:
        """Build the system + user messages for one agent turn.

        The prompt is recorded as a user entry, so the user's words are
        part of memory before any work starts.
        """
        from agent_memory.prompt import MEMORY_INSTRUCTIONS

        self.save_user(prompt)
        state_text = self.render_packet(prompt)
        content = "\n\n".join(
            [
                prompt.strip(),
                f"MEMORY STATE\n{state_text}",
                f"LAST TOOL RESULT\n{self._last_tool_result}",
            ]
        )
        return [
            {"role": "system", "content": MEMORY_INSTRUCTIONS},
            {"role": "user", "content": content},
        ]

    def supersede(
        self,
        old_id: str,
        message: str,
        payload: dict | None = None,
    ) -> str:
        target = self._resolve(old_id)
        replacement = dict(payload or {})
        return self.save(
            message,
            replacement,
            allow_empty=True,
            _operation={"operation": "supersede", "target": target},
        )

    def delete(self, item_id: str) -> str:
        target = self._resolve(item_id)
        return self.save(
            f"Deleted memory {target}",
            {},
            allow_empty=True,
            _operation={"operation": "delete", "target": target},
        )

    @contextmanager
    def revisit(self, id: str) -> Iterator[MemoryItem]:
        with self._lock():
            item = self._item(id)
            branch = self._branch_name()
            if not branch:
                raise RuntimeError("cannot revisit while already revisiting")
            if self._run("status", "--porcelain", check=False):
                raise RuntimeError("save current changes before revisiting")
            self._run("checkout", "--detach", item.id)
            try:
                yield item
            finally:
                self._run("checkout", branch)

    def branch(self, base_id: str, name: str, summary: str) -> Memory:
        """Save the current line's summary, then restore the requested snapshot.

        If the target is another branch's tip, resume it; otherwise create
        the named branch. The summary remains searchable in the old history.
        """
        if self.inspecting() is not None:
            raise RuntimeError(
                "cannot branch while inspecting; call inspect_done first"
            )
        text = (summary or "").strip()
        if not text:
            raise ValueError("branch requires a summary")
        commit_id = self._resolve(base_id)
        self.save(text, summary=True)
        with self._lock():
            if self._run("status", "--porcelain", check=False):
                raise RuntimeError("save current changes before branching")
            current = self._branch_name()
            for existing, tip in sorted(self._refs().items()):
                # Jumping to another branch's tip resumes that branch;
                # the own tip still starts a new named line.
                if tip == commit_id and existing != current:
                    self._run("checkout", existing)
                    break
            else:
                self._run("checkout", "-b", name, commit_id)
            self._search = None
            self._sync_catalog()
        return self

    def inspecting(self) -> dict | None:
        """The inspect marker ({'branch': ..., 'id': ...}) or None."""
        path = self._git_dir / INSPECT_FILE
        if path.exists():
            return json.loads(path.read_text())
        return None

    def inspect(self, item_id: str) -> MemoryItem:
        """Set every working file to exactly how it was at an entry.

        The repo stays there until inspect_done() checks the branch back
        out. Inspecting again just switches snapshots.
        """
        with self._lock():
            item = self._item(item_id)
            marker = self.inspecting()
            if marker is None:
                if self._run("status", "--porcelain", check=False):
                    raise RuntimeError("save current changes before inspecting")
                marker = {"branch": self._branch_name(), "id": item.id}
            else:
                marker = {"branch": marker["branch"], "id": item.id}
            self._run("checkout", "--force", "--detach", item.id)
            (self._git_dir / INSPECT_FILE).write_text(json.dumps(marker))
        return item

    def inspect_done(self) -> str:
        """Return from inspect: check the branch and its files back out."""
        with self._lock():
            marker = self.inspecting()
            if marker is None:
                raise RuntimeError("not inspecting; nothing to return from")
            self._run("checkout", "--force", marker["branch"])
            (self._git_dir / INSPECT_FILE).unlink()
        return marker["branch"]

    def clear(self, archive: bool = True) -> None:
        self._search = None
        self._catalog.close()
        self._catalog = None
        with self._lock():
            if archive:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                destination = self._path / f"{ARCHIVE_PREFIX}{timestamp}"
                self._git_dir.rename(destination)
            else:
                shutil.rmtree(self._git_dir)
        self._init()

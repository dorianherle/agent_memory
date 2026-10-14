from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from agent_memory.catalog import Catalog
from agent_memory.git_ops import (
    ARCHIVE_PREFIX,
    FIELD_SEPARATOR,
    GENERATED_STATE_FILES,
    HIDDEN_DIR,
    OPERATION_KEY,
    PAYLOAD_PREFIX,
    RECORD_SEPARATOR,
    ensure_exclude,
    head_file_history,
    parse_record,
    read_records,
    stale_files,
)
from agent_memory.providers.base import EmbeddingProvider

MARKER = "AGENT_MEMORY"
LOCK_FILE = "AGENT_LOCK"
INSPECT_FILE = "INSPECTING"
OPEN_QUESTION_KEY = "open_question"
GOAL_KEY = "goal"

_SMALL_TALK_RE = re.compile(
    r"^(hi|hello|hey|yo|sup|howdy|good\s+(morning|afternoon|evening)|"
    r"what'?s\s+up|how\s+are\s+you|how'?s\s+it\s+going|thanks|thank\s+you|thx|ok|okay|cool|great|nice)"
    r"([!.,?\s]+(there|man|bro|team|agent|cursor|buddy|pal|folks|all|everyone|how\s+are\s+you|what'?s\s+up))*[!.,?\s]*$",
    re.IGNORECASE,
)
_STEERING_PROMPT_RE = re.compile(
    r"(?:^|[.!?\n]\s*)(?:(?:please|wait|also|actually|btw|note)\b[,\s]*)*"
    r"(?:constraint\s*:|requirement\s*:|rule\s*:|important\s*:|"
    r"always\s+|never\s+|do\s+not\s+|don'?t\s+|must\s+not\s+|"
    r"use\s+only\s+|avoid\s+|make\s+sure\s+|remember\s+to\s+|"
    r"wait\s+also\s+|"
    r"keep\s+the\s+[^\n]*\b(?:unchanged|compatible|intact|same)\b)",
    re.IGNORECASE,
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
    marked = [
        item for item in items
        if item.payload.get(GOAL_KEY) is True or item.payload.get("role") == "goal"
    ]
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
        self._commit_files_cache: dict[str, set[str]] = {}
        self._head_history_cache: tuple[str, dict[str, set[str]], set[str], dict[str, str]] | None = None
        self._init()

    def close(self) -> None:
        if self._catalog is not None:
            self._catalog.close()

    def __enter__(self) -> Memory:
        return self

    def __exit__(self, *args) -> None:
        self.close()

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
        )
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
        if check and result.returncode:
            detail = (stderr or stdout).strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return stdout.strip()

    def _init(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        if not (self._git_dir / MARKER).exists():
            self._run("init", "-b", "main")
            (self._git_dir / MARKER).write_text("")
            self._run("config", "user.email", "agent@localhost")
            self._run("config", "user.name", "Agent Memory")

        exclude = self._git_dir / "info" / "exclude"
        first_exclude = not exclude.exists()
        exclude.parent.mkdir(parents=True, exist_ok=True)
        ensure_exclude(exclude, self._path, scan_nested=first_exclude)
        self._catalog = Catalog(self._git_dir)
        self._sync_catalog()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with open(self._git_dir / LOCK_FILE, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

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
        return parse_record(raw)

    def _sync_catalog(self) -> None:
        refs = self._refs()
        known = self._catalog.known_refs()
        if refs == known:
            return
        changed_tips = [
            tip for name, tip in refs.items()
            if known.get(name) != tip
        ]
        records = read_records(self._run, changed_tips, list(known.values()))
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
            try:
                self._searcher().queue_document(
                    row["content_hash"],
                    row["document"],
                )
            except Exception:
                pass

    def save(
        self,
        message: str,
        payload: dict | None = None,
        allow_empty: bool = True,
        summary: bool = False,
        _operation: dict | None = None,
        agent: str | None = None,
        _snapshot: bool = True,
    ) -> str:
        from agent_memory.identity import DEFAULT_AGENT, ensure_agent_payload
        from agent_memory.prompt import _goal, _steering

        payload = payload or {}
        if OPERATION_KEY in payload:
            raise ValueError(f"{OPERATION_KEY!r} is reserved")
        stored_payload = ensure_agent_payload(
            payload, agent=agent, default=DEFAULT_AGENT
        )
        if summary:
            allow_empty = True
            state = self.state()
            goal = _goal(state)
            steer = _steering(state, goal)
            pinned = {entry.id for entry in (goal, steer) if entry}
            recent = [
                item
                for item in state
                if "covers" not in item.payload
                and item.payload.get(OPEN_QUESTION_KEY) is not True
                and item.id not in pinned
            ]
            if recent:
                stored_payload["covers"] = [item.id for item in recent]
        operation = dict(_operation or {})
        operation["message_format"] = "json-v1"
        stored_payload[OPERATION_KEY] = operation
        stored_message = json.dumps(message, ensure_ascii=True)
        body = f"{stored_message}\n\n{PAYLOAD_PREFIX}{json.dumps(stored_payload)}"
        with self._lock():
            if self.inspecting() is not None:
                raise RuntimeError("cannot save while inspecting; call inspect_done first")
            if _snapshot:
                ensure_exclude(self._git_dir / "info" / "exclude", self._path, scan_nested=True)
                if any((self._path / name).exists() for name in GENERATED_STATE_FILES):
                    self._run("rm", "--cached", "--ignore-unmatch", "--", *GENERATED_STATE_FILES)
                self._run("add", "-A")
            arguments = ["commit", "--cleanup=verbatim"]
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
            wanted = [
                self._run(
                    "rev-parse", "--verify", f"{item}^{{commit}}", check=False
                ) or item
                for item in wanted
            ]
            if branch == "current" and branch_name and wanted:
                reachable = self._catalog.select(
                    branch=branch_name,
                    ids=wanted,
                    include_historical=True,
                )
                if len(reachable) < len(set(wanted)):
                    branch_name = None

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
                use_semantic = False
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
        rows = self._catalog.select(
            branch=branch,
            ids=ids,
            text=text,
            payload=payload,
            since=since,
            until=until,
            include_historical=include_historical,
            fts_query=query,
            limit=max(k * 4, 16),
        )
        return [
            self._row_item(row, -float(row["fts_rank"]))
            for row in rows[:k]
        ]

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

    def save_user(
        self,
        text: str,
        *,
        agent: str | None = None,
        _has_goal: bool | None = None,
    ) -> str | None:
        """Record the user's own words as an entry."""
        from agent_memory.identity import USER_AGENT

        if self.inspecting() is not None:
            return None
        text = text.strip()
        if not text:
            return None
        self._remember_query(text)
        latest = self.find(payload={"user": True}, k=1)
        if latest and latest[0].message == text:
            return None
        has_goal = bool(split_goals(self.state())) if _has_goal is None else _has_goal
        payload = {"user": True}
        if not has_goal:
            payload[GOAL_KEY] = True
        return self.save(
            text,
            payload,
            agent=agent or USER_AGENT,
        )

    def _remember_query(self, text: str) -> None:
        cleaned = (text or "").strip()
        if cleaned and not _SMALL_TALK_RE.match(cleaned):
            try:
                (self._git_dir / "LAST_QUERY").write_text(cleaned, encoding="utf-8")
            except OSError:
                pass

    def _active_query(self, items: list[MemoryItem]) -> str:
        from agent_memory.prompt import _latest_user_text

        path = self._git_dir / "LAST_QUERY"
        if path.exists():
            try:
                stored = path.read_text(encoding="utf-8").strip()
                if stored:
                    return stored
            except OSError:
                pass
        return _latest_user_text(items)

    def ensure_goal(self, text: str, *, agent: str | None = None) -> str | None:
        """Create the initial goal from the first request, but no chat log."""
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        has_goal = bool(split_goals(self.state()))
        if has_goal and not _STEERING_PROMPT_RE.search(cleaned):
            self._remember_query(cleaned)
            return None
        return self.save_user(cleaned, agent=agent, _has_goal=has_goal)

    def auto_save(
        self,
        text: str,
        *,
        agent: str | None = None,
    ) -> str | None:
        """Runtime durability save for a finished agent turn."""
        from agent_memory.identity import DEFAULT_AGENT, resolve_agent

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
                _snapshot=False,
            )
            closed.append(target)
        return closed

    def _committed_files(self, item_id: str) -> set[str]:
        cached = self._commit_files_cache.get(item_id)
        if cached is not None:
            return cached
        names = self._run(
            "show", "--pretty=format:", "--name-only", item_id, check=False
        )
        files = {name.strip() for name in names.splitlines() if name.strip()}
        self._commit_files_cache[item_id] = files
        return files

    def _head_file_history(
        self, head: str
    ) -> tuple[dict[str, set[str]], set[str], dict[str, str]]:
        cached = self._head_history_cache
        if cached is not None and cached[0] == head:
            return cached[1], cached[2], cached[3]
        changed_commits, known_files, unique_basenames = head_file_history(
            self._run, head, self._commit_files_cache
        )
        self._head_history_cache = (
            head,
            changed_commits,
            known_files,
            unique_basenames,
        )
        return changed_commits, known_files, unique_basenames

    def _stale_files(self, items: list[MemoryItem]) -> dict[str, list[str]]:
        return stale_files(self, items)

    def _stale_ids(self, items: list[MemoryItem]) -> set[str]:
        return set(self._stale_files(items))

    def _packet_context(
        self,
        items: list[MemoryItem],
        question: str | None = None,
    ) -> tuple[str, list[MemoryItem], list[MemoryItem]]:
        from agent_memory.prompt import _goal, _handoff, _steering

        active_q = self._active_query(items)
        query = (question or "").strip() or active_q
        related = self.related_notes(query, _state=items) if query else []
        if not related and active_q and active_q != query:
            related = self.related_notes(active_q, _state=items)
        goal = _goal(items)
        handoff = _handoff(items)
        candidates = [
            entry for entry in (goal, _steering(items, goal), handoff) if entry
        ]
        candidates += split_open_questions(items)[:8] + related
        return query, related, candidates

    def prompt_payload(self) -> dict:
        """Current prompt: compact injected text plus full-body sections."""
        from agent_memory.prompt import (
            RECENT_CAP,
            current_prompt,
            render,
            render_packet,
        )

        items = self.state()
        branches = self.branches()
        inspecting = self.inspecting()
        _, recent = split_state(items)
        hide = {item.id for item in all_open_questions(items)}
        recent_slice = [item for item in recent if item.id not in hide][-RECENT_CAP:]
        query, related, packet_candidates = self._packet_context(items)
        stale_ids = self._stale_ids(recent_slice + packet_candidates)
        payload = current_prompt(items, stale_ids=stale_ids)
        payload["injected"] = render(
            items,
            branches,
            inspecting,
            compact=True,
            stale_ids=stale_ids,
        )
        payload["packet"] = render_packet(
            items,
            related,
            branches,
            inspecting,
            query=query,
            stale_ids=stale_ids,
        )
        return payload

    def render_state(self) -> str:
        """Compact MEMORY STATE for SDK / tests (Goal, Steering, Recent)."""
        return self.prompt_payload()["injected"]

    def related_notes(self, question: str, *, k: int | None = None, _state=None) -> list:
        """Work notes matching the question via FTS. Skips transcript, folded items, and Goal."""
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
        active_ids = {item.id for item in state}
        skip = {item.id for item in (_goal(state), _handoff(state)) if item} | {
            item.id for item in state if item.payload.get("handoff")
        }
        limit = max(cap * 3, 8)
        while True:
            items = self.find(query=question, k=limit, branch="current")
            related = [
                item
                for item in items
                if item.id in active_ids
                and item.id not in skip
                and not _transcript(item)
            ]
            if len(related) >= cap or len(items) < limit:
                return related[:cap]
            limit *= 2

    def render_packet(self, question: str | None = None, *, max_chars: int = 1200) -> str:
        """Goal / Handoff / questions / Related for this question."""
        from agent_memory.prompt import render_packet

        items = self.state()
        query, related, candidates = self._packet_context(items, question)
        return render_packet(
            items,
            related,
            self.branches(),
            self.inspecting(),
            query=query,
            stale_ids=self._stale_ids(candidates),
            max_chars=max_chars,
        )

    @property
    def tools(self) -> list[dict]:
        """OpenAI-style tool schema: pass as `tools=` (plus your own)."""
        from agent_memory.tools import MEMORY_TOOLS

        return MEMORY_TOOLS

    def call(self, name_or_args, arguments: dict | None = None) -> str:
        """Run a memory tool call from the model."""
        from agent_memory.tools import call_tool

        if arguments is None:
            return call_tool(self, "memory", name_or_args)
        return call_tool(self, name_or_args, arguments)

    def messages(self, prompt: str) -> list[dict[str, str]]:
        """Build the system + user messages for one agent turn."""
        from agent_memory.prompt import MEMORY_INSTRUCTIONS

        self.ensure_goal(prompt)
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
            _snapshot=False,
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
        """Save the current line's summary, then restore the requested snapshot."""
        if self.inspecting() is not None:
            raise RuntimeError(
                "cannot branch while inspecting; call inspect_done first"
            )
        text = (summary or "").strip()
        if not text:
            raise ValueError("branch requires a summary")
        commit_id = self._resolve(base_id)
        if self._run("status", "--porcelain", check=False):
            raise RuntimeError("save current changes before branching")
        self.save(text, summary=True, _snapshot=False)
        with self._lock():
            if self._run("status", "--porcelain", check=False):
                raise RuntimeError("save current changes before branching")
            current = self._branch_name()
            for existing, tip in sorted(self._refs().items()):
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
        """Set every working file to exactly how it was at an entry."""
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

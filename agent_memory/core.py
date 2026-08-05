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
PAYLOAD_PREFIX = "Agent-Memory-Payload: "
OPERATION_KEY = "_agent_memory"
FIELD_SEPARATOR = "\x00"
RECORD_SEPARATOR = "\x02"


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
        exclude.write_text(
            f"{HIDDEN_DIR}/\n{ARCHIVE_PREFIX}*/\n.env\n__pycache__/\n.pytest_cache/\n"
        )
        self._catalog = Catalog(self._git_dir)
        self._sync_catalog()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with open(self._git_dir / LOCK_FILE, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _has_commits(self) -> bool:
        return bool(self._run("rev-parse", "--verify", "HEAD", check=False))

    def _branch_name(self) -> str:
        return self._run("branch", "--show-current", check=False)

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
    ) -> str:
        payload = payload or {}
        if OPERATION_KEY in payload:
            raise ValueError(f"{OPERATION_KEY!r} is reserved")
        stored_payload = dict(payload)
        if summary:
            allow_empty = True
            recent = split_state(self.state())[1]
            if recent:
                stored_payload["covers"] = [item.id for item in recent]
        if _operation is not None:
            stored_payload[OPERATION_KEY] = _operation
        body = f"{message}\n\n{PAYLOAD_PREFIX}{json.dumps(stored_payload)}"
        with self._lock():
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
        branch_name = self._branch_name() if branch == "current" else branch
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

        if query is not None:
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

    def state(self) -> list[MemoryItem]:
        self._sync_catalog()
        rows = self._catalog.state(self._branch_name())
        return [self._row_item(row) for row in rows]

    def messages(self, prompt: str) -> list[dict[str, str]]:
        """Build the system + user messages for one agent turn."""
        from agent_memory.prompt import MEMORY_INSTRUCTIONS, render

        content = "\n\n".join(
            [
                prompt.strip(),
                f"MEMORY STATE\n{render(self.state())}",
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

    def branch(self, base_id: str, name: str) -> Memory:
        with self._lock():
            commit_id = self._resolve(base_id)
            if self._run("status", "--porcelain", check=False):
                raise RuntimeError("save current changes before branching")
            self._run("checkout", "-b", name, commit_id)
            self._search = None
            self._sync_catalog()
        return self

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

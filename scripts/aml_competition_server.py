#!/usr/bin/env python3
"""Agent Memory Leaderboard (AML) Cycle 2 competition server (/add, /search, /health).

Wraps agent_memory's SQLite WAL + FTS5 Catalog per user_id with:
- Synchronous, idempotent POST /add (with in-flight retry waiting & conflict check)
- Multi-channel RRF POST /search (SQLite FTS5 + word/CJK BM25 + options channel
  + sliding session windows + code-symbol channel + recency tie-break + neighbor context)
- Relative time annotation ([YYYY-MM-DD HH:MM] role: ... + resolved relative dates)
- Multimodal ContentPart[] support alongside Textual and Coding tracks
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_memory.catalog import Catalog  # noqa: E402

_SENTENCE_END = re.compile(r"(?<=[.!?。！？\n])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[\u4e00-\u9fff]")
_NORMALISE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_OPTION_PREFIX_RE = re.compile(r"^\s*(?:[A-Za-z0-9]{1,3}[.)、:：-]\s*|\([A-Za-z0-9]{1,3}\)\s*)")

_CODE_TOKEN_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+)"
    r"|(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
    r"|(?:[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)"
    r"|(?:[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+)"
    r"|(?:[A-Za-z0-9]*[a-z][A-Z][A-Za-z0-9]*)"
    r"|(?:[A-Z]{2,}[a-z0-9][A-Za-z0-9]*)"
    r"|(?:[A-Z][A-Z0-9_]{2,})"
    r"|(?:[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|cpp|c|h|cs|rb|php|sql|yaml|yml|json|toml|md|sh))"
)
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])|([A-Z]+)([A-Z][a-z])")
_CODE_MARKERS = (
    "```", "traceback", "exception", "error:", "stack trace",
    "pytest", "unittest", "def ", "class ", "import ", "function ",
)

_STOPWORDS = frozenset(
    "a an and are as at be by can do for from has have how in into is it its "
    "may of on or our should so such than that the their them then there these "
    "they this to was we were what when where which who will with would "
    "answer best matches memory following".split()
)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_RELATIVE_TIME_PATTERNS = (
    (re.compile(r"\b(day before yesterday)\b", re.IGNORECASE), -2, "day"),
    (re.compile(r"\b(yesterday)\b", re.IGNORECASE), -1, "day"),
    (re.compile(r"\b(today)\b", re.IGNORECASE), 0, "day"),
    (re.compile(r"\b(tomorrow)\b", re.IGNORECASE), 1, "day"),
    (
        re.compile(
            r"\b(last|previous)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        -1,
        "weekday",
    ),
    (
        re.compile(
            r"\b(this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        0,
        "weekday",
    ),
    (
        re.compile(
            r"\b(next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        1,
        "weekday",
    ),
    (re.compile(r"\b(last|previous)\s+month\b", re.IGNORECASE), -1, "month"),
    (re.compile(r"\b(this\s+month)\b", re.IGNORECASE), 0, "month"),
    (re.compile(r"\b(next\s+month)\b", re.IGNORECASE), 1, "month"),
    (re.compile(r"\b(last|previous)\s+year\b", re.IGNORECASE), -1, "year"),
    (re.compile(r"\b(this\s+year)\b", re.IGNORECASE), 0, "year"),
    (re.compile(r"\b(next\s+year)\b", re.IGNORECASE), 1, "year"),
)

_MAX_SEARCH_RESPONSE_BYTES = 24 * 1024 * 1024  # Safely below AML's 30 MiB multimodal Search cap


def _unescape_json_code(text: str) -> str:
    if "\\n" not in text and "\\t" not in text and '\\"' not in text:
        return text
    return text.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace('\\"', '"')


def tokenize(text: str, *, drop_stop: bool = True) -> list[str]:
    clean = _unescape_json_code(text)
    camel_expanded = _CAMEL_BOUNDARY_RE.sub(
        lambda m: f"{m.group(1) or m.group(3)} {m.group(2) or m.group(4)}",
        clean,
    )
    combined = f"{clean} {camel_expanded}" if camel_expanded != clean else clean
    raw_tokens = _TOKEN_RE.findall(combined.lower())
    tokens: list[str] = []
    for t in raw_tokens:
        tokens.append(t)
        if len(t) >= 5 and t.endswith("s") and not t.endswith(("ss", "us", "is", "ies")):
            stem = t[:-2] if t.endswith(("ches", "shes", "xes", "zes", "ses")) else t[:-1]
            if len(stem) >= 3:
                tokens.append(stem)
    if not drop_stop:
        return tokens
    filtered = [t for t in tokens if t not in _STOPWORDS]
    return filtered or tokens


_FILE_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


def extract_code_tokens(text: str) -> set[str]:
    clean = _unescape_json_code(text)
    return {m.group(0).casefold() for m in _CODE_TOKEN_RE.finditer(clean)}


def is_coding_track_user(user_id: str) -> bool:
    uid_lower = user_id.casefold()
    return "coding" in uid_lower or "cambench" in uid_lower or "swe" in uid_lower


def is_low_signal_coding_unit(text: str) -> bool:
    """Return True for raw tool invocations or boilerplate outputs in CAMBench Coding."""
    lower = text.lower()
    if any(
        tag in lower
        for tag in (
            "[tool_use bash]",
            "[tool_use read]",
            "[tool_use taskcreate]",
            "[tool_use taskupdate]",
            "[tool_use grep]",
            "[tool_use glob]",
            "[tool_call]",
            "[tool_result] task #",
            "[tool_result] updated task #",
            "(bash completed with no output)",
            "<persisted-output> output too large",
            "warning: running pip as the 'root' user",
            "has been updated successfully. (file state is current",
            "file created successfully at:",
            "=== test session starts ===",
        )
    ):
        return True
    if "] assistant: [thinking]" in lower:
        return True
    return False


def is_high_signal_coding_unit(text: str) -> bool:
    """Return True for structural code landmarks (file reads, diffs, exploration reports, summaries)."""
    lower = text.lower()
    return (
        "[file: " in lower
        or "diff --git " in lower
        or "## report" in lower
        or "## changes" in lower
        or "## summary" in lower
        or "## root cause" in lower
        or "here's a summary" in lower
        or "here is the comprehensive report" in lower
        or "] user: # issue" in lower
    )


def is_code_context(user_id: str, text: str) -> bool:
    if is_coding_track_user(user_id):
        return True
    lowered = text.casefold()
    return len(extract_code_tokens(text)) >= 2 or any(m in lowered for m in _CODE_MARKERS)


def _ts_to_datetime(ms: int | float | None) -> datetime | None:
    if ms is None or isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    try:
        seconds = ms / 1000.0 if abs(ms) > 1e11 else float(ms)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def format_timestamp(ms: int | float | None) -> str | None:
    dt = _ts_to_datetime(ms)
    return dt.strftime("%Y-%m-%d %H:%M") if dt is not None else None


def iso_timestamp(ms: int | float | None) -> str | None:
    dt = _ts_to_datetime(ms)
    return dt.isoformat().replace("+00:00", "Z") if dt is not None else None


def _shift_month(anchor: datetime, offset: int) -> datetime:
    month_index = anchor.year * 12 + anchor.month - 1 + offset
    return anchor.replace(year=month_index // 12, month=month_index % 12 + 1, day=1)


def _resolve_weekday(anchor: datetime, weekday_name: str, direction: int) -> datetime:
    target = _WEEKDAYS.index(weekday_name.casefold())
    if direction < 0:
        days = (anchor.weekday() - target) % 7 or 7
        return anchor - timedelta(days=days)
    if direction > 0:
        days = (target - anchor.weekday()) % 7 or 7
        return anchor + timedelta(days=days)
    return anchor + timedelta(days=target - anchor.weekday())


def temporal_annotations(text: str, timestamp_ms: int | float | None) -> list[str]:
    anchor = _ts_to_datetime(timestamp_ms)
    if anchor is None:
        return []
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()
    for pattern, offset, granularity in _RELATIVE_TIME_PATTERNS:
        for match in pattern.finditer(text):
            if granularity == "day":
                resolved = (anchor + timedelta(days=offset)).strftime("%B %d, %Y").replace(" 0", " ")
            elif granularity == "weekday":
                dt = _resolve_weekday(anchor, match.group(2), offset)
                resolved = dt.strftime("%A, %B %d, %Y").replace(" 0", " ")
            elif granularity == "month":
                resolved = _shift_month(anchor, offset).strftime("%B %Y")
            else:
                resolved = str(anchor.year + offset)
            key = (match.group(0).lower(), resolved)
            if key not in seen:
                notes.append(f'Resolved time: "{match.group(0)}" = {resolved}')
                seen.add(key)
    return notes


def validate_content_parts(content: object) -> tuple[bool, str, bool]:
    """Validate str or Multimodal ContentPart[] and return (ok, extracted_text, is_multimodal)."""
    if isinstance(content, str):
        stripped = content.strip()
        return (bool(stripped), stripped, False)
    if isinstance(content, list) and content:
        texts: list[str] = []
        has_media = False
        for part in content:
            if not isinstance(part, dict):
                return (False, "", True)
            ptype = part.get("type")
            if ptype == "text":
                t = part.get("text")
                if not isinstance(t, str) or not t.strip():
                    return (False, "", True)
                texts.append(t.strip())
            elif ptype == "image_url":
                img = part.get("image_url")
                if not isinstance(img, dict):
                    return (False, "", True)
                url = img.get("url")
                if not isinstance(url, str) or not url.startswith("data:image/"):
                    return (False, "", True)
                has_media = True
            else:
                return (False, "", True)
        joined = "\n".join(texts).strip() or ("[image]" if has_media else "")
        return (bool(joined), joined, True)
    return (False, "", False)


def _split_long(text: str, limit: int = 1800) -> list[str]:
    if len(text) <= limit:
        return [text]
    if "\n" in text:
        lines = text.splitlines(keepends=True)
        pieces: list[str] = []
        buf = ""
        for line in lines:
            if len(line) > limit:
                if buf.strip():
                    pieces.append(buf.rstrip("\n"))
                    buf = ""
                for sub in _split_single_line(line.rstrip("\n"), limit):
                    pieces.append(sub)
            elif buf and len(buf) + len(line) > limit:
                pieces.append(buf.rstrip("\n"))
                buf = line
            else:
                buf += line
        if buf.strip():
            pieces.append(buf.rstrip("\n"))
        return pieces or [text[:limit]]
    return _split_single_line(text, limit)


def _split_single_line(text: str, limit: int = 1600) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    buf = ""
    for sentence in _SENTENCE_END.split(text):
        if not sentence:
            continue
        if buf and len(buf) + len(sentence) + 1 > limit:
            pieces.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        pieces.append(buf)
    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            out.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            out.append(piece)
    return out or [text[:limit]]


def render_unit(role: str, timestamp_ms: int | float | None, text: str) -> str:
    stamp = format_timestamp(timestamp_ms)
    prefix = f"[{stamp}] {role}:" if stamp else f"{role}:"
    base = f"{prefix} {text}".strip()
    notes = temporal_annotations(text, timestamp_ms)
    return f"{base}\n" + "\n".join(notes) if notes else base


def build_units(
    messages: list[dict],
    *,
    min_chars: int = 24,
    max_chars: int = 1800,
) -> list[dict]:
    """Turn validated messages into indexable units while preserving multimodal parts."""
    cleaned: list[tuple[str, int | float | None, str, object | None]] = []
    last_tool_file_path: str | None = None
    for m in messages:
        role = str(m.get("role") or "user").strip()
        ts = m.get("timestamp")
        raw_content = m.get("content")
        ok, text, is_mm = validate_content_parts(raw_content)
        if not ok:
            continue
        if not is_mm:
            fp_match = _FILE_PATH_RE.search(text)
            if fp_match and text.startswith("[tool_use "):
                last_tool_file_path = fp_match.group(1)
                if text.startswith(("[tool_use Edit]", "[tool_use Write]")):
                    text = f"[file: {last_tool_file_path}] {_unescape_json_code(text)}"
            elif text.startswith("[tool_result]") and last_tool_file_path:
                if last_tool_file_path not in text[:200]:
                    text = f"[file: {last_tool_file_path}] {text}"
                last_tool_file_path = None
            else:
                last_tool_file_path = None
        cleaned.append((role, ts, text, raw_content if is_mm else None))

    if not cleaned:
        return []

    merged: list[tuple[str, int | float | None, str, object | None]] = []
    for role, ts, text, mm_parts in cleaned:
        if merged and mm_parts is None and merged[-1][3] is None:
            prev_role, prev_ts, prev_text, _ = merged[-1]
            same_role = prev_role == role
            too_short = len(prev_text) < min_chars
            if (same_role or too_short) and len(prev_text) + len(text) + 1 <= max_chars:
                merged[-1] = (
                    prev_role if (too_short and not same_role) else role,
                    prev_ts if prev_ts is not None else ts,
                    f"{prev_text} {text}".strip(),
                    None,
                )
                continue
        merged.append((role, ts, text, mm_parts))

    units: list[dict] = []
    for role, ts, text, mm_parts in merged:
        if mm_parts is not None:
            rendered = render_unit(role, ts, text)
            stamp = format_timestamp(ts)
            header = f"[{stamp}] {role}:" if stamp else f"{role}:"
            mm_out = [{"type": "text", "text": header}] + list(mm_parts)
            units.append(
                {
                    "role": role,
                    "timestamp": ts,
                    "created_at": iso_timestamp(ts),
                    "message": rendered,
                    "multimodal": mm_out,
                }
            )
        else:
            fp_match = _FILE_PATH_RE.search(text)
            file_tag = ""
            if text.startswith("[file: ") and "]" in text[:200]:
                file_tag = text[: text.index("]") + 1] + " "
            elif fp_match:
                file_tag = f"[file: {fp_match.group(1)}] "
            pieces = _split_long(text, max_chars)
            for idx, piece in enumerate(pieces):
                if idx > 0 and file_tag and not piece.startswith("[file: "):
                    piece = f"{file_tag}{piece}"
                units.append(
                    {
                        "role": role,
                        "timestamp": ts,
                        "created_at": iso_timestamp(ts),
                        "message": render_unit(role, ts, piece),
                        "multimodal": None,
                    }
                )
    return units


class UserMemoryPartition:
    """Isolated per-user_id store backed by agent_memory.catalog.Catalog (SQLite WAL + FTS5)."""

    def __init__(self, user_id: str, partition_dir: Path) -> None:
        self.user_id = user_id
        self.partition_dir = partition_dir
        self.partition_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.catalog = Catalog(self.partition_dir)
        with self.catalog.connection:
            self.catalog.connection.execute(
                "CREATE TABLE IF NOT EXISTS multimodal_contents ("
                "memory_id TEXT PRIMARY KEY, content_json TEXT NOT NULL)"
            )

        self.ids: list[str] = []
        self.messages: list[str] = []
        self.return_contents: list[object] = []
        self.created_at: list[str | None] = []
        self.timestamps: list[int | float | None] = []
        self.session_ids: list[str] = []
        self.session_positions: dict[str, list[int]] = defaultdict(list)
        self.has_coding_trajectories: bool = is_coding_track_user(user_id)

        self._tokens: list[list[str]] = []
        self._term_freq: list[Counter[str]] = []
        self._doc_freq: Counter[str] = Counter()
        self._postings: dict[str, list[int]] = defaultdict(list)
        self._length_sum: int = 0
        self._code_tokens: list[set[str]] = []

        # Sliding windows (4 units, step 2) per session for cross-turn context
        self._win_tokens: list[Counter[str]] = []
        self._win_doc_freq: Counter[str] = Counter()
        self._win_postings: dict[str, list[int]] = defaultdict(list)
        self._win_len_sum: int = 0
        self._win_positions: list[list[int]] = []

        # Idempotency state: request_id -> payload_sha256
        self._completed: dict[str, str] = {}
        self._inflight: dict[str, threading.Event] = {}

        self._hydrate_from_catalog()

    def _hydrate_from_catalog(self) -> None:
        with self.lock:
            mm_rows = self.catalog.connection.execute(
                "SELECT memory_id, content_json FROM multimodal_contents"
            ).fetchall()
            mm_by_id = {row["memory_id"]: json.loads(row["content_json"]) for row in mm_rows}
            rows = self.catalog.connection.execute(
                "SELECT id, message, payload FROM memories WHERE kind = 'put' ORDER BY sequence ASC"
            ).fetchall()
            seen_sessions: list[str] = []
            for row in rows:
                payload = json.loads(row["payload"])
                req_id = payload.get("request_id")
                req_hash = payload.get("payload_hash", "")
                if req_id:
                    self._completed[req_id] = req_hash
                sess_id = payload.get("session_id", "")
                if not seen_sessions or seen_sessions[-1] != sess_id:
                    seen_sessions.append(sess_id)
                mm_val = mm_by_id.get(row["id"]) or payload.get("multimodal")
                self._index_memory_in_ram(
                    memory_id=row["id"],
                    message=row["message"],
                    return_content=mm_val if mm_val is not None else row["message"],
                    created_at=payload.get("created_at"),
                    timestamp_ms=payload.get("timestamp"),
                    session_id=sess_id,
                )
            for sess_id in dict.fromkeys(seen_sessions):
                self._rebuild_session_windows(sess_id)

    def _index_memory_in_ram(
        self,
        *,
        memory_id: str,
        message: str,
        return_content: object,
        created_at: str | None,
        timestamp_ms: int | float | None,
        session_id: str,
    ) -> int:
        pos = len(self.ids)
        self.ids.append(memory_id)
        self.messages.append(message)
        self.return_contents.append(return_content)
        self.created_at.append(created_at)
        self.timestamps.append(timestamp_ms)
        self.session_ids.append(session_id)
        self.session_positions[session_id].append(pos)

        toks = tokenize(message)
        self._tokens.append(toks)
        tf = Counter(toks)
        self._term_freq.append(tf)
        for term in tf:
            self._doc_freq[term] += 1
            self._postings[term].append(pos)
        self._length_sum += len(toks)
        self._code_tokens.append(extract_code_tokens(message))
        if not self.has_coding_trajectories:
            if "[tool_use " in message or "[tool_result]" in message or "# Issue\n" in message or "[tool_call]" in message:
                self.has_coding_trajectories = True
        return pos

    def _rebuild_session_windows(self, session_id: str) -> None:
        positions = self.session_positions.get(session_id, [])
        if not positions:
            return
        win_size = 4
        step = 2
        start_idx = max(0, len(positions) - win_size)
        for idx in range(start_idx, len(positions), step):
            group = positions[idx : idx + win_size]
            if not group:
                continue
            win_id = len(self._win_positions)
            self._win_positions.append(group)
            combined_tokens: list[str] = []
            for p in group:
                combined_tokens.extend(self._tokens[p])
            wtf = Counter(combined_tokens)
            self._win_tokens.append(wtf)
            for term in wtf:
                self._win_doc_freq[term] += 1
                self._win_postings[term].append(win_id)
            self._win_len_sum += len(combined_tokens)

    def begin_request(self, request_id: str, payload_hash: str) -> tuple[str, threading.Event | None]:
        """Return ('owner' | 'duplicate' | 'conflict' | 'wait', event)."""
        with self.lock:
            if request_id in self._completed:
                if self._completed[request_id] and self._completed[request_id] != payload_hash:
                    return ("conflict", None)
                return ("duplicate", None)
            existing = self._inflight.get(request_id)
            if existing is not None:
                return ("wait", existing)
            ev = threading.Event()
            self._inflight[request_id] = ev
            return ("owner", ev)

    def finish_request(self, request_id: str, payload_hash: str, *, success: bool) -> None:
        with self.lock:
            ev = self._inflight.pop(request_id, None)
            if success:
                self._completed[request_id] = payload_hash
            if ev is not None:
                ev.set()

    def add_units(
        self,
        *,
        request_id: str,
        payload_hash: str,
        session_id: str,
        units: list[dict],
    ) -> int:
        if not units:
            return 0
        with self.lock:
            base_idx = len(self.ids)
            now_dt = datetime.now(timezone.utc)
            records: list[dict] = []
            mm_rows: list[tuple[str, str]] = []
            prev_tip = self.ids[-1] if self.ids else None

            for offset, unit in enumerate(units):
                mem_id = f"mem_{base_idx + offset}"
                ts_ms = unit["timestamp"]
                dt = _ts_to_datetime(ts_ms) or now_dt
                payload = {
                    "request_id": request_id,
                    "payload_hash": payload_hash,
                    "session_id": session_id,
                    "created_at": unit["created_at"],
                    "timestamp": ts_ms,
                }
                if unit["multimodal"] is not None:
                    mm_rows.append(
                        (mem_id, json.dumps(unit["multimodal"], ensure_ascii=False))
                    )
                records.append(
                    {
                        "id": mem_id,
                        "message": unit["message"],
                        "payload": payload,
                        "datetime": dt,
                        "kind": "put",
                        "target_id": None,
                        "parents": [prev_tip] if prev_tip else [],
                    }
                )
                prev_tip = mem_id

            # Persist synchronously into agent_memory's SQLite WAL + FTS5 Catalog
            self.catalog.add_memories(records)
            if prev_tip is not None:
                with self.catalog.connection:
                    if mm_rows:
                        self.catalog.connection.executemany(
                            "INSERT OR REPLACE INTO multimodal_contents(memory_id, content_json) VALUES (?, ?)",
                            mm_rows,
                        )
                    self.catalog.connection.execute(
                        "INSERT OR REPLACE INTO refs(name, tip_id) VALUES ('main', ?)",
                        (prev_tip,),
                    )
                    self.catalog.connection.executemany(
                        "INSERT OR IGNORE INTO ref_memories(ref_name, memory_id) VALUES ('main', ?)",
                        [(r["id"],) for r in records],
                    )

            for record, unit in zip(records, units):
                self._index_memory_in_ram(
                    memory_id=record["id"],
                    message=unit["message"],
                    return_content=unit["multimodal"] if unit["multimodal"] is not None else unit["message"],
                    created_at=unit["created_at"],
                    timestamp_ms=unit["timestamp"],
                    session_id=session_id,
                )
            self._rebuild_session_windows(session_id)
            return len(records)

    def _bm25_positions(self, query: str, depth: int, *, k1: float = 1.5, b: float = 0.75) -> list[int]:
        total = len(self.ids)
        if not total:
            return []
        avg_len = (self._length_sum / total) or 1.0
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = self._doc_freq[term]
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            for pos in postings:
                tf = self._term_freq[pos][term]
                doc_len = len(self._tokens[pos]) or 1
                denom = tf + k1 * (1.0 - b + b * doc_len / avg_len)
                scores[pos] += idf * tf * (k1 + 1.0) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [pos for pos, _ in ranked[:depth]]

    def _window_positions(self, query: str, depth: int, *, k1: float = 1.5, b: float = 0.75) -> list[int]:
        total_wins = len(self._win_positions)
        if not total_wins:
            return []
        avg_len = (self._win_len_sum / total_wins) or 1.0
        win_scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self._win_postings.get(term)
            if not postings:
                continue
            df = self._win_doc_freq[term]
            idf = math.log(1.0 + (total_wins - df + 0.5) / (df + 0.5))
            for wid in postings:
                tf = self._win_tokens[wid][term]
                doc_len = sum(self._win_tokens[wid].values()) or 1
                denom = tf + k1 * (1.0 - b + b * doc_len / avg_len)
                win_scores[wid] += idf * tf * (k1 + 1.0) / denom
        ranked_wins = sorted(win_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        seen: set[int] = set()
        out: list[int] = []
        for wid, _ in ranked_wins:
            for pos in self._win_positions[wid]:
                if pos not in seen:
                    seen.add(pos)
                    out.append(pos)
                    if len(out) >= depth:
                        return out
        return out

    def _code_positions(self, query_code_tokens: set[str], depth: int) -> list[int]:
        if not query_code_tokens:
            return []
        scores: list[tuple[int, int]] = []
        for pos, doc_codes in enumerate(self._code_tokens):
            overlap = len(query_code_tokens & doc_codes)
            if overlap > 0:
                scores.append((overlap, pos))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [pos for _, pos in scores[:depth]]

    def search(self, query_text: str, options: list[str] | None, top_k: int) -> list[dict]:
        with self.lock:
            total = len(self.ids)
            if total == 0 or top_k <= 0:
                return []

            id_to_pos = {mid: idx for idx, mid in enumerate(self.ids)}
            depth = max(200, top_k * 3)
            rrf_k = 60.0
            channels: list[tuple[list[int], float]] = []

            # Channel 1: agent_memory Catalog SQLite FTS5 BM25
            try:
                fts_hits = self.catalog.fts(query_text, depth, branch="main")
            except Exception:
                fts_hits = []
            if fts_hits:
                channels.append(
                    ([id_to_pos[mid] for mid, _ in fts_hits if mid in id_to_pos], 1.0)
                )

            # Channel 2: Fine-grained word + CJK BM25
            bm25_main = self._bm25_positions(query_text, depth)
            if bm25_main:
                channels.append((bm25_main, 1.0))

            # Channel 3: Sliding session window BM25
            win_main = self._window_positions(query_text, depth)
            if win_main:
                channels.append((win_main, 0.55))

            # Channel 4: Multiple-choice options channel (kept separate so options don't swamp query)
            cleaned_options: list[str] = []
            if options:
                cleaned_options = [
                    _OPTION_PREFIX_RE.sub("", opt).strip()
                    for opt in options
                    if isinstance(opt, str) and opt.strip()
                ]
            if cleaned_options:
                opt_query = f"{query_text}\n" + "\n".join(cleaned_options)
                bm25_opt = self._bm25_positions(opt_query, depth)
                if bm25_opt:
                    channels.append((bm25_opt, 0.65))
                try:
                    fts_opt = self.catalog.fts(opt_query, depth, branch="main")
                except Exception:
                    fts_opt = []
                if fts_opt:
                    channels.append(
                        ([id_to_pos[mid] for mid, _ in fts_opt if mid in id_to_pos], 0.55)
                    )

            # Channel 5: Code symbol & filepath exact overlap (crucial for CAMBench Coding)
            coding_mode = self.has_coding_trajectories or is_code_context(self.user_id, query_text)
            q_code = extract_code_tokens(query_text)
            if cleaned_options:
                q_code |= extract_code_tokens(" ".join(cleaned_options))
            if q_code:
                code_pos = self._code_positions(q_code, depth)
                if code_pos:
                    channels.append((code_pos, 0.95 if coding_mode else 0.5))

            fused: dict[int, float] = defaultdict(float)
            for positions, weight in channels:
                for rank, pos in enumerate(positions, start=1):
                    fused[pos] += weight / (rrf_k + rank)

            # Fallback if zero lexical overlap (e.g. purely semantic / tiny conversation):
            # return most recent memories so the Answer LLM isn't left with empty context.
            if not fused:
                recent_positions = list(range(total - 1, -1, -1))[: min(top_k, 20)]
                return self._pack_results(
                    [(pos, 1.0 / (rank + 1)) for rank, pos in enumerate(recent_positions)],
                    min(top_k, 100),
                )

            # Neighbor context boost: when turn i strongly matches, turn i+1 / i-1 in the
            # same session often holds the answer or follow-up detail.
            top_seeds = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
            neighbor_bonus: dict[int, float] = defaultdict(float)
            for pos, sc in top_seeds:
                sess = self.session_ids[pos]
                if pos + 1 < total and self.session_ids[pos + 1] == sess:
                    neighbor_bonus[pos + 1] = max(neighbor_bonus[pos + 1], sc * 0.18)
                if pos - 1 >= 0 and self.session_ids[pos - 1] == sess:
                    neighbor_bonus[pos - 1] = max(neighbor_bonus[pos - 1], sc * 0.12)
            for npos, bonus in neighbor_bonus.items():
                fused[npos] += bonus

            # Downweight low-signal tool invocations / boilerplate test dumps in Coding trajectories
            # and boost structural code landmarks (file reads, diffs, architecture reports, summaries)
            if self.has_coding_trajectories:
                for pos in list(fused.keys()):
                    msg_text = self.messages[pos]
                    if is_low_signal_coding_unit(msg_text):
                        fused[pos] *= 0.22
                    elif is_high_signal_coding_unit(msg_text):
                        fused[pos] *= 1.28

            # Mild recency tie-breaker (0.12) aligned with AML's conflict-resolution prompt
            known_ts = [
                (pos, self.timestamps[pos])
                for pos in fused
                if isinstance(self.timestamps[pos], (int, float))
            ]
            if len(known_ts) >= 2:
                vals = [float(t) for _, t in known_ts if t is not None]
                low, high = min(vals), max(vals)
                if high > low:
                    best = max(fused.values()) or 1.0
                    span = high - low
                    for pos, stamp in known_ts:
                        fused[pos] += 0.12 * best * ((float(stamp) - low) / span)

            ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))

            # Coding noise filter: CAMBench Coding injects noisy trajectories and passes top_k=30..1000.
            # Keep compact high-confidence memories on Coding tasks (Compact ReturnSize tier).
            effective_limit = min(top_k, 100)
            if self.has_coding_trajectories and ordered:
                best_score = ordered[0][1]
                ordered = [
                    (pos, sc)
                    for pos, sc in ordered
                    if sc >= best_score * 0.30
                ][: min(effective_limit, 12)]

            return self._pack_results(ordered, effective_limit)

    def _pack_results(self, ordered: list[tuple[int, float]], limit: int) -> list[dict]:
        results: list[dict] = []
        seen_keys: set[str] = set()
        used_bytes = 0
        for pos, score in ordered:
            if len(results) >= limit:
                break
            norm_key = _NORMALISE_RE.sub(" ", self.messages[pos].lower()).strip()
            if not norm_key or norm_key in seen_keys:
                continue
            content = self.return_contents[pos]
            est_bytes = len(self.messages[pos]) if isinstance(content, str) else len(json.dumps(content))
            if results and used_bytes + est_bytes > _MAX_SEARCH_RESPONSE_BYTES:
                if isinstance(content, list):
                    text_only = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    if text_only:
                        content = text_only
                        est_bytes = len(json.dumps(content))
                if used_bytes + est_bytes > _MAX_SEARCH_RESPONSE_BYTES:
                    continue
            seen_keys.add(norm_key)
            used_bytes += est_bytes
            results.append(
                {
                    "id": self.ids[pos],
                    "content": content,
                    "score": round(float(score), 6),
                    "created_at": self.created_at[pos],
                }
            )
        return results


class AMLMemoryEngine:
    """Thread-safe multi-tenant AML memory manager."""

    def __init__(
        self,
        storage_root: Path,
        *,
        api_key: str = "",
        auth_mode: str = "auto",
    ) -> None:
        self.storage_root = storage_root
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.auth_mode = auth_mode.lower().strip() or "auto"
        self._lock = threading.Lock()
        self._partitions: dict[str, UserMemoryPartition] = {}

    def _partition_dir(self, user_id: str) -> Path:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
        return self.storage_root / digest

    def partition_for(self, user_id: str, *, create: bool = True) -> UserMemoryPartition | None:
        with self._lock:
            part = self._partitions.get(user_id)
            if part is not None:
                return part
            pdir = self._partition_dir(user_id)
            if not create and not pdir.exists():
                return None
            part = UserMemoryPartition(user_id, pdir)
            self._partitions[user_id] = part
            return part

    def check_auth(self, headers: dict[str, str]) -> bool:
        mode = self.auth_mode
        secret = self.api_key
        if mode == "none" or (mode == "auto" and not secret):
            return True
        if not secret:
            return False

        auth_header = ""
        x_api_key = ""
        for k, v in headers.items():
            kl = k.lower()
            if kl == "authorization":
                auth_header = v.strip()
            elif kl == "x-api-key":
                x_api_key = v.strip()

        candidates: list[tuple[str, str]] = []
        if auth_header:
            parts = auth_header.split(None, 1)
            if len(parts) == 2:
                candidates.append((parts[0].lower(), parts[1].strip()))
            else:
                candidates.append(("raw", auth_header))
        if x_api_key:
            candidates.append(("x-api-key", x_api_key))

        for scheme, token in candidates:
            if not hmac.compare_digest(token, secret):
                continue
            if mode == "auto":
                return True
            if mode == "bearer" and scheme == "bearer":
                return True
            if mode == "token" and scheme == "token":
                return True
            if mode in ("x-api-key", "x_api_key") and scheme == "x-api-key":
                return True
        return False

    def handle_add(self, body: object) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return (400, {"detail": {"reason": "Request body must be a JSON object"}})

        request_id = body.get("request_id")
        user_id = body.get("user_id")
        session_id = body.get("session_id")
        messages = body.get("messages")

        if not isinstance(request_id, str) or not request_id.strip():
            return (422, {"detail": {"reason": "request_id must be a non-empty string"}})
        if not isinstance(user_id, str) or not user_id.strip():
            return (422, {"detail": {"reason": "user_id must be a non-empty string"}})
        if not isinstance(session_id, str) or not session_id.strip():
            return (422, {"detail": {"reason": "session_id must be a non-empty string"}})
        if not isinstance(messages, list) or not messages:
            return (422, {"detail": {"reason": "messages must be a non-empty array"}})

        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                return (422, {"detail": {"reason": f"messages[{idx}] must be an object"}})
            role = m.get("role")
            if not isinstance(role, str) or not role.strip():
                return (422, {"detail": {"reason": f"messages[{idx}].role must be a non-empty string"}})
            ts = m.get("timestamp")
            if ts is not None and (isinstance(ts, bool) or not isinstance(ts, (int, float))):
                return (422, {"detail": {"reason": f"messages[{idx}].timestamp must be a number"}})
            ok, _, _ = validate_content_parts(m.get("content"))
            if not ok:
                return (422, {"detail": {"reason": f"messages[{idx}].content is empty or invalid"}})

        canonical_payload = json.dumps(
            {"user_id": user_id, "session_id": session_id, "messages": messages},
            sort_keys=True,
            ensure_ascii=False,
        )
        payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

        part = self.partition_for(user_id, create=True)
        assert part is not None
        status, event = part.begin_request(request_id, payload_hash)
        if status == "conflict":
            return (409, {"detail": {"reason": "Conflicting payload for existing request_id"}})
        if status == "duplicate":
            return (
                200,
                {
                    "success": True,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                },
            )
        if status == "wait" and event is not None:
            event.wait(timeout=30.0)
            return (
                200,
                {
                    "success": True,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                },
            )

        try:
            units = build_units(messages)
            part.add_units(
                request_id=request_id,
                payload_hash=payload_hash,
                session_id=session_id,
                units=units,
            )
        except Exception as exc:
            part.finish_request(request_id, payload_hash, success=False)
            return (503, {"detail": {"reason": f"Write failed: {exc}"}})

        part.finish_request(request_id, payload_hash, success=True)
        return (
            200,
            {
                "success": True,
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
            },
        )

    def handle_search(self, body: object) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return (400, {"detail": {"reason": "Request body must be a JSON object"}})

        user_id = body.get("user_id")
        top_k = body.get("top_k")
        query = body.get("query")
        options = body.get("options")

        if not isinstance(user_id, str) or not user_id.strip():
            return (422, {"detail": {"reason": "user_id must be a non-empty string"}})
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            return (422, {"detail": {"reason": "top_k must be a positive integer"}})
        ok, query_text, _ = validate_content_parts(query)
        if not ok:
            return (422, {"detail": {"reason": "query must be a non-empty string or valid ContentPart[]"}})
        if options is not None:
            if not isinstance(options, list) or any(not isinstance(o, str) for o in options):
                return (422, {"detail": {"reason": "options must be a list of strings"}})

        part = self.partition_for(user_id, create=False)
        if part is None:
            return (200, {"data": []})

        data = part.search(query_text, options, top_k)
        return (200, {"data": data[:top_k]})


class AMLHTTPServer(ThreadingHTTPServer):
    """High-concurrency ThreadingHTTPServer for AML 64-256 worker bursts."""

    daemon_threads = True
    request_queue_size = 512


def make_handler(engine: AMLMemoryEngine):
    class AMLRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            if os.environ.get("AML_QUIET") == "1":
                return
            try:
                sys.stderr.write(f"[AML] {self.address_string()} - {fmt % args}\n")
                sys.stderr.flush()
            except OSError:
                pass

        def _send_json(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except OSError:
                pass

        def do_HEAD(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            self.close_connection = True
            status = 200 if path in ("/", "/health", "/add", "/search") else 404
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            except OSError:
                pass

        def do_OPTIONS(self) -> None:
            self.close_connection = True
            try:
                self.send_response(204)
                self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            except OSError:
                pass

        def do_GET(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/health", "/"):
                self._send_json(200, {"status": "ok", "service": "agent-memory-aml"})
                return
            self._send_json(404, {"detail": {"reason": f"Unknown path: {path}"}})

        def do_POST(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if path not in ("/add", "/search"):
                self._send_json(404, {"detail": {"reason": f"Unknown path: {path}"}})
                return

            headers = {k: v for k, v in self.headers.items()}
            if not engine.check_auth(headers):
                self._send_json(401, {"detail": {"reason": "Invalid or missing API key"}})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            try:
                raw = self.rfile.read(length) if length > 0 else b""
            except OSError:
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send_json(400, {"detail": {"reason": "Malformed JSON request body"}})
                return

            if path == "/add":
                status, resp = engine.handle_add(body)
            else:
                status, resp = engine.handle_search(body)
            self._send_json(status, resp)

    return AMLRequestHandler


def run_self_check() -> None:
    """Comprehensive contract + concurrency + retrieval self-check."""
    import urllib.error
    import urllib.request

    os.environ["AML_QUIET"] = "1"
    tmp_dir = Path(tempfile.mkdtemp(prefix="aml_selfcheck_"))
    try:
        engine = AMLMemoryEngine(tmp_dir, api_key="test-secret-key", auth_mode="auto")
        server = AMLHTTPServer(("127.0.0.1", 0), make_handler(engine))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"

        def request_json(
            method: str,
            path: str,
            payload: dict | None = None,
            headers: dict[str, str] | None = None,
        ) -> tuple[int, dict]:
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            hdrs = {"Content-Type": "application/json"}
            if headers:
                hdrs.update(headers)
            req = urllib.request.Request(f"{base}{path}", data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return (resp.status, json.loads(resp.read().decode("utf-8")))
            except urllib.error.HTTPError as err:
                return (err.code, json.loads(err.read().decode("utf-8")))

        # 1. Health check (no auth required)
        st, body = request_json("GET", "/health")
        assert st == 200 and body.get("status") == "ok", f"Health failed: {st} {body}"

        # 2. Auth schemes (Bearer, Token, X-Api-Key, and 401 on missing/wrong)
        sample_add = {
            "request_id": "req-auth-1",
            "user_id": "eval:run1:userA",
            "session_id": "sess-1",
            "messages": [
                {"role": "user", "timestamp": 1704067200000, "content": "Caroline moved to Sweden yesterday."}
            ],
        }
        st, _ = request_json("POST", "/add", sample_add)
        assert st == 401, f"Expected 401 without credentials, got {st}"
        st, _ = request_json("POST", "/add", sample_add, {"Authorization": "Bearer wrong"})
        assert st == 401, f"Expected 401 with wrong credentials, got {st}"

        for hdr in (
            {"Authorization": "Bearer test-secret-key"},
            {"Authorization": "Token test-secret-key"},
            {"X-Api-Key": "test-secret-key"},
        ):
            st, body = request_json("POST", "/add", sample_add, hdr)
            assert st == 200 and body["success"] is True, f"Auth failed for {hdr}: {st} {body}"
            assert body["request_id"] == "req-auth-1"
            assert body["user_id"] == "eval:run1:userA"
            assert body["session_id"] == "sess-1"

        auth = {"Authorization": "Bearer test-secret-key"}

        # 3. Idempotency: duplicate Add did not create duplicate memories, and conflict returns 409
        st, body = request_json(
            "POST",
            "/search",
            {"query": "Caroline Sweden", "user_id": "eval:run1:userA", "top_k": 10},
            auth,
        )
        assert st == 200 and len(body["data"]) == 1, f"Expected 1 unit after duplicate Add, got {body}"
        assert "2024-01-01" in body["data"][0]["content"]
        assert "December 31, 2023" in body["data"][0]["content"], "Relative date 'yesterday' not resolved!"

        conflict_add = dict(sample_add, messages=[{"role": "user", "content": "Different payload"}])
        st, _ = request_json("POST", "/add", conflict_add, auth)
        assert st == 409, f"Expected 409 on conflicting payload for same request_id, got {st}"

        # 4. Validation (422)
        st, _ = request_json("POST", "/add", {"request_id": "x", "messages": []}, auth)
        assert st == 422
        st, _ = request_json("POST", "/search", {"query": "x", "user_id": "eval:run1:userA"}, auth)
        assert st == 422

        # 5. Strict user_id isolation
        request_json(
            "POST",
            "/add",
            {
                "request_id": "req-userB",
                "user_id": "eval:run1:userB",
                "session_id": "sess-b",
                "messages": [{"role": "user", "content": "Bob repairs grand pianos in Kyoto."}],
            },
            auth,
        )
        st, body = request_json(
            "POST",
            "/search",
            {"query": "Sweden pianos Kyoto", "user_id": "eval:run1:userA", "top_k": 50},
            auth,
        )
        blob = " ".join(item["content"] for item in body["data"])
        assert "Caroline" in blob and "Kyoto" not in blob, "Cross-user leak detected!"

        # 6. Options channel for multiple-choice retrieval
        request_json(
            "POST",
            "/add",
            {
                "request_id": "req-mc",
                "user_id": "eval:run1:userMC",
                "session_id": "sess-mc",
                "messages": [
                    {"role": "user", "content": "She drinks matcha latte every morning."},
                    {"role": "user", "content": "He collects vintage fountain pens from Japan."},
                    {"role": "user", "content": "They adopted a tabby cat named Miso."},
                ],
            },
            auth,
        )
        st, body = request_json(
            "POST",
            "/search",
            {
                "query": "Which item matches the user's hobby?",
                "options": ["A. Skiing in Norway", "B. Vintage fountain pens from Japan"],
                "user_id": "eval:run1:userMC",
                "top_k": 3,
            },
            auth,
        )
        assert "fountain pens" in body["data"][0]["content"], f"Options channel failed: {body}"

        # 7. Multimodal ContentPart[] round-trip
        mm_parts = [
            {"type": "text", "text": "Diagram of the optical directional coupler layout."},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                },
            },
        ]
        st, _ = request_json(
            "POST",
            "/add",
            {
                "request_id": "req-mm",
                "user_id": "eval:run1:userMM",
                "session_id": "sess-mm",
                "messages": [{"role": "user", "timestamp": 1704067200000, "content": mm_parts}],
            },
            auth,
        )
        assert st == 200
        st, body = request_json(
            "POST",
            "/search",
            {
                "query": [{"type": "text", "text": "optical directional coupler"}],
                "user_id": "eval:run1:userMM",
                "top_k": 5,
            },
            auth,
        )
        assert st == 200 and isinstance(body["data"][0]["content"], list)

        # 8. Coding noise filter (CAMBench Coding relevant vs noisy trajectories)
        request_json(
            "POST",
            "/add",
            {
                "request_id": "req-code-rel",
                "user_id": "eval:cambench_coding:task1",
                "session_id": "traj-1",
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "Fixed TypeError in django/db/models/sql/query.py inside "
                            "Query.build_filter by unwrapping F() expressions before tuple unpacking."
                        ),
                    }
                ],
            },
            auth,
        )
        for i in range(15):
            request_json(
                "POST",
                "/add",
                {
                    "request_id": f"req-code-noise-{i}",
                    "user_id": "eval:cambench_coding:task1",
                    "session_id": f"traj-noise-{i}",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": f"Unrelated task {i}: updated CSS padding in admin template header.",
                        }
                    ],
                },
                auth,
            )
        st, body = request_json(
            "POST",
            "/search",
            {
                "query": "Bug in django/db/models/sql/query.py where Query.build_filter raises TypeError on F()",
                "user_id": "eval:cambench_coding:task1",
                "top_k": 100,
            },
            auth,
        )
        assert st == 200
        assert "Query.build_filter" in body["data"][0]["content"]
        assert len(body["data"]) <= 3, f"Noise filter failed to prune unrelated coding tasks: {len(body['data'])}"

        server.shutdown()
        print("SELF-CHECK PASSED: All 8 AML contract, isolation, multimodal, and coding checks succeeded.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="AML Cycle 2 Competition Memory Server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8788")))
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=Path(os.environ.get("AML_STORAGE_DIR", "/tmp/agent_memory_aml_store")),
    )
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", os.environ.get("AML_API_KEY", "")))
    parser.add_argument("--auth-mode", default=os.environ.get("AUTH_MODE", "auto"))
    parser.add_argument("--self-check", action="store_true", help="Run contract self-check and exit")
    args = parser.parse_args()

    if args.self_check:
        run_self_check()
        return

    engine = AMLMemoryEngine(
        args.storage_dir,
        api_key=args.api_key,
        auth_mode=args.auth_mode,
    )
    server = AMLHTTPServer((args.host, args.port), make_handler(engine))
    print(
        f"AML Memory Server listening on http://{args.host}:{args.port} "
        f"(storage={args.storage_dir}, auth_mode={args.auth_mode}, api_key_set={bool(args.api_key)})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

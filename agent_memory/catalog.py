import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from agent_memory.vector_store import VectorStoreMixin, locked, vector_bytes

__all__ = ["CATALOG_FILE", "Catalog", "canonical_document", "content_hash", "locked", "vector_bytes"]

CATALOG_FILE = "catalog.sqlite3"
SCHEMA_VERSION = 5
_FTS_STOP = frozenset(
    "a an and are as at be by can do for from has have how in into is it its "
    "may of on or our should so such than that the their them then there these "
    "they this to was we were what when where which who will with would".split()
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_document(message: str, payload: dict) -> str:
    # Transcript retention must not turn into unbounded embedding token spend.
    # Full bodies remain in memories.message and SQLite FTS; only their former
    # headline-sized representation is sent to the optional embedding provider.
    if payload.get("user") or payload.get("auto"):
        message = message.strip().split("\n", 1)[0].strip()
        if len(message) > 100:
            message = message[:99].rstrip() + "…"
    metadata = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{message}\nMetadata: {metadata}"


class Catalog(VectorStoreMixin):
    def __init__(self, git_dir: Path) -> None:
        self.path = git_dir / CATALOG_FILE
        self.lock = threading.RLock()
        self.owner = uuid.uuid4().hex
        self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._create_schema()

    def _create_schema(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            existing = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'"
            ).fetchone()
            if existing is not None:
                return
        if version != SCHEMA_VERSION:
            self.connection.execute("PRAGMA foreign_keys=OFF")
            self.connection.execute("DROP TABLE IF EXISTS memory_fts")
            names = [
                row["name"]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for name in names:
                if name != "embeddings":
                    self.connection.execute(f'DROP TABLE IF EXISTS "{name}"')
            self.connection.execute("PRAGMA foreign_keys=ON")
            for stale in self.path.parent.glob("vectors-*"):
                stale.unlink()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contents (
                id INTEGER PRIMARY KEY,
                hash TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                message TEXT NOT NULL,
                payload TEXT NOT NULL,
                committed_us INTEGER NOT NULL,
                sequence INTEGER NOT NULL UNIQUE,
                content_id INTEGER NOT NULL REFERENCES contents(id),
                kind TEXT NOT NULL DEFAULT 'put',
                target_id TEXT
            );
            CREATE TABLE IF NOT EXISTS parents (
                child_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                parent_id TEXT NOT NULL,
                PRIMARY KEY (child_id, position)
            );
            CREATE TABLE IF NOT EXISTS refs (
                name TEXT PRIMARY KEY,
                tip_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ref_memories (
                ref_name TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                PRIMARY KEY (ref_name, memory_id)
            );
            CREATE TABLE IF NOT EXISTS payload_fields (
                memory_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (memory_id, key)
            );
            CREATE INDEX IF NOT EXISTS payload_lookup
                ON payload_fields(key, value);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                memory_id UNINDEXED,
                message
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                provider TEXT NOT NULL,
                purpose TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (provider, purpose, input_hash)
            );
            CREATE TABLE IF NOT EXISTS embedding_jobs (
                provider TEXT NOT NULL,
                purpose TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                claimed_us INTEGER,
                owner TEXT,
                error TEXT,
                PRIMARY KEY (provider, purpose, input_hash)
            );
            """
        )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    @locked
    def close(self) -> None:
        self.connection.close()

    def __del__(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass

    @locked
    def known_refs(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT name, tip_id FROM refs").fetchall()
        return {row["name"]: row["tip_id"] for row in rows}

    @locked
    def add_memories(self, records: list[dict]) -> None:
        with self.connection:
            sequence = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM memories"
            ).fetchone()[0]
            for record in records:
                payload = record["payload"]
                document = canonical_document(record["message"], payload)
                digest = content_hash(document)
                self.connection.execute(
                    "INSERT OR IGNORE INTO contents(hash, text) VALUES (?, ?)",
                    (digest, document),
                )
                content_id = self.connection.execute(
                    "SELECT id FROM contents WHERE hash = ?", (digest,)
                ).fetchone()["id"]
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO memories(
                        id, message, payload, committed_us, sequence,
                        content_id, kind, target_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record["message"],
                        json.dumps(payload, sort_keys=True),
                        int(record["datetime"].timestamp() * 1_000_000),
                        sequence + 1,
                        content_id,
                        record["kind"],
                        record["target_id"],
                    ),
                )
                if not self.connection.execute(
                    "SELECT changes()"
                ).fetchone()[0]:
                    continue
                sequence += 1
                for position, parent in enumerate(record["parents"]):
                    self.connection.execute(
                        "INSERT INTO parents VALUES (?, ?, ?)",
                        (record["id"], position, parent),
                    )
                for key, value in payload.items():
                    self.connection.execute(
                        "INSERT INTO payload_fields VALUES (?, ?, ?)",
                        (record["id"], key, json.dumps(value, sort_keys=True)),
                    )
                self.connection.execute(
                    "INSERT INTO memory_fts(memory_id, message) VALUES (?, ?)",
                    (record["id"], record["message"]),
                )

    @locked
    def update_refs(self, refs: dict[str, str]) -> None:
        old = self.known_refs()
        changed = {
            name for name, tip in refs.items()
            if old.get(name) != tip
        }
        removed = set(old) - set(refs)
        with self.connection:
            for name in changed:
                self.connection.execute(
                    "INSERT OR REPLACE INTO refs(name, tip_id) VALUES (?, ?)",
                    (name, refs[name]),
                )
                self.connection.execute(
                    "DELETE FROM ref_memories WHERE ref_name = ?", (name,)
                )
                self.connection.execute(
                    """
                    WITH RECURSIVE reachable(id) AS (
                        VALUES (?)
                        UNION
                        SELECT parents.parent_id
                        FROM parents JOIN reachable ON parents.child_id = reachable.id
                    )
                    INSERT OR IGNORE INTO ref_memories(ref_name, memory_id)
                    SELECT ?, reachable.id
                    FROM reachable JOIN memories ON memories.id = reachable.id
                    """,
                    (refs[name], name),
                )
            for name in removed:
                self.connection.execute("DELETE FROM refs WHERE name = ?", (name,))
                self.connection.execute(
                    "DELETE FROM ref_memories WHERE ref_name = ?", (name,)
                )

    @locked
    def memory_count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def _scope(self, branch: str | None) -> tuple[str, list]:
        if branch is None:
            return "", []
        return (
            "JOIN ref_memories scope ON scope.memory_id = memories.id "
            "AND scope.ref_name = ? ",
            [branch],
        )

    @locked
    def select(
        self,
        *,
        branch: str | None,
        ids: list[str] | None = None,
        text: str | None = None,
        payload: dict | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        include_historical: bool = False,
        limit: int | None = None,
        fts_query: str | None = None,
        sequence_order: bool = False,
    ) -> list[sqlite3.Row]:
        if ids == []:
            return []
        scope_sql, values = self._scope(branch)
        where = ["memories.kind != 'delete'"]
        if branch is None:
            where.append(
                "EXISTS (SELECT 1 FROM ref_memories reachable "
                "WHERE reachable.memory_id = memories.id)"
            )
        if not include_historical:
            if branch is None:
                where.append(
                    """
                    EXISTS (
                        SELECT 1 FROM ref_memories target_scope
                        WHERE target_scope.memory_id = memories.id
                          AND NOT EXISTS (
                              SELECT 1 FROM memories effects
                              JOIN ref_memories effects_scope
                                ON effects_scope.memory_id = effects.id
                               AND effects_scope.ref_name = target_scope.ref_name
                              WHERE effects.target_id = memories.id
                                AND effects.kind IN ('delete', 'supersede')
                          )
                    )
                    """
                )
            else:
                values.append(branch)
                where.append(
                    """
                    NOT EXISTS (
                        SELECT 1 FROM memories effects
                        JOIN ref_memories effects_scope
                          ON effects_scope.memory_id = effects.id
                         AND effects_scope.ref_name = ?
                        WHERE effects.target_id = memories.id
                          AND effects.kind IN ('delete', 'supersede')
                    )
                    """
                )
        if ids is not None:
            where.append(f"memories.id IN ({','.join('?' for _ in ids)})")
            values.extend(ids)
        if text is not None:
            where.append("instr(lower(memories.message), lower(?)) > 0")
            values.append(text)
        if since is not None:
            where.append("memories.committed_us >= ?")
            values.append(int(since.timestamp() * 1_000_000))
        if until is not None:
            where.append("memories.committed_us <= ?")
            values.append(int(until.timestamp() * 1_000_000))
        for key, value in (payload or {}).items():
            where.append(
                "EXISTS (SELECT 1 FROM payload_fields fields "
                "WHERE fields.memory_id = memories.id "
                "AND fields.key = ? AND fields.value = ?)"
            )
            values.extend([key, json.dumps(value, sort_keys=True)])
        search_join = ""
        search_rank = ""
        ordering = "memories.committed_us DESC, memories.sequence DESC"
        if sequence_order:
            ordering = "memories.sequence DESC"
        if fts_query is not None:
            raw_words = re.findall(r"\w+", fts_query)
            expanded: list[str] = []
            for token in raw_words:
                expanded.append(token)
                if "_" in token:
                    expanded.extend(part for part in token.split("_") if part)
            words = [w for w in expanded if w.lower() not in _FTS_STOP] or expanded
            if not words:
                token = fts_query.strip()
                if not token:
                    return []
                where.append("instr(lower(memories.message), lower(?)) > 0")
                values.append(token)
                search_rank = ", 0.0 AS fts_rank "
            else:
                where.append("memory_fts MATCH ?")
                values.append(" OR ".join(f'"{word}"' for word in dict.fromkeys(words)))
                search_join = "JOIN memory_fts ON memory_fts.memory_id = memories.id "
                search_rank = ", bm25(memory_fts) AS fts_rank "
                ordering = "fts_rank ASC, " + ordering
        sql = (
            "SELECT memories.*, contents.hash AS content_hash, contents.text AS document "
            f"{search_rank}"
            "FROM memories JOIN contents ON contents.id = memories.content_id "
            f"{search_join}{scope_sql}WHERE {' AND '.join(where)} "
            f"ORDER BY {ordering}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        return self.connection.execute(sql, values).fetchall()

    @locked
    def state(self, branch: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT memories.*
            FROM memories
            JOIN ref_memories scope ON scope.memory_id = memories.id
                AND scope.ref_name = ?
            WHERE memories.kind != 'delete'
              AND memories.id NOT IN (
                  SELECT covered.value
                  FROM payload_fields fields
                  JOIN ref_memories cover_scope
                    ON cover_scope.memory_id = fields.memory_id
                   AND cover_scope.ref_name = ?
                  JOIN json_each(fields.value) covered
                  WHERE fields.key = 'covers'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM memories effects
                  JOIN ref_memories effects_scope
                    ON effects_scope.memory_id = effects.id
                   AND effects_scope.ref_name = ?
                  WHERE effects.target_id = memories.id
                    AND effects.kind IN ('delete', 'supersede')
              )
            ORDER BY memories.committed_us ASC, memories.sequence ASC
            """,
            (branch, branch, branch),
        ).fetchall()

    @locked
    def get(self, item_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT memories.*, contents.hash AS content_hash, contents.text AS document
            FROM memories JOIN contents ON contents.id = memories.content_id
            WHERE memories.id = ?
            """,
            (item_id,),
        ).fetchone()

    @locked
    def fts(self, query: str, limit: int, *, branch: str | None = None, **filters) -> list[tuple[str, float]]:
        rows = self.select(branch=branch, fts_query=query, limit=limit, **filters)
        return [(row["id"], -float(row["fts_rank"])) for row in rows]

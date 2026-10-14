import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

import numpy

CATALOG_FILE = "catalog.sqlite3"
SCHEMA_VERSION = 5
_FTS_STOP = frozenset(
    "a an and are as at be by can do for from has have how in into is it its "
    "may of on or our should so such than that the their them then there these "
    "they this to was we were what when where which who will with would".split()
)
# Rebuild the Git projection after exact line-ending decoding changes.


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


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


def vector_bytes(vector: list[float]) -> tuple[int, bytes]:
    values = numpy.asarray(vector, dtype=numpy.float64)
    if values.ndim != 1 or values.size == 0 or not numpy.isfinite(values).all():
        raise ValueError("embedding provider must return a finite nonempty one-dimensional vector")
    scale = numpy.max(numpy.abs(values))
    if not scale:
        raise ValueError("embedding provider returned a zero vector")
    # Scaling before the norm prevents overflow for large valid vectors.
    values = values / scale
    values = numpy.asarray(values / numpy.linalg.norm(values), dtype=numpy.float32)
    return len(values), values.tobytes()


class Catalog:
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
        if version != SCHEMA_VERSION:
            # The catalog is a projection of Git, so an outdated schema is
            # dropped and rebuilt from history instead of migrated in place.
            # Embeddings are kept: they cannot be recomputed from Git alone.
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
            # Rebuilding reassigns content row ids, so persisted HNSW labels
            # would point at the wrong vectors. Force a rebuild from SQLite.
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

    @locked
    def is_empty(self) -> bool:
        row = self.connection.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
        return row is None

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
            words = [w for w in raw_words if w.lower() not in _FTS_STOP] or raw_words
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
              AND NOT EXISTS (
                  SELECT 1 FROM payload_fields model_fields
                  WHERE model_fields.memory_id = memories.id
                    AND model_fields.key = 'model_event'
              )
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
    def hydrate(self, ids: list[str]) -> list[sqlite3.Row]:
        if not ids:
            return []
        rows = self.connection.execute(
            f"""
            SELECT memories.*, contents.hash AS content_hash, contents.text AS document
            FROM memories JOIN contents ON contents.id = memories.content_id
            WHERE memories.id IN ({','.join('?' for _ in ids)})
            """,
            ids,
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    @locked
    def fts(self, query: str, limit: int, *, branch: str | None = None, **filters) -> list[tuple[str, float]]:
        # Apply scope and evidence filters before LIMIT. Global top hits can
        # otherwise hide all valid matches on a smaller branch.
        rows = self.select(branch=branch, fts_query=query, limit=limit, **filters)
        return [(row["id"], -float(row["fts_rank"])) for row in rows]

    @locked
    def missing_documents(self, provider: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT contents.hash AS input_hash, contents.text
            FROM contents
            LEFT JOIN embeddings ON embeddings.provider = ?
                AND embeddings.purpose = 'document'
                AND embeddings.input_hash = contents.hash
            WHERE embeddings.input_hash IS NULL
              AND EXISTS (
                  SELECT 1 FROM memories
                  WHERE memories.content_id = contents.id
                    AND memories.kind != 'delete'
              )
            """,
            (provider,),
        ).fetchall()

    @locked
    def embedding(self, provider: str, purpose: str, digest: str) -> numpy.ndarray | None:
        row = self.connection.execute(
            """
            SELECT dimensions, vector FROM embeddings
            WHERE provider = ? AND purpose = ? AND input_hash = ?
            """,
            (provider, purpose, digest),
        ).fetchone()
        if row is None:
            return None
        return numpy.frombuffer(row["vector"], dtype=numpy.float32).copy()

    @locked
    def save_embeddings(
        self,
        provider: str,
        purpose: str,
        values: list[tuple[str, str, list[float]]],
    ) -> None:
        with self.connection:
            for digest, text, vector in values:
                dimensions, raw = vector_bytes(vector)
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO embeddings(
                        provider, purpose, input_hash, text, dimensions, vector
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (provider, purpose, digest, text, dimensions, raw),
                )
                self.connection.execute(
                    """
                    DELETE FROM embedding_jobs
                    WHERE provider = ? AND purpose = ? AND input_hash = ?
                      AND owner = ?
                    """,
                    (provider, purpose, digest, self.owner),
                )

    @locked
    def queue_embeddings(
        self, provider: str, purpose: str, inputs: list[tuple[str, str]]
    ) -> None:
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO embedding_jobs(
                    provider, purpose, input_hash, text
                ) VALUES (?, ?, ?, ?)
                """,
                [(provider, purpose, digest, text) for digest, text in inputs],
            )

    @locked
    def claim_embeddings(
        self,
        provider: str,
        purpose: str,
        inputs: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        claimed = []
        for start in range(0, len(inputs), 400):
            claimed.extend(
                self._claim_chunk(provider, purpose, inputs[start:start + 400])
            )
        return claimed

    def _claim_chunk(
        self,
        provider: str,
        purpose: str,
        inputs: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        if not inputs:
            return []
        now = time.time_ns() // 1000
        stale = now - 120_000_000
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO embedding_jobs(
                    provider, purpose, input_hash, text
                ) VALUES (?, ?, ?, ?)
                """,
                [(provider, purpose, digest, text) for digest, text in inputs],
            )
            self.connection.execute(
                """
                UPDATE embedding_jobs
                SET state = 'pending', claimed_us = NULL, owner = NULL
                WHERE provider = ? AND purpose = ?
                  AND state = 'running' AND claimed_us < ?
                """,
                (provider, purpose, stale),
            )
            digests = [digest for digest, _ in inputs]
            rows = self.connection.execute(
                f"""
                SELECT jobs.input_hash, jobs.text FROM embedding_jobs jobs
                WHERE jobs.provider = ? AND jobs.purpose = ?
                  AND jobs.state = 'pending'
                  AND jobs.input_hash IN ({','.join('?' for _ in digests)})
                  AND NOT EXISTS (
                      SELECT 1 FROM embeddings
                      WHERE embeddings.provider = jobs.provider
                        AND embeddings.purpose = jobs.purpose
                        AND embeddings.input_hash = jobs.input_hash
                  )
                """,
                [provider, purpose, *digests],
            ).fetchall()
            if rows:
                claimed = [row["input_hash"] for row in rows]
                self.connection.execute(
                    f"""
                    UPDATE embedding_jobs
                    SET state = 'running', claimed_us = ?, owner = ?
                    WHERE provider = ? AND purpose = ?
                      AND input_hash IN ({','.join('?' for _ in claimed)})
                    """,
                    [now, self.owner, provider, purpose, *claimed],
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return [(row["input_hash"], row["text"]) for row in rows]

    @locked
    def renew_embeddings(
        self,
        provider: str,
        purpose: str,
        inputs: list[tuple[str, str]],
    ) -> None:
        if not inputs:
            return
        digests = [digest for digest, _ in inputs]
        with self.connection:
            for start in range(0, len(digests), 400):
                chunk = digests[start:start + 400]
                self.connection.execute(
                    f"""
                    UPDATE embedding_jobs SET claimed_us = ?
                    WHERE provider = ? AND purpose = ? AND owner = ?
                      AND input_hash IN ({','.join('?' for _ in chunk)})
                    """,
                    [
                        time.time_ns() // 1000,
                        provider,
                        purpose,
                        self.owner,
                        *chunk,
                    ],
                )

    @locked
    def fail_embeddings(
        self,
        provider: str,
        purpose: str,
        inputs: list[tuple[str, str]],
        error: str,
    ) -> None:
        if not inputs:
            return
        digests = [digest for digest, _ in inputs]
        with self.connection:
            for start in range(0, len(digests), 400):
                chunk = digests[start:start + 400]
                self.connection.execute(
                    f"""
                    UPDATE embedding_jobs
                    SET state = 'pending', claimed_us = NULL, owner = NULL,
                        error = ?
                    WHERE provider = ? AND purpose = ?
                      AND owner = ?
                      AND input_hash IN ({','.join('?' for _ in chunk)})
                    """,
                    [error, provider, purpose, self.owner, *chunk],
                )

    @locked
    def vectors(self, provider: str) -> list[tuple[int, str, numpy.ndarray]]:
        rows = self.connection.execute(
            """
            SELECT contents.id, contents.hash, embeddings.vector
            FROM contents JOIN embeddings
              ON embeddings.input_hash = contents.hash
             AND embeddings.provider = ?
             AND embeddings.purpose = 'document'
            ORDER BY contents.id
            """,
            (provider,),
        ).fetchall()
        return [
            (
                row["id"],
                row["hash"],
                numpy.frombuffer(row["vector"], dtype=numpy.float32).copy(),
            )
            for row in rows
        ]

    @locked
    def vector_count(self, provider: str) -> int:
        return self.connection.execute(
            """
            SELECT COUNT(*) FROM embeddings
            WHERE provider = ? AND purpose = 'document'
            """,
            (provider,),
        ).fetchone()[0]

    @locked
    def vector_metadata(self, provider: str) -> dict[int, str]:
        rows = self.connection.execute(
            """
            SELECT contents.id, contents.hash
            FROM contents JOIN embeddings
              ON embeddings.input_hash = contents.hash
             AND embeddings.provider = ?
             AND embeddings.purpose = 'document'
            """,
            (provider,),
        ).fetchall()
        return {row["id"]: row["hash"] for row in rows}

    @locked
    def vector_dimensions(self, provider: str) -> int:
        row = self.connection.execute(
            """
            SELECT dimensions FROM embeddings
            WHERE provider = ? AND purpose = 'document' LIMIT 1
            """,
            (provider,),
        ).fetchone()
        return row["dimensions"] if row else 0

    @locked
    def vectors_for_ids(
        self, provider: str, labels: list[int]
    ) -> dict[int, numpy.ndarray]:
        if not labels:
            return {}
        rows = []
        for start in range(0, len(labels), 400):
            chunk = labels[start:start + 400]
            rows.extend(self.connection.execute(
                f"""
                SELECT contents.id, embeddings.vector
                FROM contents JOIN embeddings
                  ON embeddings.input_hash = contents.hash
                 AND embeddings.provider = ?
                 AND embeddings.purpose = 'document'
                WHERE contents.id IN ({','.join('?' for _ in chunk)})
                """,
                [provider, *chunk],
            ).fetchall())
        return {
            row["id"]: numpy.frombuffer(
                row["vector"], dtype=numpy.float32
            ).copy()
            for row in rows
        }

    @locked
    def memory_ids_for_content(self, hashes: list[str]) -> dict[str, list[str]]:
        if not hashes:
            return {}
        rows = []
        for start in range(0, len(hashes), 400):
            chunk = hashes[start:start + 400]
            rows.extend(self.connection.execute(
                f"""
                SELECT contents.hash, memories.id
                FROM contents JOIN memories ON memories.content_id = contents.id
                WHERE contents.hash IN ({','.join('?' for _ in chunk)})
                """,
                chunk,
            ).fetchall())
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["hash"], []).append(row["id"])
        return result

    @locked
    def document_vectors(
        self, provider: str, hashes: list[str]
    ) -> dict[str, numpy.ndarray]:
        if not hashes:
            return {}
        rows = []
        for start in range(0, len(hashes), 400):
            chunk = hashes[start:start + 400]
            rows.extend(self.connection.execute(
                f"""
                SELECT input_hash, vector FROM embeddings
                WHERE provider = ? AND purpose = 'document'
                  AND input_hash IN ({','.join('?' for _ in chunk)})
                """,
                [provider, *chunk],
            ).fetchall())
        return {
            row["input_hash"]: numpy.frombuffer(
                row["vector"], dtype=numpy.float32
            ).copy()
            for row in rows
        }

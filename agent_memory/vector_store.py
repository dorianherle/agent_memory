"""SQLite vector storage and embedding job queue mixin for Catalog."""

from __future__ import annotations

import sqlite3
import time
from functools import wraps
from typing import Iterable, TypeVar

import numpy

T = TypeVar("T")


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def _chunks(items: list[T], size: int = 400) -> Iterable[list[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _decode_vector(blob: bytes) -> numpy.ndarray:
    return numpy.frombuffer(blob, dtype=numpy.float32).copy()


def vector_bytes(vector: list[float]) -> tuple[int, bytes]:
    values = numpy.asarray(vector, dtype=numpy.float64)
    if values.ndim != 1 or values.size == 0 or not numpy.isfinite(values).all():
        raise ValueError(
            "embedding provider must return a finite nonempty one-dimensional vector"
        )
    scale = numpy.max(numpy.abs(values))
    if not scale:
        raise ValueError("embedding provider returned a zero vector")
    values = values / scale
    values = numpy.asarray(values / numpy.linalg.norm(values), dtype=numpy.float32)
    return len(values), values.tobytes()


class VectorStoreMixin:
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
    def embedding(
        self, provider: str, purpose: str, digest: str
    ) -> numpy.ndarray | None:
        row = self.connection.execute(
            """
            SELECT dimensions, vector FROM embeddings
            WHERE provider = ? AND purpose = ? AND input_hash = ?
            """,
            (provider, purpose, digest),
        ).fetchone()
        return _decode_vector(row["vector"]) if row is not None else None

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
        claimed: list[tuple[str, str]] = []
        for chunk in _chunks(inputs):
            claimed.extend(self._claim_chunk(provider, purpose, chunk))
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
            placeholders = ",".join("?" for _ in digests)
            rows = self.connection.execute(
                f"""
                SELECT jobs.input_hash, jobs.text FROM embedding_jobs jobs
                WHERE jobs.provider = ? AND jobs.purpose = ?
                  AND jobs.state = 'pending'
                  AND jobs.input_hash IN ({placeholders})
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
        now = time.time_ns() // 1000
        with self.connection:
            for chunk in _chunks(digests):
                self.connection.execute(
                    f"""
                    UPDATE embedding_jobs SET claimed_us = ?
                    WHERE provider = ? AND purpose = ? AND owner = ?
                      AND input_hash IN ({','.join('?' for _ in chunk)})
                    """,
                    [now, provider, purpose, self.owner, *chunk],
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
            for chunk in _chunks(digests):
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
            (row["id"], row["hash"], _decode_vector(row["vector"]))
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
        for chunk in _chunks(labels):
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT contents.id, embeddings.vector
                    FROM contents JOIN embeddings
                      ON embeddings.input_hash = contents.hash
                     AND embeddings.provider = ?
                     AND embeddings.purpose = 'document'
                    WHERE contents.id IN ({','.join('?' for _ in chunk)})
                    """,
                    [provider, *chunk],
                ).fetchall()
            )
        return {row["id"]: _decode_vector(row["vector"]) for row in rows}

    @locked
    def memory_ids_for_content(self, hashes: list[str]) -> dict[str, list[str]]:
        if not hashes:
            return {}
        rows = []
        for chunk in _chunks(hashes):
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT contents.hash, memories.id
                    FROM contents JOIN memories ON memories.content_id = contents.id
                    WHERE contents.hash IN ({','.join('?' for _ in chunk)})
                    """,
                    chunk,
                ).fetchall()
            )
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
        for chunk in _chunks(hashes):
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT input_hash, vector FROM embeddings
                    WHERE provider = ? AND purpose = 'document'
                      AND input_hash IN ({','.join('?' for _ in chunk)})
                    """,
                    [provider, *chunk],
                ).fetchall()
            )
        return {row["input_hash"]: _decode_vector(row["vector"]) for row in rows}

import threading
import time
from datetime import datetime
from pathlib import Path

import numpy

from agent_memory.ann import HnswIndex
from agent_memory.catalog import Catalog, content_hash
from agent_memory.providers.base import EmbeddingProvider, load_provider

WAIT_SECONDS = 600


class Search:
    def __init__(
        self,
        catalog: Catalog,
        git_dir: Path,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self.catalog = catalog
        self.provider = provider or load_provider()
        base = self.provider.fingerprint
        self.document_fingerprint = f"{base}:document:v1"
        query_config = getattr(self.provider, "query_instruction", "")
        self.query_fingerprint = f"{base}:query:v1:{query_config}"
        self.ann = HnswIndex(git_dir, self.document_fingerprint)
        self._vector_count = -1
        # Memory count last confirmed fully embedded. Read from SQLite, so
        # saves made by other processes are detected too.
        self._embedded_memories = -1

    def queue_document(self, digest: str, text: str) -> None:
        if self.catalog.embedding(
            self.document_fingerprint,
            "document",
            digest,
        ) is None:
            self.catalog.queue_embeddings(
                self.document_fingerprint,
                "document",
                [(digest, text)],
            )

    def _embed_claimed(self, claimed: list[tuple[str, str]]) -> None:
        # Renew the job lease during long provider calls so another process
        # does not reclaim the batch and embed the same documents again.
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(30):
                self.catalog.renew_embeddings(
                    self.document_fingerprint,
                    "document",
                    claimed,
                )

        keeper = threading.Thread(target=renew, daemon=True)
        keeper.start()
        try:
            vectors = self.provider.embed_documents(
                [text for _, text in claimed]
            )
            if len(vectors) != len(claimed):
                raise ValueError(
                    "embedding provider returned the wrong number of vectors"
                )
            self.catalog.save_embeddings(
                self.document_fingerprint,
                "document",
                [
                    (digest, text, vector)
                    for (digest, text), vector in zip(claimed, vectors)
                ],
            )
        except Exception as error:
            self.catalog.fail_embeddings(
                self.document_fingerprint,
                "document",
                claimed,
                str(error),
            )
            raise
        finally:
            stop.set()
            keeper.join()

    def update(self) -> int:
        computed = 0
        known = self.catalog.memory_count()
        if known != self._embedded_memories:
            deadline = time.monotonic() + WAIT_SECONDS
            while True:
                missing = self.catalog.missing_documents(
                    self.document_fingerprint
                )
                if not missing:
                    self._embedded_memories = known
                    break
                inputs = [
                    (row["input_hash"], row["text"]) for row in missing
                ]
                claimed = self.catalog.claim_embeddings(
                    self.document_fingerprint,
                    "document",
                    inputs,
                )
                if claimed:
                    self._embed_claimed(claimed)
                    computed += len(claimed)
                elif time.monotonic() > deadline:
                    raise TimeoutError(
                        "timed out waiting for document embeddings"
                    )
                else:
                    # Another process owns the jobs; wait, then retry the
                    # claim so a crashed owner's stale lease gets taken over.
                    time.sleep(0.05)
        self._update_ann()
        return computed

    def _update_ann(self) -> None:
        count = self.catalog.vector_count(self.document_fingerprint)
        if count != self._vector_count:
            labels = self.catalog.vector_metadata(self.document_fingerprint)
            dimensions = self.catalog.vector_dimensions(self.document_fingerprint)
            self.ann.update(
                labels,
                dimensions,
                lambda missing: self.catalog.vectors_for_ids(
                    self.document_fingerprint,
                    missing,
                ),
            )
            self._vector_count = count

    def _query_vector(self, query: str) -> numpy.ndarray:
        digest = content_hash(query)
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            cached = self.catalog.embedding(
                self.query_fingerprint,
                "query",
                digest,
            )
            if cached is not None:
                return cached
            claimed = self.catalog.claim_embeddings(
                self.query_fingerprint,
                "query",
                [(digest, query)],
            )
            if claimed:
                try:
                    vector = self.provider.embed_query(query)
                    self.catalog.save_embeddings(
                        self.query_fingerprint,
                        "query",
                        [(digest, query, vector)],
                    )
                except Exception as error:
                    self.catalog.fail_embeddings(
                        self.query_fingerprint,
                        "query",
                        claimed,
                        str(error),
                    )
                    raise
            elif time.monotonic() > deadline:
                raise TimeoutError("timed out waiting for query embedding")
            else:
                time.sleep(0.05)

    def _select_candidates(self, candidate_ids: list[str], **filters):
        rows = []
        for start in range(0, len(candidate_ids), 400):
            rows.extend(
                self.catalog.select(
                    ids=candidate_ids[start:start + 400],
                    **filters,
                )
            )
        return rows

    def find(
        self,
        *,
        query: str,
        branch: str | None,
        ids: list[str] | None,
        text: str | None,
        payload: dict | None,
        since: datetime | None,
        until: datetime | None,
        k: int,
        hybrid: bool,
        dedupe: bool,
        recency_weight: float,
        include_historical: bool,
    ) -> list[tuple[object, float]]:
        self.update()
        query_vector = self._query_vector(query)
        total = max(1, self._vector_count)
        pool = min(total, max(50, k * 10))
        lexical = dict(self.catalog.fts(query, max(50, k * 10))) if hybrid else {}

        filtered = any([
            ids is not None,
            text is not None,
            payload,
            since is not None,
            until is not None,
        ])
        if filtered:
            rows = self.catalog.select(
                branch=branch,
                ids=ids,
                text=text,
                payload=payload,
                since=since,
                until=until,
                include_historical=include_historical,
            )
        else:
            rows = []
            while True:
                hashes = self.ann.search(query_vector, pool)
                content_ids = self.catalog.memory_ids_for_content(hashes)
                candidate_ids = [
                    item_id
                    for digest in hashes
                    for item_id in content_ids.get(digest, [])
                ]
                candidate_ids.extend(lexical)
                if not candidate_ids:
                    return []
                rows = self._select_candidates(
                    list(dict.fromkeys(candidate_ids)),
                    branch=branch,
                    text=None,
                    payload=None,
                    since=None,
                    until=None,
                    include_historical=include_historical,
                )
                # Branch scoping can discard candidates, so keep widening the
                # pool until enough survive or every vector was considered.
                usable = (
                    len({row["content_hash"] for row in rows})
                    if dedupe else len(rows)
                )
                if usable >= k or pool >= total:
                    break
                pool = min(total, pool * 2)

        if dedupe:
            seen = set()
            eligible = []
            for row in rows:
                if row["content_hash"] not in seen:
                    seen.add(row["content_hash"])
                    eligible.append(row)
        else:
            eligible = rows

        hashes = [row["content_hash"] for row in eligible]
        vectors = self.catalog.document_vectors(
            self.document_fingerprint,
            hashes,
        )
        if not eligible:
            return []
        times = [row["committed_us"] for row in eligible]
        oldest, newest = min(times), max(times)
        span = max(1, newest - oldest)
        scored = []
        for row in eligible:
            vector = vectors.get(row["content_hash"])
            semantic = float(vector @ query_vector) if vector is not None else 0.0
            lexical_score = 1.0 if row["id"] in lexical else 0.0
            recency = (row["committed_us"] - oldest) / span
            score = semantic + (0.1 * lexical_score if hybrid else 0.0)
            score += recency_weight * recency
            scored.append((row, score))
        scored.sort(key=lambda pair: (pair[1], pair[0]["id"]), reverse=True)
        return scored[:k]

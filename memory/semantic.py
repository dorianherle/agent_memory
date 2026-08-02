"""Semantic search over the Git memory: "have we tried this before?".

A derived index. It holds nothing that cannot be rebuilt by replaying the log,
so Git stays the only source of truth. Delete the file and it comes back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from google import genai

from memory.git_memory import PARK_MESSAGE, CommitEntry, Memory, Tag

# Anchored to this file, not the current directory, so the controller can be
# launched from anywhere. Real env vars still win: load_dotenv never overrides.
load_dotenv(Path(__file__).parent.parent / ".env")

MODEL = "gemini-embedding-001"
DIMS = 1536  # the model offers 3072; halving it costs almost no quality
INDEX_FILE = "semantic_index.npz"
BATCH = 100  # the API rejects any request carrying more texts than this

# Stored beside the vectors: change either constant and the old ones are junk.
_STAMP = f"{MODEL}:{DIMS}"


def _embed(texts: list[str], task: str) -> np.ndarray:
    """Gemini wants to know whether it is embedding a query or a document."""
    # Held in a local: a temporary Client is collected mid-request and closes
    # the connection under itself.
    client = genai.Client()
    res = client.models.embed_content(
        model=MODEL, contents=texts,
        config={"task_type": task, "output_dimensionality": DIMS})

    vectors = np.array([e.values for e in res.embeddings], dtype=np.float32)
    # Truncated vectors come back well short of unit length, so cosine needs this.
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class Search:
    """Vector search over commit messages, kept in step with the log."""

    def __init__(self, memory: Memory) -> None:
        self.memory = memory
        self.path = memory.git_dir / INDEX_FILE
        self.ids: list[str] = []
        self.vectors = np.zeros((0, DIMS), dtype=np.float32)

        if self.path.exists():
            saved = np.load(self.path)
            if str(saved["stamp"]) == _STAMP:
                self.ids = list(saved["ids"])
                self.vectors = saved["vectors"]

    def _catch_up(self, entries: list[CommitEntry]) -> int:
        """Embed every commit the index has not seen. Returns how many."""
        known = set(self.ids)
        # Park commits are bookkeeping, not ideas. Embedding them costs money
        # and pushes real matches down the ranking.
        new = [e for e in entries if e.id not in known and e.message != PARK_MESSAGE]

        # Saved per batch, so a long first build that dies partway resumes
        # where it stopped instead of paying for the whole thing again.
        for start in range(0, len(new), BATCH):
            batch = new[start:start + BATCH]
            fresh = _embed([e.message for e in batch], "RETRIEVAL_DOCUMENT")
            self.vectors = np.vstack([self.vectors, fresh])
            self.ids += [e.id for e in batch]
            np.savez(self.path, stamp=_STAMP, ids=np.array(self.ids), vectors=self.vectors)
        return len(new)

    def update(self) -> int:
        """Bring the index level with the log without searching."""
        return self._catch_up(self.memory.history(all_branches=True, limit=0))

    def find(self, query: str, k: int = 5,
             tag: Tag | str | None = None) -> list[tuple[CommitEntry, float]]:
        """The commits closest to a question, newest branches included.

        Every branch is searched, because dead ends are the point of asking.
        Pass tag=Tag.FAIL to ask it only of the failures.
        """
        entries = self.memory.history(all_branches=True, limit=0)
        self._catch_up(entries)
        if not self.ids:
            return []

        scores = self.vectors @ _embed([query], "RETRIEVAL_QUERY")[0]
        by_id = {e.id: e for e in entries}

        hits = []
        for commit_id, score in sorted(zip(self.ids, scores), key=lambda pair: -pair[1]):
            entry = by_id.get(commit_id)  # None once a branch holding it is gone
            if entry is None or (tag and entry.tag != Tag(tag)):
                continue
            hits.append((entry, float(score)))
            if len(hits) == k:
                break
        return hits

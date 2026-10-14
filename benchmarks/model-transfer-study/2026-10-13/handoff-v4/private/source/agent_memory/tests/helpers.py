import os
import tempfile
import unittest
from pathlib import Path

from agent_memory import Memory


class MemoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Live HTML auto-starts a Finder reveal helper; keep unit tests offline.
        os.environ.setdefault("AGENT_MEMORY_NO_REVEAL", "1")
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.memory = Memory(self.path)
        self.file = self.path / "work.txt"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save(self, message: str, payload: dict | None = None) -> str:
        self.file.write_text(message)
        return self.memory.save(message, payload or {})


class FakeProvider:
    fingerprint = "fake:1"

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0
        self.document_texts = 0

    def vector(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [
            float(lowered.count("matrix")),
            float(lowered.count("document")),
            float(lowered.count("branch")),
        ]
        return vector if any(vector) else [0.0, 0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        self.document_texts += len(texts)
        return [self.vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self.vector(text)

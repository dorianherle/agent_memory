import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.tests.helpers import FakeProvider, MemoryTestCase


try:
    import hnswlib  # noqa: F401
    HAS_HNSWLIB = True
except ImportError:
    HAS_HNSWLIB = False


class OtherFakeProvider(FakeProvider):
    fingerprint = "fake:2"


class BrokenProvider(FakeProvider):
    def embed_documents(self, texts):
        return []


class InstructedProvider(FakeProvider):
    fingerprint = "fake:instruction"

    def __init__(self, instruction):
        super().__init__()
        self.query_instruction = instruction


class TestSemanticSearch(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.matrix = self.save("Optimize matrix multiplication")
        self.document = self.save("Rewrite document parser")

    def find(self, query, provider, **kwargs):
        self.memory._provider = provider
        self.memory._search = None
        return self.memory.find(query, **kwargs)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_query_ranks_messages(self):
        hits = self.find("document", FakeProvider())
        self.assertEqual(hits[0].id, self.document)

    def test_missing_provider_falls_back_to_text(self):
        with patch.dict(os.environ, {"AGENT_MEMORY_PROVIDER": ""}, clear=False):
            hits = self.memory.find("matrix")
        self.assertEqual([item.id for item in hits], [self.matrix])

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_document_embeddings_are_incremental(self):
        provider = FakeProvider()
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 1)

        self.save("Create branch")
        self.find("branch", provider)
        self.assertEqual(provider.document_calls, 2)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_document_embeddings_persist_across_instances(self):
        first = FakeProvider()
        self.find("matrix", first)
        second = FakeProvider()
        memory = Memory(self.path, provider=second)
        memory.find("matrix")
        self.assertEqual(second.document_calls, 0)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_schema_rebuild_keeps_embeddings(self):
        self.find("matrix", FakeProvider())
        catalog_path = self.memory._catalog.path
        self.memory._catalog.close()
        connection = sqlite3.connect(catalog_path)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()

        provider = FakeProvider()
        memory = Memory(self.path, provider=provider)
        hits = memory.find("matrix")

        self.assertEqual(hits[0].id, self.matrix)
        self.assertEqual(provider.document_calls, 0)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_query_embedding_is_computed_once(self):
        provider = FakeProvider()
        self.memory._provider = provider
        self.memory._search = None
        self.memory.find("matrix")
        self.memory.find("matrix")
        self.assertEqual(provider.query_calls, 1)

        second = FakeProvider()
        memory = Memory(self.path, provider=second)
        memory.find("matrix")
        self.assertEqual(second.query_calls, 0)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_duplicate_content_is_embedded_once(self):
        self.memory.save("Same memory", allow_empty=True)
        self.memory.save("Same memory", allow_empty=True)
        provider = FakeProvider()
        self.find("same", provider)
        self.assertEqual(provider.document_texts, 3)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_provider_change_has_separate_cache(self):
        self.find("matrix", FakeProvider())
        provider = OtherFakeProvider()
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 1)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_query_instruction_does_not_recompute_documents(self):
        self.find("matrix", InstructedProvider("first"))
        provider = InstructedProvider("second")
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 0)
        self.assertEqual(provider.query_calls, 1)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_ann_rebuild_uses_cached_vectors(self):
        provider = FakeProvider()
        self.find("matrix", provider)
        for path in Path(self.memory._git_dir).glob("vectors-*.hnsw"):
            path.unlink()
        second = FakeProvider()
        memory = Memory(self.path, provider=second)
        hits = memory.find("matrix")
        self.assertEqual(hits[0].id, self.matrix)
        self.assertEqual(second.document_calls, 0)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_ann_only_loads_new_vectors(self):
        provider = FakeProvider()
        self.memory._provider = provider
        self.memory._search = None
        self.memory.find("matrix")
        with patch.object(
            self.memory._catalog,
            "vectors_for_ids",
            wraps=self.memory._catalog.vectors_for_ids,
        ) as load:
            self.memory.find("document")
        load.assert_not_called()

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_bad_provider_result(self):
        # Provider bugs must not break find for the agent — substring fallback.
        hits = self.find("matrix", BrokenProvider())
        self.assertEqual([item.id for item in hits], [self.matrix])


@unittest.skipUnless(os.getenv("GEMINI_API_KEY"), "needs GEMINI_API_KEY")
class TestGeminiProvider(unittest.TestCase):
    def test_real_embedding(self):
        from agent_memory.providers.gemini import GeminiProvider

        vector = GeminiProvider(dimensions=128).embed_query("agent memory")
        self.assertEqual(len(vector), 128)

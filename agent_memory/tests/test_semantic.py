import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.tests.helpers import FakeProvider, MemoryTestCase


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
        self.memory._search = None
        with patch("agent_memory.search.load_provider", return_value=provider):
            return self.memory.find(query, **kwargs)

    def test_query_ranks_messages(self):
        hits = self.find("document", FakeProvider())
        self.assertEqual(hits[0].id, self.document)

    def test_missing_provider_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AGENT_MEMORY_PROVIDER"):
                self.memory.find("matrix")

    def test_document_embeddings_are_incremental(self):
        provider = FakeProvider()
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 1)

        self.save("Create branch")
        self.find("branch", provider)
        self.assertEqual(provider.document_calls, 2)

    def test_document_embeddings_persist_across_instances(self):
        first = FakeProvider()
        self.find("matrix", first)
        second = FakeProvider()
        memory = Memory(self.path)
        with patch("agent_memory.search.load_provider", return_value=second):
            memory.find("matrix")
        self.assertEqual(second.document_calls, 0)

    def test_schema_rebuild_keeps_embeddings(self):
        self.find("matrix", FakeProvider())
        catalog_path = self.memory._catalog.path
        self.memory._catalog.close()
        connection = sqlite3.connect(catalog_path)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()

        provider = FakeProvider()
        memory = Memory(self.path)
        with patch("agent_memory.search.load_provider", return_value=provider):
            hits = memory.find("matrix")

        self.assertEqual(hits[0].id, self.matrix)
        self.assertEqual(provider.document_calls, 0)

    def test_query_embedding_is_computed_once(self):
        provider = FakeProvider()
        self.memory._search = None
        with patch("agent_memory.search.load_provider", return_value=provider):
            self.memory.find("matrix")
            self.memory.find("matrix")
        self.assertEqual(provider.query_calls, 1)

        second = FakeProvider()
        memory = Memory(self.path)
        with patch("agent_memory.search.load_provider", return_value=second):
            memory.find("matrix")
        self.assertEqual(second.query_calls, 0)

    def test_duplicate_content_is_embedded_once(self):
        self.memory.save("Same memory", allow_empty=True)
        self.memory.save("Same memory", allow_empty=True)
        provider = FakeProvider()
        self.find("same", provider)
        self.assertEqual(provider.document_texts, 3)

    def test_provider_change_has_separate_cache(self):
        self.find("matrix", FakeProvider())
        provider = OtherFakeProvider()
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 1)

    def test_query_instruction_does_not_recompute_documents(self):
        self.find("matrix", InstructedProvider("first"))
        provider = InstructedProvider("second")
        self.find("matrix", provider)
        self.assertEqual(provider.document_calls, 0)
        self.assertEqual(provider.query_calls, 1)

    def test_ann_rebuild_uses_cached_vectors(self):
        provider = FakeProvider()
        self.find("matrix", provider)
        for path in Path(self.memory._git_dir).glob("vectors-*.hnsw"):
            path.unlink()
        second = FakeProvider()
        memory = Memory(self.path)
        with patch("agent_memory.search.load_provider", return_value=second):
            hits = memory.find("matrix")
        self.assertEqual(hits[0].id, self.matrix)
        self.assertEqual(second.document_calls, 0)

    def test_ann_only_loads_new_vectors(self):
        provider = FakeProvider()
        self.memory._search = None
        with patch("agent_memory.search.load_provider", return_value=provider):
            self.memory.find("matrix")
            search = self.memory._search
            with patch.object(
                self.memory._catalog,
                "vectors_for_ids",
                wraps=self.memory._catalog.vectors_for_ids,
            ) as load:
                self.memory.find("document")
            load.assert_not_called()

    def test_bad_provider_result(self):
        with self.assertRaisesRegex(ValueError, "wrong number"):
            self.find("matrix", BrokenProvider())


@unittest.skipUnless(os.getenv("GEMINI_API_KEY"), "needs GEMINI_API_KEY")
class TestGeminiProvider(unittest.TestCase):
    def test_real_embedding(self):
        from agent_memory.providers.gemini import GeminiProvider

        vector = GeminiProvider(dimensions=128).embed_query("agent memory")
        self.assertEqual(len(vector), 128)

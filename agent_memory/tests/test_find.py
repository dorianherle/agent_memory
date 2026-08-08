import unittest
from unittest.mock import patch

from agent_memory.tests.helpers import FakeProvider, MemoryTestCase

try:
    import hnswlib  # noqa: F401
    HAS_HNSWLIB = True
except ImportError:
    HAS_HNSWLIB = False


class TestFind(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.save("Created matrix", {"type": "action", "status": "pass"})
        self.second = self.save("Updated document", {"type": "observation", "status": "fail"})
        self.third = self.save("Fixed matrix", {"type": "action", "status": "pass"})

    def test_no_filters_returns_list(self):
        found = self.memory.find()

        self.assertIsInstance(found, list)
        self.assertEqual([item.id for item in found], [self.third, self.second, self.first])

    def test_ids_string(self):
        self.assertEqual([item.id for item in self.memory.find(ids=self.first)], [self.first])

    def test_ids_list(self):
        found = self.memory.find(ids=[self.first, self.third])

        self.assertEqual([item.id for item in found], [self.third, self.first])

    def test_ids_none_and_missing(self):
        self.assertEqual(len(self.memory.find(ids=None)), 3)
        self.assertEqual(self.memory.find(ids="missing"), [])

    def test_text(self):
        found = self.memory.find(text="UPDATED")

        self.assertEqual([item.id for item in found], [self.second])

    def test_text_none(self):
        self.assertEqual(len(self.memory.find(text=None)), 3)

    def test_payload(self):
        found = self.memory.find(payload={"type": "action", "status": "pass"})

        self.assertEqual([item.id for item in found], [self.third, self.first])

    def test_payload_none_and_empty(self):
        self.assertEqual(len(self.memory.find(payload=None)), 3)
        self.assertEqual(len(self.memory.find(payload={})), 3)

    def test_combined_filters(self):
        found = self.memory.find(
            ids=[self.first, self.second],
            text="matrix",
            payload={"status": "pass"},
        )

        self.assertEqual([item.id for item in found], [self.first])

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic(self):
        self.memory._provider = FakeProvider()
        self.memory._search = None
        found = self.memory.find("matrix")
        self.assertEqual({item.id for item in found[:2]}, {self.first, self.third})

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic_combines_with_payload(self):
        self.memory._provider = FakeProvider()
        self.memory._search = None
        found = self.memory.find(
            "matrix",
            payload={"type": "observation"},
        )
        self.assertEqual([item.id for item in found], [self.second])

    def test_query_falls_back_to_text_without_provider(self):
        found = self.memory.find(query="UPDATED")
        self.assertEqual([item.id for item in found], [self.second])

    def test_ids_reach_other_branches(self):
        self.memory.branch(self.first, "side")

        found = self.memory.find(ids=self.third)

        self.assertEqual([item.id for item in found], [self.third])

    def test_all_branches_tool_search(self):
        from agent_memory import call_tool

        self.memory.branch(self.first, "side")

        scoped = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Fixed", "all_branches": False},
        )
        wide = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Fixed", "all_branches": True},
        )

        self.assertEqual(scoped, "Nothing found.")
        self.assertIn("Fixed matrix", wide)

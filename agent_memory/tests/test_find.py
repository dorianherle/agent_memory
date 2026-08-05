from unittest.mock import patch

from agent_memory.tests.helpers import FakeProvider, MemoryTestCase


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

    def test_semantic(self):
        with patch("agent_memory.search.load_provider", return_value=FakeProvider()):
            found = self.memory.find("matrix")

        self.assertEqual({item.id for item in found[:2]}, {self.first, self.third})

    def test_semantic_combines_with_payload(self):
        with patch("agent_memory.search.load_provider", return_value=FakeProvider()):
            found = self.memory.find(
                "matrix",
                payload={"type": "observation"},
            )

        self.assertEqual([item.id for item in found], [self.second])

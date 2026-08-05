from datetime import datetime

from agent_memory import MemoryItem
from agent_memory.tests.helpers import MemoryTestCase


class TestSave(MemoryTestCase):
    def test_message_and_return_id(self):
        self.file.write_text("content")
        item_id = self.memory.save("Created file")

        self.assertEqual(len(item_id), 40)
        item = self.memory.find(ids=item_id)[0]
        self.assertIsInstance(item, MemoryItem)
        self.assertEqual(item.message, "Created file")
        self.assertEqual(item.payload, {})
        self.assertIsInstance(item.datetime, datetime)
        self.assertIsNotNone(item.datetime.tzinfo)

    def test_payload(self):
        payload = {"type": "action", "details": {"files": 2}}
        self.file.write_text("content")
        item_id = self.memory.save("Stored payload", payload=payload)

        self.assertEqual(self.memory.find(ids=item_id)[0].payload, payload)

    def test_allow_empty_false(self):
        self.save("Initial")

        with self.assertRaises(RuntimeError):
            self.memory.save("No file changes", allow_empty=False)

    def test_allow_empty_true(self):
        self.save("Initial")

        item_id = self.memory.save("Thought about it")

        self.assertEqual(self.memory.find(ids=item_id)[0].message, "Thought about it")

    def test_string_path(self):
        other = self.path / "other"
        memory = type(self.memory)(str(other))
        (other / "file.txt").write_text("content")

        self.assertEqual(len(memory.save("String path")), 40)

from agent_memory.tests.helpers import MemoryTestCase


class TestClear(MemoryTestCase):
    def test_archive_defaults_to_true(self):
        self.save("first")

        result = self.memory.clear()

        self.assertIsNone(result)
        self.assertEqual(self.memory.find(), [])
        self.assertEqual(len(list(self.path.glob(".agent_memory_archive_*"))), 1)

    def test_archive_true(self):
        self.save("first")

        self.memory.clear(archive=True)

        self.assertEqual(self.memory.find(), [])
        self.assertEqual(len(list(self.path.glob(".agent_memory_archive_*"))), 1)

    def test_archive_false(self):
        self.save("first")

        self.memory.clear(archive=False)

        self.assertEqual(self.memory.find(), [])
        self.assertEqual(list(self.path.glob(".agent_memory_archive_*")), [])

    def test_can_save_after_clear(self):
        self.save("first")
        self.memory.clear(archive=False)
        self.file.write_text("second")

        item_id = self.memory.save("second")

        self.assertEqual(self.memory.find(ids=item_id)[0].message, "second")

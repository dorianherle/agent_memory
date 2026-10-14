from agent_memory.tests.helpers import MemoryTestCase


class TestRevisit(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.save("first")
        self.second = self.save("second")

    def test_id_and_restore(self):
        with self.memory.revisit(id=self.first) as item:
            self.assertEqual(item.id, self.first)
            self.assertEqual(self.file.read_text(), "first")

        self.assertEqual(self.file.read_text(), "second")

    def test_restores_after_error(self):
        with self.assertRaises(ValueError):
            with self.memory.revisit(id=self.first):
                raise ValueError("stop")

        self.assertEqual(self.file.read_text(), "second")

    def test_unknown_id(self):
        with self.assertRaises(ValueError):
            with self.memory.revisit(id="missing"):
                pass

from agent_memory import Memory
from agent_memory.tests.helpers import MemoryTestCase


class TestBranch(MemoryTestCase):
    def test_base_id_name_and_return_value(self):
        first = self.save("first")
        self.save("second")

        branch = self.memory.branch(base_id=first, name="different-idea")

        self.assertIsInstance(branch, Memory)
        self.assertEqual(branch._branch_name(), "different-idea")
        self.assertEqual(self.file.read_text(), "first")
        self.assertEqual(branch.find(ids=first)[0].message, "first")

    def test_unknown_base_id(self):
        self.save("first")

        with self.assertRaises(ValueError):
            self.memory.branch(base_id="missing", name="bad")

    def test_duplicate_name(self):
        first = self.save("first")
        self.memory.branch(base_id=first, name="duplicate")

        with self.assertRaises(RuntimeError):
            self.memory.branch(base_id=first, name="duplicate")

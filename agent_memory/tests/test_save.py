from datetime import datetime

from agent_memory import MemoryItem
from agent_memory.core import GENERATED_STATE_FILES
from agent_memory.tests.helpers import MemoryTestCase


class TestSave(MemoryTestCase):
    def test_generated_state_is_excluded_even_when_previously_tracked(self):
        self.save("source")
        for name in GENERATED_STATE_FILES:
            path = self.path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("old generated state")
            self.memory._run("add", "-f", "--", name)
        self.memory._run("commit", "-m", "Previously tracked generated state")
        self.memory.write_state_file()
        self.memory.write_state_rule()
        snapshot = self.memory.save("Current source and notes")
        names = self.memory._run("ls-tree", "-r", "--name-only", snapshot).splitlines()
        for name in GENERATED_STATE_FILES:
            self.assertNotIn(name, names)
            self.assertTrue((self.path / name).exists())
        self.assertEqual(self.memory._run("show", f"{snapshot}:work.txt"), "source")

    def test_message_and_return_id(self):
        self.file.write_text("content")
        item_id = self.memory.save("Created file")

        self.assertEqual(len(item_id), 40)
        item = self.memory.find(ids=item_id)[0]
        self.assertIsInstance(item, MemoryItem)
        self.assertEqual(item.message, "Created file")
        self.assertEqual(item.payload, {"agent": "agent"})
        self.assertIsInstance(item.datetime, datetime)
        self.assertIsNotNone(item.datetime.tzinfo)

    def test_payload(self):
        payload = {"type": "action", "details": {"files": 2}}
        self.file.write_text("content")
        item_id = self.memory.save("Stored payload", payload=payload)

        self.assertEqual(
            self.memory.find(ids=item_id)[0].payload,
            {**payload, "agent": "agent"},
        )

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

    def test_nested_git_does_not_break_summarize(self):
        import subprocess

        nested = self.path / "vendor" / "pkg"
        nested.mkdir(parents=True)
        (nested / "code.py").write_text("print(1)\n")
        subprocess.run(
            ["git", "init"],
            cwd=nested,
            check=True,
            capture_output=True,
        )
        # Host-looking .git at the worktree root (agent-memory uses its own git-dir).
        host_git = self.path / ".git"
        host_git.mkdir()
        (host_git / "HEAD").write_text("ref: refs/heads/main\n")

        self.memory.save("with nested git present")
        summary = self.memory.summarize("still works with nested git")
        self.assertEqual(len(summary), 40)
        exclude = (self.path / ".agent_memory" / "info" / "exclude").read_text()
        self.assertIn(".git/", exclude)
        self.assertIn("vendor/pkg/", exclude)

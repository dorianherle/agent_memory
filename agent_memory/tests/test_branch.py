import subprocess
import sys

from agent_memory import Memory
from agent_memory.tests.helpers import MemoryTestCase


class TestBranch(MemoryTestCase):
    def test_failed_command_allows_revision_without_branch(self):
        self.save("initial")
        result = subprocess.run(
            [sys.executable, "-c", "from pathlib import Path; assert Path('work.txt').read_text() == 'fixed'"],
            cwd=self.path, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.file.write_text("fixed")
        saved = self.memory.save("Fixed content after the assertion failed")
        self.assertEqual(self.memory._branch_name(), "main")
        self.assertEqual(self.memory._run("show", f"{saved}:work.txt"), "fixed")

    def test_branch_summary_is_searchable_without_becoming_an_instruction(self):
        first = self.save("initial")
        self.save("candidate")
        summary = "This candidate failed on empty input."
        self.memory.branch(first, "retry", summary=summary)
        found = self.memory.find(text=summary, branch=None)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].message, summary)
        self.assertNotIn(summary, self.memory.render_state())
        self.assertEqual(self.file.read_text(), "initial")

    def test_base_id_name_and_return_value(self):
        first = self.save("first")
        self.save("second")

        branch = self.memory.branch(
            base_id=first, name="different-idea", summary="second was wrong"
        )

        self.assertIsInstance(branch, Memory)
        self.assertEqual(branch._branch_name(), "different-idea")
        self.assertEqual(self.file.read_text(), "first")
        self.assertEqual(branch.find(ids=first)[0].message, "first")

    def test_summary_is_required(self):
        first = self.save("first")
        with self.assertRaises(ValueError):
            self.memory.branch(first, "retry", summary="")
        with self.assertRaises(TypeError):
            self.memory.branch(first, "retry")  # type: ignore[misc]

    def test_unknown_base_id(self):
        self.save("first")

        with self.assertRaises(ValueError):
            self.memory.branch(
                base_id="missing", name="bad", summary="no such id"
            )

    def test_duplicate_name(self):
        first = self.save("first")
        second = self.save("second")
        self.save("third")
        self.memory.branch(
            base_id=first, name="duplicate", summary="left for a retry"
        )

        with self.assertRaises(RuntimeError):
            self.memory.branch(
                base_id=second, name="duplicate", summary="name clash"
            )

    def test_summary_caps_the_left_branch(self):
        first = self.save("first")
        second = self.save("second")

        self.memory.branch(first, "retry", summary="second was a dead end")

        cap = self.memory._refs()["main"]
        item = self.memory.find(ids=cap)[0]
        self.assertEqual(item.message, "second was a dead end")
        self.assertIn(second, item.payload["covers"])
        self.assertEqual([entry.id for entry in self.memory.state()], [first])

    def test_branch_to_tip_resumes_that_branch(self):
        first = self.save("first")
        self.save("second")
        self.memory.branch(first, "retry", summary="left main")

        tip = self.memory._refs()["main"]
        self.memory.branch(tip, "ignored", summary="back to main")

        self.assertEqual(self.memory._branch_name(), "main")
        self.assertEqual(self.file.read_text(), "second")

    def test_inspect_and_done(self):
        first = self.save("first")
        self.save("second")

        item = self.memory.inspect(first)

        self.assertEqual(item.id, first)
        self.assertEqual(self.file.read_text(), "first")
        self.assertEqual(self.memory.inspecting()["branch"], "main")
        with self.assertRaises(RuntimeError):
            self.memory.save("not allowed")
        with self.assertRaises(RuntimeError):
            self.memory.branch(first, "not-allowed", summary="blocked")

        branch = self.memory.inspect_done()

        self.assertEqual(branch, "main")
        self.assertEqual(self.file.read_text(), "second")
        self.assertIsNone(self.memory.inspecting())

    def test_inspect_switches_snapshots(self):
        first = self.save("first")
        second = self.save("second")
        self.save("third")

        self.memory.inspect(first)
        self.memory.inspect(second)

        self.assertEqual(self.file.read_text(), "second")
        self.assertEqual(self.memory.inspecting()["branch"], "main")
        self.memory.inspect_done()
        self.assertEqual(self.file.read_text(), "third")

    def test_stale_inspect_marker_is_recovered(self):
        first = self.save("first")
        self.save("second")
        self.memory.inspect(first)

        memory = Memory(self.path)

        self.assertIsNone(memory.inspecting())
        self.assertEqual(self.file.read_text(), "second")

    def test_inspect_done_without_inspect(self):
        self.save("first")

        with self.assertRaises(RuntimeError):
            self.memory.inspect_done()

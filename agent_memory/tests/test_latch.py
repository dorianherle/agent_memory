import os
import sys
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.cursor.hook import handle
from agent_memory.tests.helpers import MemoryTestCase

FAIL = f"{sys.executable} -c 'raise SystemExit(1)'"
PASS = f"{sys.executable} -c 'raise SystemExit(0)'"
AUTOPSY = "Redis cache made p95 worse. Do not retry under this schema."


class LatchTests(MemoryTestCase):
    def test_failed_check_sets_red_latch(self) -> None:
        keep = self.save("green checkpoint")
        with self.assertRaises(RuntimeError) as raised:
            self.memory.check(FAIL)
        self.assertIn("check failed", str(raised.exception))
        latch = self.memory.latch()
        self.assertEqual(latch["status"], "red")
        self.assertEqual(latch["checkpoint_id"], keep)
        self.assertEqual(latch["exit_code"], 1)
        self.assertTrue(latch["failure_id"])

    def test_red_latch_blocks_mutation_and_normal_save(self) -> None:
        self.save("green checkpoint")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        with self.assertRaises(RuntimeError) as raised:
            self.memory.save("keep going on the red path")
        self.assertIn("only memory_branch is allowed", str(raised.exception).lower())
        with self.assertRaises(RuntimeError):
            self.memory.summarize("fold the failed work")
        with self.assertRaises(RuntimeError):
            self.memory.require_green("edit")
        with self.assertRaises(RuntimeError):
            self.memory.check(PASS)

    def test_branch_is_only_escape_from_red(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        self.memory.branch(keep, "retry", AUTOPSY)
        latch = self.memory.latch()
        self.assertEqual(latch["status"], "green")
        self.assertEqual(latch["checkpoint_id"], keep)
        note = self.memory.save("new attempt after rewind")
        self.assertTrue(note)

    def test_branch_restores_exact_last_green_snapshot(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken mutation")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        decoy = self.memory.last_green()
        self.assertEqual(decoy, keep)
        self.memory.branch("0" * 40, "retry", AUTOPSY)
        self.assertEqual(self.file.read_text(), "green checkpoint")
        self.assertEqual(self.memory._run("rev-parse", "HEAD"), keep)

    def test_one_failure_creates_exactly_one_autopsy(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        self.memory.branch(keep, "retry", AUTOPSY)
        avoid = self.memory.prompt_sections()["avoid"]
        self.assertEqual(len(avoid), 1)
        self.assertEqual(avoid[0].message, AUTOPSY)
        self.assertTrue(avoid[0].payload.get("failure_id"))

    def test_vague_or_multiline_autopsy_is_rejected(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        with self.assertRaises(ValueError):
            self.memory.branch(keep, "retry", "tests failed")
        with self.assertRaises(ValueError):
            self.memory.branch(keep, "retry", "line one\nline two")
        self.assertEqual(self.memory.latch()["status"], "red")
        self.memory.branch(keep, "retry", AUTOPSY)
        self.assertEqual(self.memory.latch()["status"], "green")

    def test_next_prompt_contains_autopsy_exactly_once(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        self.memory.branch(keep, "retry", AUTOPSY)
        text = self.memory.render_state()
        self.assertEqual(text.count(AUTOPSY), 1)
        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            out = handle("beforeSubmitPrompt", {"prompt": "continue the job"})
        self.assertNotIn(AUTOPSY, out["additional_context"])
        self.assertIn("alwaysApply rule", out["additional_context"])
        rule = (self.path / ".cursor" / "rules" / "agent-memory-state.mdc").read_text()
        self.assertEqual(rule.count(AUTOPSY), 1)

    def test_repeated_prompt_render_does_not_duplicate_autopsy(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        self.memory.branch(keep, "retry", AUTOPSY)
        first = self.memory.render_state()
        second = self.memory.render_state()
        self.assertEqual(first.count(AUTOPSY), 1)
        self.assertEqual(second.count(AUTOPSY), 1)
        self.assertEqual(len(self.memory.prompt_sections()["avoid"]), 1)
        self.assertEqual(len(self.memory.prompt_sections()["avoid"]), 1)

    def test_successful_check_advances_last_green(self) -> None:
        first = self.save("first green")
        self.assertEqual(self.memory.check(PASS), f"check passed: {PASS}")
        self.assertEqual(self.memory.last_green(), first)
        second = self.save("second green")
        self.memory.check(PASS)
        self.assertEqual(self.memory.last_green(), second)

    def test_process_restart_preserves_red_latch(self) -> None:
        keep = self.save("green checkpoint")
        self.file.write_text("broken")
        with self.assertRaises(RuntimeError):
            self.memory.check(FAIL)
        restarted = Memory(self.path)
        latch = restarted.latch()
        self.assertEqual(latch["status"], "red")
        self.assertEqual(latch["checkpoint_id"], keep)
        with self.assertRaises(RuntimeError):
            restarted.save("cannot continue after restart")

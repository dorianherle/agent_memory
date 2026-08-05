import unittest

from agent_memory.prompt import render
from agent_memory.tests.helpers import MemoryTestCase


class StateTests(MemoryTestCase):
    def test_summary_hides_recent_steps(self) -> None:
        a = self.save("step a")
        self.save("step b")
        summary = self.memory.save("did a and b", summary=True)

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [summary])

        found = self.memory.find(ids=a)
        self.assertEqual(found[0].message, "step a")

    def test_state_orders_oldest_to_newest(self) -> None:
        self.save("step a")
        summary = self.memory.save("did a", summary=True)
        b = self.save("step b")

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [summary, b])

    def test_summary_keeps_earlier_summaries_visible(self) -> None:
        self.save("step a")
        first = self.memory.save("summary one", summary=True)
        self.save("step b")
        second = self.memory.save("summary two", summary=True)

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [first, second])

    def test_summary_without_recent_steps_is_a_plain_entry(self) -> None:
        self.save("step a")
        first = self.memory.save("did a", summary=True)
        second = self.memory.save("nothing left to fold", summary=True)

        item = self.memory.find(ids=second)[0]
        self.assertNotIn("covers", item.payload)
        self.assertEqual(
            [entry.id for entry in self.memory.state()], [first, second]
        )

    def test_state_keeps_duplicate_messages(self) -> None:
        self.memory.save("same message", allow_empty=True)
        self.memory.save("same message", allow_empty=True)

        messages = [item.message for item in self.memory.state()]
        self.assertEqual(messages, ["same message", "same message"])

    def test_branch_isolates_summary(self) -> None:
        a = self.save("step a")
        self.save("step b")
        self.save("step c")
        self.memory.save("summary of a, b and c", summary=True)

        branched = self.memory.branch(a, "retry")
        note = branched.save(
            "went wrong after a",
            {"tag": "hint"},
            allow_empty=True,
        )

        ids = [item.id for item in branched.state()]
        self.assertEqual(ids, [a, note])

    def test_render_splits_history_and_recent(self) -> None:
        a = self.save("step a")
        summary = self.memory.save("did a", summary=True)
        b = self.save("step b")

        text = render(self.memory.state())
        self.assertIn("## History", text)
        self.assertIn(f"[{summary[:8]}] did a", text)
        self.assertIn("## Recent", text)
        self.assertIn(f"[{b[:8]}] step b", text)
        self.assertNotIn(a[:8], text)

    def test_render_without_summaries(self) -> None:
        a = self.save("step a")

        text = render(self.memory.state())
        self.assertNotIn("## History", text)
        self.assertIn("## Recent", text)
        self.assertIn(f"[{a[:8]}] step a", text)

    def test_render_nudges_when_recent_is_long(self) -> None:
        self.save("step a")
        self.save("step b")
        self.save("step c")

        text = render(self.memory.state(), nudge_after=2)
        self.assertIn("Recent has 3 steps", text)

        text = render(self.memory.state(), nudge_after=5)
        self.assertNotIn("Recent has", text)

    def test_render_branches_section(self) -> None:
        a = self.save("step a")
        self.save("step b")
        self.memory.branch(a, "retry", summary="b went nowhere")

        text = render(self.memory.state(), self.memory.branches())

        self.assertIn("## Branches", text)
        self.assertIn("* retry (you are here)", text)
        cap = self.memory._refs()["main"]
        self.assertIn(f"[{cap[:8]}] main: b went nowhere", text)

    def test_render_single_branch_has_no_branches_section(self) -> None:
        self.save("step a")

        text = render(self.memory.state(), self.memory.branches())

        self.assertNotIn("## Branches", text)

    def test_render_user_lines(self) -> None:
        item = self.memory.save_user("fix the bug")

        text = render(self.memory.state())

        self.assertIn(f"[{item[:8]}] user: fix the bug", text)

    def test_save_user_skips_repeated_text(self) -> None:
        first = self.memory.save_user("fix the bug")
        second = self.memory.save_user("fix the bug")
        third = self.memory.save_user("now test it")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)

    def test_render_inspect_banner(self) -> None:
        a = self.save("step a")
        self.save("step b")
        self.memory.inspect(a)

        text = render(
            self.memory.state(),
            self.memory.branches(),
            self.memory.inspecting(),
        )

        self.assertIn(f"INSPECTING [{a[:8]}]", text)
        self.assertIn("return to 'main'", text)
        self.memory.inspect_done()


if __name__ == "__main__":
    unittest.main()

import unittest

from agent_memory.prompt import render
from agent_memory.tests.helpers import MemoryTestCase


class StateTests(MemoryTestCase):
    def test_summary_hides_covered_steps(self) -> None:
        a = self.save("step a")
        b = self.save("step b")
        summary = self.memory.save("did a and b", covers=[a, b])

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [summary])

        found = self.memory.find(ids=a)
        self.assertEqual(found[0].message, "step a")

    def test_state_orders_oldest_to_newest(self) -> None:
        a = self.save("step a")
        b = self.save("step b")
        summary = self.memory.save("did a", covers=[a])

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [b, summary])

    def test_summary_of_summaries_hides_transitively(self) -> None:
        a = self.save("step a")
        b = self.save("step b")
        first = self.memory.save("summary one", covers=[a, b])
        c = self.save("step c")
        second = self.memory.save("summary two", covers=[c])
        top = self.memory.save("top summary", covers=[first, second])

        ids = [item.id for item in self.memory.state()]
        self.assertEqual(ids, [top])

    def test_covers_accepts_short_ids(self) -> None:
        a = self.save("step a")
        summary = self.memory.save("did a", covers=[a[:8]])

        item = self.memory.find(ids=summary)[0]
        self.assertEqual(item.payload["covers"], [a])
        self.assertEqual([entry.id for entry in self.memory.state()], [summary])

    def test_state_keeps_duplicate_messages(self) -> None:
        self.memory.save("same message", allow_empty=True)
        self.memory.save("same message", allow_empty=True)

        messages = [item.message for item in self.memory.state()]
        self.assertEqual(messages, ["same message", "same message"])

    def test_branch_isolates_summary(self) -> None:
        a = self.save("step a")
        b = self.save("step b")
        c = self.save("step c")
        self.memory.save("summary of b and c", covers=[b, c])

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
        summary = self.memory.save("did a", covers=[a])
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


if __name__ == "__main__":
    unittest.main()

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

        branched = self.memory.branch(a, "retry", summary="b and c went nowhere")
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
        self.assertIn(f"[{summary[:8]}] @agent Summary: did a", text)
        self.assertIn("## Recent", text)
        self.assertIn(f"[{b[:8]}] @agent step b", text)
        self.assertNotIn(a[:8], text)

    def test_render_without_summaries(self) -> None:
        a = self.save("step a")

        text = render(self.memory.state())
        self.assertNotIn("## History", text)
        self.assertIn("## Recent", text)
        self.assertIn(f"[{a[:8]}] @agent step a", text)

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

    def test_render_marks_summaries(self) -> None:
        self.save("step a")
        summary = self.memory.save("did a", summary=True)

        text = render(self.memory.state())

        self.assertIn(f"[{summary[:8]}] @agent Summary: did a", text)

    def test_render_user_lines(self) -> None:
        item = self.memory.save_user("fix the bug")

        text = render(self.memory.state())

        self.assertIn(f"[{item[:8]}] user: fix the bug", text)

    def test_compact_lifts_original_user_to_goal(self) -> None:
        goal = self.memory.save_user("make the header small")
        self.save("finding: cap Recent at 10")
        text = self.memory.render_state()
        self.assertIn("## Goal", text)
        self.assertIn(f"[{goal[:8]}] make the header small", text)
        recent = text.split("## Recent", 1)[1]
        self.assertNotIn(goal[:8], recent)
        self.assertNotIn("user: make the header small", text)

    def test_later_user_notes_are_steering_not_goal(self) -> None:
        goal = self.memory.save_user("ship the open-question rename")
        note = self.memory.save_user("wait also clean the readme")
        text = self.memory.render_state()
        self.assertIn(f"[{goal[:8]}] ship the open-question rename", text)
        self.assertIn("## Steering", text)
        self.assertIn(
            f"[{note[:8]}] wait also clean the readme",
            text.split("## Steering", 1)[1].split("## Recent", 1)[0],
        )
        self.assertNotIn(note[:8], text.split("## Recent", 1)[1])

    def test_goal_survives_summarize(self) -> None:
        goal = self.memory.save_user("run the ralph loop")
        self.save("step a")
        self.memory.summarize("chunk done")
        text = self.memory.render_state()
        self.assertIn(f"[{goal[:8]}] run the ralph loop", text)
        self.assertIn("## Goal", text)

    def test_steering_survives_summarize(self) -> None:
        self.memory.save_user("run the ralph loop")
        note = self.memory.save_user("wait also clean the readme")
        self.save("step a")
        self.memory.summarize("chunk done")
        text = self.memory.render_state()
        self.assertIn("## Steering", text)
        self.assertIn(f"[{note[:8]}] wait also clean the readme", text)

    def test_compact_keeps_goal_after_recent_cap(self) -> None:
        from agent_memory.prompt import RECENT_CAP

        goal = self.memory.save_user("ship the open-question rename")
        for index in range(RECENT_CAP + 2):
            self.save(f"step {index}")
        text = self.memory.render_state()
        self.assertIn(f"[{goal[:8]}] ship the open-question rename", text)
        self.assertIn("## Goal", text)

    def test_render_compact_uses_commit_subjects_only(self) -> None:
        long_body = (
            "fixed the webhook retry\n\n"
            "Details:\n- checked logs\n- patched handler\n"
            + ("x" * 200)
        )
        item = self.memory.save(long_body)

        before_summary = render(self.memory.state())
        before_compact = render(self.memory.state(), compact=True)
        self.assertIn("Details:", before_summary)
        self.assertNotIn("Details:", before_compact)
        self.assertNotIn("xxx", before_compact)
        self.assertIn(
            f"[{item[:8]}] @agent fixed the webhook retry", before_compact
        )
        self.assertIn("Commit subjects only", before_compact)

        self.memory.save("step two with\nmultiple lines")
        summary = self.memory.save("finished pairing steps", summary=True)
        compact = self.memory.render_state()

        self.assertNotIn("## Last summary", compact)
        self.assertNotIn("## History", compact)
        self.assertNotIn(summary[:8], compact)
        # Folded step is not a top-level Recent line.
        recent_section = compact.split("## Recent", 1)[1]
        self.assertNotIn(item[:8], recent_section)
        # Full body still retrievable.
        found = self.memory.find(ids=item)
        self.assertIn("Details:", found[0].message)

    def test_compact_hides_summaries(self) -> None:
        self.save("step a")
        first = self.memory.save("fold one", summary=True)
        self.save("step b")
        second = self.memory.save("fold two", summary=True)
        text = self.memory.render_state()
        self.assertNotIn("## Last summary", text)
        self.assertNotIn(f"[{first[:8]}]", text)
        self.assertNotIn(f"[{second[:8]}]", text)
        self.assertNotIn("## History", text)
        self.assertEqual(
            self.memory.find(ids=second)[0].message, "fold two"
        )

    def test_compact_caps_recent(self) -> None:
        from agent_memory.prompt import RECENT_CAP

        ids = [self.save(f"step {i}") for i in range(RECENT_CAP + 2)]
        text = self.memory.render_state()
        self.assertIn("(+2 earlier — memory_find / memory_history)", text)
        self.assertNotIn(f"[{ids[0][:8]}]", text)
        self.assertIn(f"[{ids[-1][:8]}]", text)
        self.assertNotIn("## History", text)

    def test_compact_hides_other_branches(self) -> None:
        first = self.save("step a")
        self.save("step b")
        self.memory.branch(first, "retry", summary="side quest")
        text = self.memory.render_state()
        self.assertIn("* retry (you are here)", text)
        self.assertNotIn("## Branches", text)
        self.assertNotIn("main:", text)

    def test_compact_nudges_when_checkpoint_is_old(self) -> None:
        self.save("real finding")
        for index in range(8):
            self.memory.save_user(f"prompt {index}")
        text = self.memory.render_state()
        self.assertIn("last @agent checkpoint 8 steps ago", text)

    def test_compact_skips_checkpoint_nudge_when_fresh(self) -> None:
        self.save("real finding")
        self.memory.save_user("one prompt")
        text = self.memory.render_state()
        self.assertNotIn("last @agent checkpoint", text)

    def test_compact_marks_stale_when_own_files_changed(self) -> None:
        old = self.save("step a")
        fresh = self.save("step b")
        text = self.memory.render_state()
        self.assertIn(f"[{old[:8]}] [stale]", text)
        self.assertNotIn(f"[{fresh[:8]}] [stale]", text)

    def test_compact_goal_steering_questions_are_never_stale(self) -> None:
        self.file.write_text("start")
        goal = self.memory.save_user("the original job")
        self.file.write_text("nudge")
        steer = self.memory.save_user("wait also check the cookie")
        self.file.write_text("ask")
        question = self.memory.open_question("Why is it still slow?")
        old = self.save("finding: full table scan")
        self.save("later finding")
        text = self.memory.render_state()
        pinned = text.split("## Recent", 1)[0]
        self.assertNotIn("[stale]", pinned)
        self.assertIn(f"[{goal[:8]}] the original job", pinned)
        self.assertIn(f"[{steer[:8]}] wait also check the cookie", pinned)
        self.assertIn(f"[{question[:8]}] Why is it still slow?", pinned)
        self.assertIn(f"[{old[:8]}] [stale]", text.split("## Recent", 1)[1])

    def test_compact_recent_hides_user_and_auto(self) -> None:
        self.memory.save_user("the original job")
        mid = self.memory.save_user("a mid-run prompt")
        self.memory.save_user("wait also the cookie")
        finding = self.save("finding: full table scan")
        auto = self.memory.auto_save("finished the turn")
        text = self.memory.render_state()
        recent = text.split("## Recent", 1)[1]
        self.assertIn(f"[{finding[:8]}]", recent)
        self.assertNotIn(f"[{mid[:8]}]", recent)
        self.assertNotIn("user:", recent)
        self.assertNotIn(f"[{auto[:8]}]", recent)
        self.assertNotIn("finished the turn", recent)

    def test_note_only_save_is_not_stale(self) -> None:
        note = self.memory.save("decision: use pytest")
        self.file.write_text("unrelated change")
        self.memory.save("later step")
        text = self.memory.render_state()
        self.assertNotIn(f"[{note[:8]}] [stale]", text)

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

from agent_memory.core import OPEN_QUESTION_KEY, all_open_questions, split_open_questions
from agent_memory.prompt import render
from agent_memory.tests.helpers import MemoryTestCase
from agent_memory.tools import call_tool


class OpenQuestionTests(MemoryTestCase):
    def test_add_lands_in_state_with_depends_on(self) -> None:
        evidence = self.save("root cause: full scan")
        qid = self.memory.open_question("Why is login still slow?", [evidence])
        state = self.memory.state()
        self.assertIn(qid, [item.id for item in state])
        questions = split_open_questions(state)
        self.assertEqual([item.id for item in questions], [qid])
        self.assertEqual(questions[0].payload["depends_on"], [evidence])

    def test_render_uses_square_brackets_only(self) -> None:
        evidence = self.save("root cause: full scan")
        qid = self.memory.open_question("Why is login still slow?", [evidence])
        text = render(self.memory.state(), compact=True)
        self.assertIn("## Open questions", text)
        self.assertIn(f"[{qid[:8]}] Why is login still slow? [{evidence[:8]}]", text)
        self.assertNotIn("rests on", text)

    def test_render_without_questions_has_no_block(self) -> None:
        self.save("plain step")
        self.assertNotIn(
            "## Open questions", render(self.memory.state(), compact=True)
        )

    def test_same_text_is_noop(self) -> None:
        first = self.memory.open_question("Why is login still slow?")
        second = self.memory.open_question("Why is login still slow?")
        self.assertEqual(first, second)
        self.assertEqual(
            [item.id for item in split_open_questions(self.memory.state())],
            [first],
        )

    def test_same_text_ignores_case_and_spacing(self) -> None:
        first = self.memory.open_question("Why is login still slow?")
        second = self.memory.open_question("  why is   LOGIN still slow? ")
        self.assertEqual(first, second)

    def test_packet_filters_duplicate_questions(self) -> None:
        first = self.memory.save(
            "Why is login still slow?",
            {OPEN_QUESTION_KEY: True},
            allow_empty=True,
        )
        twin = self.memory.save(
            "why is login still slow?",
            {OPEN_QUESTION_KEY: True},
            allow_empty=True,
        )
        state = self.memory.state()
        self.assertEqual(
            [item.id for item in split_open_questions(state)], [first]
        )
        self.assertEqual(
            [item.id for item in all_open_questions(state)], [first, twin]
        )
        text = render(state, compact=True)
        questions = text.split("## Open questions", 1)[1].split("## Recent", 1)[0]
        self.assertIn(first[:8], questions)
        self.assertNotIn(twin[:8], questions)
        self.assertNotIn(twin[:8], text.split("## Recent", 1)[1])

    def test_close_one_closes_same_text_twins(self) -> None:
        first = self.memory.save(
            "Why slow?", {OPEN_QUESTION_KEY: True}, allow_empty=True
        )
        self.memory.save(
            "why slow?", {OPEN_QUESTION_KEY: True}, allow_empty=True
        )
        other = self.memory.open_question("Different question")
        self.memory.close_questions([first])
        left = split_open_questions(self.memory.state())
        self.assertEqual([item.id for item in left], [other])
        self.assertEqual(all_open_questions(self.memory.state()), left)

    def test_can_have_several(self) -> None:
        first = self.memory.open_question("Is the retry gate sound?")
        second = self.memory.open_question("Is the readme clear?")
        questions = split_open_questions(self.memory.state())
        self.assertEqual([item.id for item in questions], [first, second])
        text = render(self.memory.state(), compact=True)
        self.assertIn("Is the retry gate sound?", text)
        self.assertIn("Is the readme clear?", text)

    def test_close_all(self) -> None:
        first = self.memory.open_question("Why slow?")
        self.memory.close_questions()
        self.assertEqual(split_open_questions(self.memory.state()), [])
        self.assertNotIn(first, [item.id for item in self.memory.state()])

    def test_close_one(self) -> None:
        first = self.memory.open_question("A")
        second = self.memory.open_question("B")
        self.memory.close_questions([first])
        left = split_open_questions(self.memory.state())
        self.assertEqual([item.id for item in left], [second])

    def test_close_without_questions_is_noop(self) -> None:
        self.assertEqual(self.memory.close_questions(), [])

    def test_unknown_dependency_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.memory.open_question("question", ["not-a-real-id"])

    def test_close_non_question_is_rejected(self) -> None:
        step = self.save("a finding")
        self.memory.open_question("Why slow?")
        with self.assertRaises(ValueError):
            self.memory.close_questions([step])

    def test_survives_summarize(self) -> None:
        evidence = self.save("root cause: full scan")
        qid = self.memory.open_question("Why is login still slow?", [evidence])
        self.memory.summarize("stage closed")
        state = self.memory.state()
        self.assertIn(qid, [item.id for item in state])
        text = render(state, compact=True)
        self.assertIn("## Open questions", text)
        self.assertEqual(text.count(evidence[:8]), 1)

    def test_compact_does_not_duplicate_question_in_recent(self) -> None:
        qid = self.memory.open_question("Why slow?")
        text = render(self.memory.state(), compact=True)
        recent = text.split("## Recent", 1)[1]
        self.assertNotIn(qid[:8], recent)

    def test_tool_round_trip(self) -> None:
        evidence = self.save("root cause: full scan")
        result = call_tool(
            self.memory,
            "memory",
            {
                "action": "open_questions",
                "text": "Why slow?",
                "ids": [evidence],
            },
        )
        self.assertIn("Open question [", result)
        self.assertIn("added", result)
        self.assertIn(f"[{evidence[:8]}]", result)
        again = call_tool(
            self.memory,
            "memory",
            {"action": "open_questions", "text": "Why slow?"},
        )
        self.assertIn("already open", again)
        self.assertIn(
            "## Open questions", render(self.memory.state(), compact=True)
        )
        cleared = call_tool(
            self.memory, "memory", {"action": "open_questions", "text": ""}
        )
        self.assertIn("Closed [", cleared)
        self.assertNotIn(
            "## Open questions", render(self.memory.state(), compact=True)
        )

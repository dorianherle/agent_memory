from agent_memory.prompt import render
from agent_memory.tests.helpers import MemoryTestCase


class DeadEndTests(MemoryTestCase):
    def test_branch_autopsy_appears_under_avoid(self) -> None:
        keep = self.save("green checkpoint")
        self.save("redis made latency worse")
        self.memory.branch(
            keep, "retry", summary="Redis cache made p95 worse. Do not retry."
        )
        avoid = self.memory.prompt_sections()["avoid"]
        self.assertEqual(len(avoid), 1)
        self.assertIn("Redis cache made p95 worse", avoid[0].message)
        text = self.memory.render_state()
        self.assertIn("## Avoid / Previous failed paths", text)
        self.assertIn("Redis cache made p95 worse", text)
        self.assertNotIn("## Prior knowledge", text)

    def test_ordinary_summarize_is_not_avoid(self) -> None:
        self.save("step a")
        self.memory.summarize("chunk done")
        self.assertEqual(self.memory.prompt_sections(), {})

    def test_two_dead_ends_newest_first(self) -> None:
        a = self.save("a")
        self.memory.branch(a, "try-b", summary="path A failed the login test")
        self.save("b")
        self.memory.branch(a, "try-c", summary="path B failed the cookie test")
        messages = [item.message for item in self.memory.prompt_sections()["avoid"]]
        self.assertEqual(messages[0], "path B failed the cookie test")
        self.assertIn("path A failed the login test", messages)

    def test_empty_sections_omitted_from_render(self) -> None:
        self.save("a step")
        text = render(self.memory.state(), compact=True, sections={})
        self.assertNotIn("Avoid", text)
        self.assertNotIn("Prior knowledge", text)

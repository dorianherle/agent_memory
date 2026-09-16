import os
import unittest
from unittest.mock import patch

from agent_memory.tests.helpers import FakeProvider, MemoryTestCase

try:
    import hnswlib  # noqa: F401
    HAS_HNSWLIB = True
except ImportError:
    HAS_HNSWLIB = False


class TestFind(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.save("Created matrix", {"type": "action", "status": "pass"})
        self.second = self.save("Updated document", {"type": "observation", "status": "fail"})
        self.third = self.save("Fixed matrix", {"type": "action", "status": "pass"})

    def test_no_filters_returns_list(self):
        found = self.memory.find()

        self.assertIsInstance(found, list)
        self.assertEqual([item.id for item in found], [self.third, self.second, self.first])

    def test_ids_string(self):
        self.assertEqual([item.id for item in self.memory.find(ids=self.first)], [self.first])

    def test_ids_list(self):
        found = self.memory.find(ids=[self.first, self.third])

        self.assertEqual([item.id for item in found], [self.third, self.first])

    def test_ids_none_and_missing(self):
        self.assertEqual(len(self.memory.find(ids=None)), 3)
        self.assertEqual(self.memory.find(ids="missing"), [])

    def test_text(self):
        found = self.memory.find(text="UPDATED")

        self.assertEqual([item.id for item in found], [self.second])

    def test_text_none(self):
        self.assertEqual(len(self.memory.find(text=None)), 3)

    def test_payload(self):
        found = self.memory.find(payload={"type": "action", "status": "pass"})

        self.assertEqual([item.id for item in found], [self.third, self.first])

    def test_payload_none_and_empty(self):
        self.assertEqual(len(self.memory.find(payload=None)), 3)
        self.assertEqual(len(self.memory.find(payload={})), 3)

    def test_combined_filters(self):
        found = self.memory.find(
            ids=[self.first, self.second],
            text="matrix",
            payload={"status": "pass"},
        )

        self.assertEqual([item.id for item in found], [self.first])

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic(self):
        self.memory._provider = FakeProvider()
        self.memory._search = None
        found = self.memory.find("matrix")
        self.assertEqual({item.id for item in found[:2]}, {self.first, self.third})

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic_combines_with_payload(self):
        self.memory._provider = FakeProvider()
        self.memory._search = None
        found = self.memory.find(
            "matrix",
            payload={"type": "observation"},
        )
        self.assertEqual([item.id for item in found], [self.second])

    def test_query_falls_back_to_text_without_provider(self):
        found = self.memory.find(query="UPDATED")
        self.assertEqual([item.id for item in found], [self.second])

    def test_query_falls_back_when_provider_errors(self):
        class BoomProvider:
            fingerprint = "boom:1"

            def embed_documents(self, texts):
                raise RuntimeError("no api key")

            def embed_query(self, text):
                raise RuntimeError("no api key")

        with patch.dict(
            os.environ,
            {"AGENT_MEMORY_PROVIDER": "agent_memory.providers.gemini:GeminiProvider"},
            clear=False,
        ):
            self.memory._provider = BoomProvider()
            self.memory._search = None
            found = self.memory.find(query="UPDATED")
        self.assertEqual([item.id for item in found], [self.second])

    def test_ids_reach_other_branches(self):
        self.memory.branch(self.first, "side", summary="left main")

        found = self.memory.find(ids=self.third)

        self.assertEqual([item.id for item in found], [self.third])

    def test_all_branches_tool_search(self):
        from agent_memory import call_tool

        self.memory.branch(self.first, "side", summary="left main")

        scoped = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Fixed", "all_branches": False},
        )
        wide = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Fixed", "all_branches": True},
        )

        self.assertEqual(scoped, "Nothing found.")
        self.assertIn("Fixed matrix", wide)

    def test_query_without_provider_uses_fts(self):
        from agent_memory import call_tool
        from agent_memory.prompt import SUBSTRING_FALLBACK

        with patch.dict(os.environ, {"AGENT_MEMORY_PROVIDER": ""}, clear=False):
            result = call_tool(
                self.memory,
                "memory",
                {"action": "find", "query": "matrix"},
            )
        self.assertIn("Created matrix", result)
        self.assertNotIn(SUBSTRING_FALLBACK, result)

    def test_query_fts_matches_any_word(self):
        self.save("Authentication now uses a timeout of 30 seconds.")
        with patch.dict(os.environ, {"AGENT_MEMORY_PROVIDER": ""}, clear=False):
            found = self.memory.find(query="authentication timeout")
        self.assertTrue(
            any("timeout of 30 seconds" in item.message for item in found)
        )

    def test_search_returns_headlines_not_bodies(self):
        from agent_memory import call_tool

        self.save("Auth timeout is 30s\nWhy: measured in src/auth.py:88")
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Auth timeout"},
        )
        self.assertIn("Auth timeout is 30s", result)
        self.assertNotIn("src/auth.py:88", result)

    def test_ids_return_full_bodies(self):
        from agent_memory import call_tool

        item_id = self.save(
            "Auth timeout is 30s\nWhy: measured in src/auth.py:88"
        )
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "ids": [item_id[:8]]},
        )
        self.assertIn("Auth timeout is 30s", result)
        self.assertIn("src/auth.py:88", result)

    def test_summary_hit_lists_child_headlines(self):
        from agent_memory import call_tool

        child = self.save(
            "Auth timeout is 30s\nWhy: measured in src/auth.py:88"
        )
        summary = self.memory.save("Finished auth timeout work", summary=True)
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Finished auth"},
        )
        self.assertIn("Finished auth timeout work", result)
        self.assertIn(child[:8], result)
        self.assertIn("Auth timeout is 30s", result)
        self.assertNotIn("src/auth.py:88", result)
        self.assertIn(summary[:8], result)

    def test_search_skips_transcript(self):
        from agent_memory import call_tool

        self.memory.auto_save("Auth timeout is 30s\nWhy: measured")
        self.memory.save_user("please check the Auth timeout")
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Auth timeout"},
        )
        self.assertEqual(result, "Nothing found.")

    def test_ids_can_open_auto_saves(self):
        from agent_memory import call_tool

        item_id = self.memory.auto_save(
            "Auth timeout is 30s\nWhy: measured in src/auth.py:88"
        )
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "ids": [item_id[:8]]},
        )
        self.assertIn("Auth timeout is 30s", result)
        self.assertNotIn("src/auth.py:88", result)

    def test_find_caps_long_bodies(self):
        from agent_memory import call_tool
        from agent_memory.prompt import FIND_BODY_MAX

        item_id = self.save("head\n" + ("x" * (FIND_BODY_MAX + 50)))
        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "ids": [item_id[:8]]},
        )
        self.assertIn("more)", result)
        self.assertLess(len(result), FIND_BODY_MAX + 40)

    def test_text_search_does_not_announce_fallback(self):
        from agent_memory import call_tool
        from agent_memory.prompt import SUBSTRING_FALLBACK

        result = call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "Fixed"},
        )
        self.assertIn("Fixed matrix", result)
        self.assertNotIn(SUBSTRING_FALLBACK, result)

    def test_save_hygiene_warns_on_long_headline(self):
        from agent_memory import call_tool

        result = call_tool(
            self.memory,
            "memory",
            {"action": "save", "text": "x" * 140},
        )
        self.assertTrue(result.startswith("Saved ["))
        self.assertIn("Warning: first line is 140 chars", result)

    def test_save_hygiene_warns_on_narration(self):
        from agent_memory import call_tool

        result = call_tool(
            self.memory,
            "memory",
            {"action": "save", "text": "Investigated the hook file"},
        )
        self.assertIn("Warning: headline reads like narration", result)

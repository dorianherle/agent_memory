import os
import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.tests.helpers import FakeProvider, MemoryTestCase

try:
    import hnswlib  # noqa: F401
    HAS_HNSWLIB = True
except ImportError:
    HAS_HNSWLIB = False


class TestExtendedMemory(MemoryTestCase):
    def test_reserved_payload_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.memory.save(
                "bad",
                {"_agent_memory": "junk"},
                allow_empty=True,
            )

    def test_outdated_catalog_schema_is_rebuilt_from_git(self):
        item_id = self.save("first")
        catalog_path = self.memory._catalog.path
        self.memory._catalog.close()
        connection = sqlite3.connect(catalog_path)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()

        memory = Memory(self.path)

        self.assertEqual(memory.find(ids=item_id)[0].message, "first")

    def test_find_does_not_read_git_history_twice(self):
        self.save("first")
        with patch.object(self.memory, "_run", wraps=self.memory._run) as run:
            self.memory.find()
            self.memory.find()
        history_calls = [
            call for call in run.call_args_list
            if call.args and call.args[0] == "log"
        ]
        self.assertEqual(history_calls, [])

    def test_k_bounds_results(self):
        self.save("first")
        self.save("second")
        self.assertEqual(len(self.memory.find(k=1)), 1)

    def test_time_filters(self):
        item_id = self.save("first")
        item = self.memory.find(ids=item_id)[0]
        self.assertEqual(self.memory.find(since=item.datetime)[0].id, item_id)
        self.assertEqual(
            self.memory.find(since=item.datetime + timedelta(seconds=1)),
            [],
        )

    def test_context_budget(self):
        self.save("a" * 20)
        self.save("b" * 20)
        items = self.memory.find(k=10, max_tokens=5, dedupe=False)
        self.assertEqual(len(items), 1)

    def test_deduplicates_equal_content(self):
        self.memory.save("same", allow_empty=True)
        self.memory.save("same", allow_empty=True)
        self.assertEqual(len(self.memory.find(k=10)), 1)
        self.assertEqual(len(self.memory.find(k=10, dedupe=False)), 2)

    def test_delete_hides_target(self):
        item_id = self.save("wrong")
        self.memory.delete(item_id)
        self.assertEqual(self.memory.find(ids=item_id), [])
        self.assertNotIn(item_id, [item.id for item in self.memory.state()])
        historical = self.memory.find(ids=item_id, include_historical=True)
        self.assertEqual(historical[0].id, item_id)

    def test_supersede_hides_target(self):
        old_id = self.save("old")
        new_id = self.memory.supersede(old_id, "correct")
        self.assertEqual(self.memory.find(ids=old_id), [])
        self.assertEqual(self.memory.find(ids=new_id)[0].message, "correct")

    def test_delete_is_branch_scoped(self):
        old_id = self.save("shared")
        self.memory.branch(old_id, "keep", summary="isolate delete")
        self.memory._run("checkout", "main")
        self.memory.delete(old_id)
        self.assertEqual(self.memory.find(ids=old_id), [])
        self.assertEqual(
            self.memory.find(ids=old_id, branch="keep")[0].message,
            "shared",
        )
        self.assertEqual(
            self.memory.find(ids=old_id, branch=None)[0].message,
            "shared",
        )

    def test_deleted_branch_is_not_globally_searchable(self):
        base = self.save("base")
        self.memory.branch(base, "temporary", summary="temp line")
        side_id = self.memory.save("side only", allow_empty=True)
        self.memory._run("checkout", "main")
        self.memory._run("branch", "-D", "temporary")
        self.assertEqual(self.memory.find(ids=side_id, branch=None), [])

    def test_supersede_payload_does_not_expose_internal_fields(self):
        old_id = self.save("old")
        new_id = self.memory.supersede(old_id, "new", {"kind": "fact"})
        self.assertEqual(
            self.memory.find(ids=new_id)[0].payload,
            {"kind": "fact", "agent": "agent"},
        )

    def test_negative_k_is_rejected(self):
        self.save("first")
        with self.assertRaises(ValueError):
            self.memory.find(k=-1)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic_query_is_bounded(self):
        self.save("matrix one")
        self.save("matrix two")
        self.memory._provider = FakeProvider()
        self.memory._search = None
        self.assertEqual(len(self.memory.find("matrix", k=1)), 1)

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic_payload_and_time_filters(self):
        first = self.save("matrix", {"kind": "keep"})
        self.memory.save("matrix", {"kind": "drop"}, allow_empty=True)
        timestamp = self.memory.find(
            ids=first,
            include_historical=True,
        )[0].datetime
        self.memory._provider = FakeProvider()
        self.memory._search = None
        items = self.memory.find(
            "matrix",
            payload={"kind": "keep"},
            since=timestamp,
        )
        self.assertEqual([item.id for item in items], [first])

    @unittest.skipUnless(HAS_HNSWLIB, "needs hnswlib")
    def test_semantic_id_filter_does_not_depend_on_ann_pool(self):
        ids = [
            self.memory.save(f"entry {index}", allow_empty=True)
            for index in range(60)
        ]
        self.memory._provider = FakeProvider()
        self.memory._search = None
        items = self.memory.find("matrix", ids=ids[0], k=1)
        self.assertEqual(items[0].id, ids[0])

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from agent_memory import Memory, call_tool
from agent_memory.core import HIDDEN_DIR
from agent_memory.goals import create_goal, list_goals, memory_for, resolve_goal
from agent_memory.tests.helpers import MemoryTestCase


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.test"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / ".gitignore").write_text(".agent_memory/\n")
    (path / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True
    )


class GoalSpaceTests(MemoryTestCase):
    def test_long_goal_keeps_full_text_and_has_clean_display_title(self) -> None:
        text = "Implement the crash-safe concurrent ledger while preserving every explicit requirement across compaction"
        record = create_goal(self.path, text)
        self.assertEqual(record["goal"], text)
        self.assertTrue(record["title"].endswith("…"))
        self.assertFalse(record["title"].endswith(" …"))

    def test_create_lists_and_isolates_stores(self) -> None:
        first = create_goal(self.path, "Fix login p95")
        second = create_goal(self.path, "Export invoices")
        self.assertEqual(len(first["id"]), 8)
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(Path(first["workspace"]).is_dir())
        self.assertTrue(
            (Path(first["workspace"]) / HIDDEN_DIR / "GOAL").read_text().startswith(
                first["id"]
            )
        )
        ids = {row["id"] for row in list_goals(self.path)}
        self.assertEqual(ids, {first["id"], second["id"]})

        Memory(first["workspace"]).save("login cache in auth.py")
        Memory(second["workspace"]).save("invoice csv writer")
        left = [item.message for item in Memory(first["workspace"]).state()]
        right = [item.message for item in Memory(second["workspace"]).state()]
        self.assertTrue(any("login cache" in text for text in left))
        self.assertFalse(any("login cache" in text for text in right))
        self.assertFalse(any("invoice csv" in text for text in left))

        project_notes = [item.message for item in Memory(self.path).state()]
        self.assertFalse(any("login cache" in text for text in project_notes))

    def test_inspect_one_goal_leaves_the_other_tree(self) -> None:
        first = create_goal(self.path, "Goal A")
        second = create_goal(self.path, "Goal B")
        work_a = Path(first["workspace"]) / "work.txt"
        work_b = Path(second["workspace"]) / "work.txt"
        work_a.write_text("A-one")
        checkpoint = Memory(first["workspace"]).save("A checkpoint")
        work_a.write_text("A-two")
        Memory(first["workspace"]).save("A later")
        work_b.write_text("B-keep")
        Memory(second["workspace"]).save("B checkpoint")

        Memory(first["workspace"]).inspect(checkpoint)
        self.assertEqual(work_a.read_text(), "A-one")
        self.assertEqual(work_b.read_text(), "B-keep")
        self.assertTrue(
            any(
                "B checkpoint" in item.message
                for item in Memory(second["workspace"]).state()
            )
        )
        Memory(first["workspace"]).inspect_done()

    def test_worktree_when_project_is_git(self) -> None:
        _git_init(self.path)
        record = create_goal(self.path, "Ship worktree goal")
        self.assertEqual(record["branch"], f"goal/{record['id']}")
        self.assertTrue((Path(record["workspace"]) / "app.py").is_file())
        self.assertTrue((Path(record["workspace"]) / ".git").exists())

    def test_goal_kwarg_routes_tools_off_the_project_notebook(self) -> None:
        record = create_goal(self.path, "Isolated tool goal")
        call_tool(
            self.memory,
            "memory",
            {"action": "save", "text": "stays on project"},
        )
        call_tool(
            self.memory,
            "memory",
            {
                "action": "save",
                "text": "lives on the goal",
                "goal": record["id"],
            },
        )
        project = [item.message for item in Memory(self.path).state()]
        goal_notes = [
            item.message for item in memory_for(self.path, record["id"]).state()
        ]
        self.assertTrue(any("stays on project" in text for text in project))
        self.assertFalse(any("lives on the goal" in text for text in project))
        self.assertTrue(any("lives on the goal" in text for text in goal_notes))

        listed = call_tool(self.memory, "memory", {"action": "goal_list"})
        self.assertIn(record["id"], listed)
        packet = call_tool(
            self.memory, "memory", {"action": "goal_use", "id": record["id"]}
        )
        self.assertIn(f"Goal [{record['id']}]", packet)
        self.assertIn("Workspace:", packet)

    def test_resolve_unknown_goal(self) -> None:
        with self.assertRaises(ValueError):
            resolve_goal(self.path, "deadbeef")
        goal_commit = self.memory.save_user("Build tx_engine")
        self.assertEqual(memory_for(self.path, goal_commit[:8])._path, self.path.resolve())


class GoalMcpTests(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        import agent_memory.cursor.common as common

        common._resolved_root = None

    def tearDown(self) -> None:
        import agent_memory.cursor.common as common

        common._resolved_root = None
        super().tearDown()

    def test_mcp_goal_param_and_project_default(self) -> None:
        try:
            from agent_memory.cursor.mcp import _build_server
        except SystemExit as error:
            self.skipTest(str(error))

        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            try:
                server = _build_server()
            except SystemExit as error:
                self.skipTest(str(error))
            names = set(server._tool_manager._tools)
            self.assertIn("memory_goal_create", names)
            self.assertIn("memory_goal_list", names)
            self.assertIn("memory_goal_use", names)
            created = server._tool_manager._tools["memory_goal_create"].fn(
                text="MCP goal"
            )
            self.assertIn("Created goal [", created)
            goal_id = created.split("Created goal [", 1)[1][:8]
            server._tool_manager._tools["memory_save"].fn(
                text="project notebook note"
            )
            server._tool_manager._tools["memory_save"].fn(
                text="goal notebook note", goal=goal_id
            )

        project = [item.message for item in Memory(self.path).state()]
        goal_notes = [
            item.message for item in memory_for(self.path, goal_id).state()
        ]
        self.assertTrue(any("project notebook note" in text for text in project))
        self.assertFalse(any("goal notebook note" in text for text in project))
        self.assertTrue(any("goal notebook note" in text for text in goal_notes))

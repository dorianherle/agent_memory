import json
import os
import sys
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.core import split_state
from agent_memory.cursor.hook import handle
from agent_memory.cursor.install import install_cursor
from agent_memory.tests.helpers import MemoryTestCase


class McpServerTests(MemoryTestCase):
    def test_build_server_registers_tools(self) -> None:
        from agent_memory.cursor.mcp import _build_server

        server = _build_server()
        names = set(server._tool_manager._tools)
        self.assertIn("memory_state", names)
        self.assertIn("memory_graph_data", names)
        self.assertIn("memory_summarize", names)


class MemoryRootTests(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        import agent_memory.cursor.common as common

        common._resolved_root = None

    def tearDown(self) -> None:
        import agent_memory.cursor.common as common

        common._resolved_root = None
        super().tearDown()

    def test_ignores_unexpanded_workspace_folder(self) -> None:
        from agent_memory.cursor.common import resolve_root

        with patch.dict(
            os.environ,
            {"AGENT_MEMORY_ROOT": "${workspaceFolder}"},
            clear=False,
        ):
            with patch("agent_memory.cursor.common.Path.cwd") as cwd:
                cwd.return_value = self.path
                self.assertEqual(resolve_root([]), self.path.resolve())

    def test_root_cli_arg_wins(self) -> None:
        from agent_memory.cursor.common import resolve_root

        other = self.path / "other"
        other.mkdir()
        self.assertEqual(
            resolve_root(["--root", str(other)]),
            other.resolve(),
        )

    def test_remember_root_survives_argv_strip(self) -> None:
        import agent_memory.cursor.common as common
        from agent_memory.cursor.mcp import _strip_root_argv

        other = self.path / "cached"
        other.mkdir()
        with patch.object(sys, "argv", ["prog", "--root", str(other)]):
            self.assertEqual(common.remember_root(), other.resolve())
            _strip_root_argv()
            self.assertEqual(sys.argv, ["prog"])
            self.assertEqual(common.memory_root(), other.resolve())


class GraphDataTests(MemoryTestCase):
    def test_graph_data_shape(self) -> None:
        first = self.save("first")
        self.save("second")
        summary = self.memory.summarize("did both")

        data = self.memory.graph_data()
        self.assertEqual(
            set(data), {"nodes", "edges", "kinds", "branches"}
        )
        self.assertEqual(
            set(data["kinds"]), {"summary", "user", "step", "delete"}
        )
        by_id = {node["id"]: node for node in data["nodes"]}
        self.assertIn(first, by_id)
        self.assertTrue(by_id[first]["folded"])
        self.assertEqual(by_id[summary]["kind"], "summary")
        self.assertIn(first, by_id[summary]["children"])
        self.assertTrue(any(branch["current"] for branch in data["branches"]))
        self.assertTrue(any(edge["kind"] == "fold" for edge in data["edges"]))
        # Parent edges appear once there is a visible chain outside a fold.
        later = self.save("after summary")
        data = self.memory.graph_data()
        self.assertTrue(
            any(
                edge["kind"] == "parent" and edge["to"] == later
                for edge in data["edges"]
            )
        )


class AutoSaveTests(MemoryTestCase):
    def test_auto_save_and_dedupe(self) -> None:
        first = self.memory.auto_save("done with checkout")
        second = self.memory.auto_save("done with checkout")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertTrue(self.memory.state()[-1].payload.get("auto"))

    def test_summarize_folds_recent(self) -> None:
        self.memory.save("step one")
        self.memory.save("step two")
        summary_id = self.memory.summarize("finished pairing steps")
        history, recent = split_state(self.memory.state())
        self.assertEqual(recent, [])
        self.assertEqual(history[-1].id, summary_id)
        self.assertEqual(len(history[-1].payload["covers"]), 2)

    def test_write_state_file(self) -> None:
        self.memory.save("note")
        path = self.memory.write_state_file()
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("note", content)
        self.assertIn("Recent", content)


class HookTests(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env = patch.dict(
            os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        super().tearDown()

    def test_before_submit_saves_user(self) -> None:
        handle("beforeSubmitPrompt", {"prompt": "why double charge?"})
        items = Memory(self.path).find(payload={"user": True}, k=1)
        self.assertEqual(items[0].message, "why double charge?")

    def test_after_response_auto_saves(self) -> None:
        handle("afterAgentResponse", {"text": "fixed the webhook retry"})
        last = Memory(self.path).state()[-1]
        self.assertEqual(last.message, "fixed the webhook retry")
        self.assertTrue(last.payload.get("auto"))

    def test_session_start_injects_context(self) -> None:
        self.memory.save("prior work")
        out = handle("sessionStart", {})
        self.assertIn("additional_context", out)
        self.assertIn("prior work", out["additional_context"])
        self.assertTrue((self.path / ".cursor" / "MEMORY_STATE.md").exists())


class InstallTests(MemoryTestCase):
    def test_install_merges_configs(self) -> None:
        cursor = self.path / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "other": {"command": "echo", "args": ["hi"]}
                    }
                }
            )
        )
        (cursor / "hooks.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "stop": [{"command": "echo stop"}],
                    },
                }
            )
        )

        written = install_cursor(self.path, python=sys.executable)
        mcp = json.loads(written["mcp"].read_text())
        self.assertIn("other", mcp["mcpServers"])
        self.assertIn("agent-memory", mcp["mcpServers"])
        self.assertEqual(
            mcp["mcpServers"]["agent-memory"]["args"],
            [
                "-m",
                "agent_memory.cursor.mcp",
                "--root",
                str(self.path.resolve()),
            ],
        )
        self.assertNotIn("env", mcp["mcpServers"]["agent-memory"])

        hooks = json.loads(written["hooks"].read_text())
        self.assertEqual(hooks["hooks"]["stop"], [{"command": "echo stop"}])
        for event in (
            "sessionStart",
            "beforeSubmitPrompt",
            "afterAgentResponse",
        ):
            commands = [e["command"] for e in hooks["hooks"][event]]
            self.assertTrue(
                any("agent_memory.cursor.hook" in c for c in commands)
            )

        self.assertTrue(written["rule"].exists())
        self.assertTrue(written["state"].exists())

    def test_install_is_idempotent(self) -> None:
        install_cursor(self.path, python=sys.executable)
        install_cursor(self.path, python=sys.executable)
        hooks = json.loads((self.path / ".cursor" / "hooks.json").read_text())
        for event in (
            "sessionStart",
            "beforeSubmitPrompt",
            "afterAgentResponse",
        ):
            matches = [
                e
                for e in hooks["hooks"][event]
                if "agent_memory.cursor.hook" in e["command"]
            ]
            self.assertEqual(len(matches), 1)

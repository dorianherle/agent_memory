import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from agent_memory import Memory
from agent_memory.core import split_state
from agent_memory.cursor.hook import handle
from agent_memory.cursor.install import install_cursor
from agent_memory.tests.helpers import MemoryTestCase


class McpServerTests(MemoryTestCase):
    def test_build_server_registers_tools(self) -> None:
        try:
            from agent_memory.cursor.mcp import _build_server
        except SystemExit as error:
            self.skipTest(str(error))

        try:
            server = _build_server()
        except SystemExit as error:
            self.skipTest(str(error))
        names = set(server._tool_manager._tools)
        self.assertNotIn("memory_state", names)
        self.assertIn("memory_save", names)
        self.assertIn("memory_goal_create", names)
        self.assertIn("memory_goal_list", names)
        self.assertIn("memory_goal_use", names)
        self.assertIn("memory_summarize", names)
        self.assertIn("memory_delete", names)
        self.assertIn("memory_open_questions", names)
        self.assertNotIn("memory_check", names)
        self.assertNotIn("memory_pin", names)
        self.assertIn("memory_history", names)
        self.assertNotIn("memory_write_canvas", names)
        self.assertNotIn("memory_graph_data", names)
        self.assertNotIn("memory_graph", names)

    def test_memory_save_tool_checkpoints(self) -> None:
        try:
            from agent_memory.cursor.mcp import _build_server
        except SystemExit as error:
            self.skipTest(str(error))

        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            try:
                server = _build_server()
            except SystemExit as error:
                self.skipTest(str(error))
            tool = server._tool_manager._tools["memory_save"]
            result = tool.fn(text="mid-turn finding: auth in src/auth.ts", agent="agent")
        self.assertIn("Saved [", result)
        self.assertIn("@agent", result)
        last = Memory(self.path).state()[-1]
        self.assertIn("auth in src/auth.ts", last.message)
        self.assertFalse(last.payload.get("auto"))
        self.assertEqual(last.payload.get("agent"), "agent")

    def test_memory_save_tool_accepts_payload_files_and_handoff(self) -> None:
        try:
            from agent_memory.cursor.mcp import _build_server
        except SystemExit as error:
            self.skipTest(str(error))

        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            server = _build_server()
            tool = server._tool_manager._tools["memory_save"]
            result = tool.fn(
                text="Handoff checkpoint\nNext: pytest -k auth",
                agent="agent",
                files=["work.txt"],
                handoff=True,
                payload={"tag": "cp"},
            )
        self.assertIn("Saved [", result)
        last = Memory(self.path).state()[-1]
        self.assertTrue(last.payload.get("handoff"))
        self.assertEqual(last.payload.get("files"), ["work.txt"])
        self.assertEqual(last.payload.get("tag"), "cp")

    def test_memory_delete_tool_hides_from_state(self) -> None:
        try:
            from agent_memory.cursor.mcp import _build_server
        except SystemExit as error:
            self.skipTest(str(error))

        noise = self.save("opened file X")
        with patch.dict(os.environ, {"AGENT_MEMORY_ROOT": str(self.path)}):
            try:
                server = _build_server()
            except SystemExit as error:
                self.skipTest(str(error))
            tool = server._tool_manager._tools["memory_delete"]
            result = tool.fn(ids=[noise[:8]])
        self.assertIn("Deleted [", result)
        self.assertIn(noise[:8], result)
        mem = Memory(self.path)
        self.assertNotIn(noise, [item.id for item in mem.state()])
        self.assertEqual(mem.find(ids=noise), [])


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


class WriteCanvasTests(MemoryTestCase):
    def test_write_canvas_is_instant_tsx(self) -> None:
        self.save("first step")
        self.memory.summarize("done first")
        out = self.path / "out"
        path = self.memory.write_canvas(out / "graph.canvas.tsx")
        text = path.read_text()
        self.assertTrue(path.exists())
        self.assertIn("useCanvasState", text)
        self.assertIn("drill-trail", text)
        self.assertIn("selected-entry", text)
        self.assertIn("Subgraph (", text)
        self.assertIn('"body"', text)
        self.assertIn("children", text)
        self.assertIn("first step", text)
        self.assertIn("Live", text)

    def test_write_live_html_creates_shell_and_data(self) -> None:
        self.save("first step")
        self.memory.summarize("done first")
        index = self.memory.write_live_html()
        data = index.parent / "data.js"
        self.assertTrue(index.exists())
        self.assertTrue(data.exists())
        shell = index.read_text()
        payload = data.read_text()
        self.assertIn("agent-memory-live-shell", shell)
        self.assertIn('content="24"', shell)
        self.assertIn('id="history"', shell)
        self.assertIn('id="state"', shell)
        self.assertIn('id="actions"', shell)
        self.assertIn("MEMORY STATE", shell)
        self.assertIn("memory_find", shell)
        self.assertIn("branch-tab", shell)
        self.assertIn("openInFinder", shell)
        self.assertIn("MCP tool catalog", shell)
        self.assertIn('"prompt"', payload)
        self.assertIn('"actions"', payload)
        self.assertIn("window.__AGENT_MEMORY_LIVE__", payload)
        self.assertIn("first step", payload)
        self.assertIn('"root"', payload)
        self.assertIn(str(self.path.resolve()), payload)
        # Second call refreshes data without requiring a rewrite of an
        # up-to-date shell; force_shell still rewrites.
        mtime = index.stat().st_mtime_ns
        self.save("second step")
        again = self.memory.write_live_html()
        self.assertEqual(again, index)
        self.assertIn("second step", data.read_text())
        forced = self.memory.write_live_html(force_shell=True)
        self.assertEqual(forced, index)
        self.assertGreaterEqual(index.stat().st_mtime_ns, mtime)

    def test_live_html_attaches_stale_files_and_keeps_goal_fresh(self) -> None:
        self.file.write_text("v1")
        goal = self.memory.save_user("understand the circuit")
        finding = self.save("verified circuit check")
        self.file.write_text("v2")
        packet = self.memory.render_packet("circuit")
        self.assertNotIn(f"[{goal[:8]}] [stale]", packet)
        self.assertIn(f"[{finding[:8]}] [stale]", packet)
        index = self.memory.write_live_html()
        payload = (index.parent / "data.js").read_text()
        self.assertIn('"stale_files"', payload)
        self.assertIn("work.txt", payload)

    def test_find_is_recorded_on_the_live_page(self) -> None:
        self.save("checkout retries duplicate the write")
        from agent_memory.tools import call_tool

        call_tool(
            self.memory,
            "memory",
            {"action": "find", "text": "checkout"},
        )
        index = self.memory.write_live_html()
        payload = (index.parent / "data.js").read_text()
        self.assertIn('"action": "find"', payload)
        self.assertIn("text=checkout", payload)
        self.assertIn("text=checkout", (index.parent / "actions.js").read_text())

    def test_write_live_demo_is_self_contained(self) -> None:
        from agent_memory.live_html import write_live_demo

        path = write_live_demo(self.path / "demo.html")
        text = path.read_text()
        self.assertIn("agent-memory · demo", text)
        self.assertIn('"demo": true', text)
        self.assertIn("retry", text)
        self.assertIn("pytest", text)
        self.assertIn("live_html.py", text)
        self.assertIn('id="state"', text)
        self.assertIn('id="actions"', text)
        self.assertIn("memory_find", text)
        self.assertIn("branch-tab", text)
        self.assertIn("explore", text)
        self.assertIn("query=login p95", text)
        self.assertNotIn('src="data.js"', text)

    def test_live_html_hides_deleted(self) -> None:
        noise = self.save("opened file X")
        self.save("real finding")
        self.memory.delete(noise)
        index = self.memory.write_live_html()
        payload = (index.parent / "data.js").read_text()
        self.assertNotIn(noise, payload)
        self.assertNotIn("opened file X", payload)
        self.assertIn("real finding", payload)

    def test_default_canvas_dir_slug(self) -> None:
        from agent_memory.canvas import default_canvas_dir, project_slug

        root = Path("/Users/me/proj")
        self.assertEqual(project_slug(root), "Users-me-proj")
        self.assertEqual(
            default_canvas_dir(root),
            Path.home()
            / ".cursor"
            / "projects"
            / "Users-me-proj"
            / "canvases",
        )

    def test_default_canvas_dir_prefers_live_ide_project(self) -> None:
        """When `_` vs `-` folders both exist, prefer the live IDE project."""
        import tempfile
        from unittest import mock

        from agent_memory.canvas import default_canvas_dir

        root = Path("/Users/me/agent_memory")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            projects = home / ".cursor" / "projects"
            underscored = projects / "Users-me-agent_memory"
            hyphenated = projects / "Users-me-agent-memory"
            (underscored / "canvases").mkdir(parents=True)
            (hyphenated / "canvases").mkdir(parents=True)
            (hyphenated / "terminals").mkdir()
            with mock.patch(
                "agent_memory.canvas.Path.home",
                return_value=home,
            ):
                self.assertEqual(
                    default_canvas_dir(root),
                    hyphenated / "canvases",
                )


class GraphDataTests(MemoryTestCase):
    def test_graph_data_shape(self) -> None:
        first = self.save("first")
        self.save("second")
        summary = self.memory.summarize("did both")

        data = self.memory.graph_data()
        self.assertEqual(
            set(data), {"nodes", "edges", "kinds", "branches", "agents"}
        )
        self.assertEqual(
            set(data["kinds"]),
            {"summary", "user", "step", "delete", "open_question"},
        )
        by_id = {node["id"]: node for node in data["nodes"]}
        self.assertIn(first, by_id)
        self.assertTrue(by_id[first]["folded"])
        self.assertEqual(by_id[summary]["kind"], "summary")
        self.assertEqual(by_id[summary]["agent"], "agent")
        self.assertIn(first, by_id[summary]["children"])
        self.assertTrue(any(a["name"] == "agent" for a in data["agents"]))
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

    def test_graph_data_hides_deleted(self) -> None:
        noise = self.save("opened file X")
        keeper = self.save("real finding")
        self.memory.delete(noise)
        data = self.memory.graph_data()
        ids = {node["id"] for node in data["nodes"]}
        self.assertNotIn(noise, ids)
        self.assertIn(keeper, ids)
        self.assertFalse(any(node["kind"] == "delete" for node in data["nodes"]))


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

    def test_before_submit_does_not_write_state_rule(self) -> None:
        out = handle("beforeSubmitPrompt", {"prompt": "why double charge?"})
        rule = self.path / ".cursor" / "rules" / "agent-memory-state.mdc"
        self.assertFalse(rule.exists())
        self.assertNotIn("additional_context", out)
        self.assertTrue(out.get("continue"))

    def test_before_submit_records_full_prompt_packet(self) -> None:
        # Install the static rule the hook reads into the packet.
        rules = self.path / ".cursor" / "rules"
        rules.mkdir(parents=True, exist_ok=True)
        (rules / "agent-memory.mdc").write_text(
            "---\nalwaysApply: true\n---\n# Agent Memory\nuse memory_save\n"
        )
        out = handle(
            "beforeSubmitPrompt",
            {"prompt": "exact goal for live html"},
        )
        live = self.path / ".agent_memory" / "live"
        data_js = (live / "data.js").read_text()
        turns = (live / "turn_prompts.json").read_text()
        self.assertIn("exact goal for live html", turns)
        self.assertIn("memory_save", turns)
        self.assertIn("MEMORY INSTRUCTIONS", turns)
        self.assertIn("ALWAYS-APPLY RULE", turns)
        self.assertIn("MCP TOOL CATALOG", turns)
        self.assertIn("exact goal for live html", data_js)
        self.assertIn('"instructions"', data_js)
        self.assertIn('"rule"', data_js)
        self.assertIn('"mcp_tools"', data_js)
        self.assertIn('"packet"', data_js)
        self.assertNotIn("additional_context", out)

    def test_after_response_does_not_save_conversation(self) -> None:
        handle("afterAgentResponse", {"text": "fixed the webhook retry"})
        self.assertEqual(Memory(self.path).state(), [])

    def test_only_first_prompt_establishes_goal(self) -> None:
        handle("beforeSubmitPrompt", {"prompt": "hey what's up?"})
        handle("beforeSubmitPrompt", {"prompt": "fix checkout"})
        handle("beforeSubmitPrompt", {"prompt": "so what were we discussing?"})
        items = Memory(self.path).state()
        self.assertEqual([item.message for item in items], ["fix checkout"])
        self.assertTrue(items[0].payload.get("goal"))

    def test_before_submit_captures_explicit_steering_constraint(self) -> None:
        handle("beforeSubmitPrompt", {"prompt": "fix checkout"})
        handle("beforeSubmitPrompt", {"prompt": "Constraint: keep the public REST API compatible"})
        mem = Memory(self.path)
        pkt = mem.render_packet("checkout")
        self.assertIn("## Goal", pkt)
        self.assertIn("fix checkout", pkt)
        self.assertIn("## Steering", pkt)
        self.assertIn("keep the public REST API compatible", pkt)

    def test_session_start_injects_context(self) -> None:
        self.memory.save("prior work")
        out = handle("sessionStart", {"session_id": "conv-1"})
        self.assertIn("additional_context", out)
        self.assertIn("@agent", out["additional_context"])
        self.assertIn("memory_save", out["additional_context"])
        self.assertEqual(out.get("env", {}).get("AGENT_MEMORY_AGENT"), "agent")
        self.assertFalse((self.path / ".cursor" / "MEMORY_STATE.md").exists())
        self.assertFalse((self.path / ".cursor" / "rules" / "agent-memory-state.mdc").exists())

    def test_subagent_start_and_stop_tag_agents(self) -> None:
        handle(
            "subagentStart",
            {
                "subagent_id": "abc-123456",
                "subagent_type": "explore",
                "task": "Find auth flow",
                "parent_conversation_id": "conv-parent",
            },
        )
        mem = Memory(self.path)
        spawn = mem.state()[-1]
        self.assertEqual(spawn.payload.get("agent"), "agent")
        self.assertIn("@explore·abc123", spawn.message)

        handle(
            "subagentStop",
            {
                "subagent_id": "abc-123456",
                "subagent_type": "explore",
                "status": "completed",
                "summary": "Auth lives in src/auth.ts",
                "description": "Exploring auth",
                "task": "Find auth flow",
            },
        )
        mem = Memory(self.path)
        done = mem.state()[-1]
        self.assertEqual(done.payload.get("agent"), "explore·abc123")
        self.assertIn("Auth lives in src/auth.ts", done.message)
        self.assertTrue(any(n.get("agent") == "explore·abc123" for n in mem.graph_data()["nodes"]))

    def test_auto_save_keeps_agents_distinct(self) -> None:
        self.memory.auto_save("same text", agent="agent")
        self.memory.auto_save("same text", agent="explore·x1")
        agents = [i.payload.get("agent") for i in self.memory.state()]
        self.assertEqual(agents[-2:], ["agent", "explore·x1"])


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

        written = install_cursor(
            self.path, python=sys.executable, skip_gemini=True, interactive=False
        )
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
            "subagentStart",
            "subagentStop",
        ):
            commands = [e["command"] for e in hooks["hooks"][event]]
            self.assertTrue(
                any("agent_memory.cursor.hook" in c for c in commands)
            )

        self.assertTrue(written["rule"].exists())
        self.assertIn("memory_save", written["rule"].read_text())
        self.assertTrue(written["skill"].exists())
        self.assertTrue(written["sdk_guide"].exists())
        guide = written["sdk_guide"].read_text()
        self.assertIn('setting_sources=["project"]', guide)
        self.assertIn("settingSources", guide)
        self.assertNotIn("state", written)
        self.assertTrue((self.path / ".gitignore").exists())
        self.assertIn(".agent_memory/", (self.path / ".gitignore").read_text())
        self.assertEqual(
            Path(mcp["mcpServers"]["agent-memory"]["command"]).resolve(),
            Path(sys.executable).resolve(),
        )

    def test_user_mcp_collision_warns_on_other_product(self) -> None:
        from agent_memory.cursor.install import user_mcp_collision

        user = self.path / "user-mcp.json"
        user.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "agent-memory": {"command": "/opt/old-agent-memory", "args": ["mcp"]}
                    }
                }
            )
        )
        msg = user_mcp_collision(user_mcp=user)
        self.assertIsNotNone(msg)
        self.assertIn("repo-bound", msg)
        self.assertIn("/opt/old-agent-memory", msg)

    def test_user_mcp_collision_ignores_same_product(self) -> None:
        from agent_memory.cursor.install import user_mcp_collision

        user = self.path / "user-mcp.json"
        user.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "agent-memory": {
                            "command": sys.executable,
                            "args": ["-m", "agent_memory.cursor.mcp", "--root", str(self.path)],
                        }
                    }
                }
            )
        )
        self.assertIsNone(user_mcp_collision(user_mcp=user))

    def test_install_wires_gemini_env(self) -> None:
        written = install_cursor(
            self.path,
            python=sys.executable,
            gemini_api_key="test-gemini-key",
            interactive=False,
        )
        mcp = json.loads(written["mcp"].read_text())
        env = mcp["mcpServers"]["agent-memory"]["env"]
        self.assertEqual(
            env["AGENT_MEMORY_PROVIDER"],
            "agent_memory.providers.gemini:GeminiProvider",
        )
        self.assertNotIn("GEMINI_API_KEY", env)
        cred = written["credentials"].read_text()
        self.assertIn("GEMINI_API_KEY=test-gemini-key", cred)
        from agent_memory.cursor.install import apply_credentials_env

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("AGENT_MEMORY_PROVIDER", None)
            apply_credentials_env(self.path)
            self.assertEqual(os.environ.get("GEMINI_API_KEY"), "test-gemini-key")
            self.assertIn("gemini:GeminiProvider", os.environ.get("AGENT_MEMORY_PROVIDER", ""))

    def test_install_refuses_silent_gemini_skip(self) -> None:
        from agent_memory.cursor.install import GeminiSetupError

        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            with self.assertRaises(GeminiSetupError):
                install_cursor(
                    self.path,
                    python=sys.executable,
                    interactive=False,
                    skip_gemini=False,
                )

    def test_install_uses_project_dotenv(self) -> None:
        (self.path / ".env").write_text("GEMINI_API_KEY=from-dotenv-key\n")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            written = install_cursor(
                self.path,
                python=sys.executable,
                interactive=False,
            )
        cred = written["credentials"].read_text()
        self.assertIn("GEMINI_API_KEY=from-dotenv-key", cred)

    def test_apply_credentials_falls_back_to_dotenv(self) -> None:
        from agent_memory.cursor.install import apply_credentials_env

        (self.path / ".env").write_text("GEMINI_API_KEY=dotenv-runtime-key\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            loaded = apply_credentials_env(self.path)
            self.assertEqual(os.environ.get("GEMINI_API_KEY"), "dotenv-runtime-key")
            self.assertEqual(loaded.get("GEMINI_API_KEY"), "dotenv-runtime-key")

    def test_install_is_idempotent(self) -> None:
        install_cursor(
            self.path, python=sys.executable, skip_gemini=True, interactive=False
        )
        install_cursor(
            self.path, python=sys.executable, skip_gemini=True, interactive=False
        )
        hooks = json.loads((self.path / ".cursor" / "hooks.json").read_text())
        for event in (
            "sessionStart",
            "beforeSubmitPrompt",
            "afterAgentResponse",
            "subagentStart",
            "subagentStop",
        ):
            matches = [
                e
                for e in hooks["hooks"][event]
                if "agent_memory.cursor.hook" in e["command"]
            ]
            self.assertEqual(len(matches), 1)


class SkillTests(MemoryTestCase):
    """The shipped skill is the agent's manual; keep it discoverable."""

    TOOLS = (
        "memory_save",
        "memory_summarize",
        "memory_open_questions",
        "memory_delete",
        "memory_find",
        "memory_history",
        "memory_state",
        "memory_branch",
        "memory_inspect",
        "memory_inspect_done",
        "memory_goal_create",
        "memory_goal_list",
        "memory_goal_use",
    )

    def test_install_writes_skill_with_references(self) -> None:
        written = install_cursor(
            self.path, python=sys.executable, skip_gemini=True, interactive=False
        )
        skill = written["skill"]
        self.assertEqual(
            skill,
            (self.path / ".cursor" / "skills" / "agent-memory" / "SKILL.md").resolve(),
        )
        body = skill.read_text()
        for name in self.TOOLS:
            self.assertIn(name, body)
        for reference in ("playbooks.md", "internals.md"):
            self.assertIn(reference, body)
            self.assertTrue((skill.parent / reference).exists())

    def test_skill_frontmatter_is_auto_invocable(self) -> None:
        from agent_memory.cursor.install import SKILL_SOURCE

        body = (SKILL_SOURCE / "SKILL.md").read_text()
        header = body.split("---")[1]
        self.assertIn("name: agent-memory", header)
        self.assertIn("description:", header)
        # Auto-invocation is the point: the agent should not have to be told.
        self.assertNotIn("disable-model-invocation", body)
        # Cursor reads the whole file every time it fires.
        self.assertLess(len(body.splitlines()), 500)

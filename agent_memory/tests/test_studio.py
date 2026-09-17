import json
import threading
from http.client import HTTPConnection

from agent_memory.studio import Handler, SESSION, ThreadingHTTPServer, memory_view
from agent_memory.tests.helpers import MemoryTestCase


class StudioTests(MemoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        from unittest.mock import patch
        from agent_memory.studio_runtime import StandbyAgent
        self.warm_patch = patch('agent_memory.studio._warm_next')
        self.warm_patch.start()
        client_patch = patch('agent_memory.studio_runtime.workspace_client')
        client_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(self.warm_patch.stop)
        self.pool_patch = patch('agent_memory.studio.STANDBY', StandbyAgent())
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)
        self.codex_patch = patch("agent_memory.runner.codex_logged_in", return_value=False)
        self.codex_patch.start()
        self.addCleanup(self.codex_patch.stop)
        import agent_memory.studio as studio

        studio.RUN_WORKER = None
        self.addCleanup(lambda: setattr(studio, "RUN_WORKER", None))
        SESSION.update(
            {
                "api_key": "",
                "project": "",
                "model": "composer-2.5",
                "agent": None,
                "run": None,
                "cancel": False,
                "goal_id": "",
            }
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        data = json.loads(raw) if raw else {}
        return response.status, data

    def test_open_new_folder_prepares_memory_and_preserves_other_mcp(self) -> None:
        from pathlib import Path
        root = self.path / "fresh-studio-project"
        (root / ".cursor").mkdir(parents=True)
        config = root / ".cursor" / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"existing": {"command": "keep-me"}}}))
        status, data = self._json("POST", "/api/open", {"project": str(root)})
        self.assertEqual(status, 200)
        self.assertTrue(data["ready"])
        merged = json.loads(config.read_text())["mcpServers"]
        self.assertEqual(merged["existing"], {"command": "keep-me"})
        self.assertIn("agent-memory", merged)
        self.assertTrue((root / ".cursor" / "hooks.json").exists())
        self.assertTrue((root / ".cursor" / "MEMORY_STATE.md").exists())
        status, again = self._json("POST", "/api/open", {"project": str(root)})
        self.assertTrue(again["ready"])

    def test_page_and_memory(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        html = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertIn("One board", html)
        self.assertIn("Open folder", html)
        self.assertIn("key-ok", html)
        self.assertNotIn('id="token-saved"', html)
        self.assertNotIn('id="show-all"', html)
        self.assertIn('id="prompt-dialog"', html)
        self.assertIn("view prompt", html)
        self.assertNotIn("Open a project path", html)
        self.assertIn("Goal", html)
        self.assertNotIn("+ Summary", html)
        self.assertNotIn("summary-form", html)

        status, data = self._json("GET", "/api/memory")
        self.assertEqual(status, 400)

        status, data = self._json(
            "POST", "/api/open", {"project": str(self.path), "api_key": "cursor_test"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["project"], str(self.path.resolve()))

        self.save("Login loads the whole users table every time")
        self.memory.save_user("Why is login so slow?")
        view = memory_view(str(self.path))
        self.assertIn("login", (view["goal"] or {}).get("text", "").lower())
        self.assertTrue(view["commits"])
        status, data = self._json("GET", "/api/memory")
        self.assertEqual(status, 200)
        self.assertIn("Goal", data["packet"])
        self.assertNotIn("Steering", data["packet"])

        from unittest.mock import patch

        with patch("agent_memory.studio.pick_folder", return_value=str(self.path)):
            status, data = self._json("POST", "/api/pick", {"api_key": "cursor_test"})
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], self.path.name)

        SESSION["api_key"] = ""
        status, data = self._json("GET", "/api/session")
        self.assertFalse(data["has_key"])
        self.assertEqual(data["worker"], "none")
        with patch("agent_memory.studio.validate_key", return_value=None):
            status, data = self._json("POST", "/api/key", {"api_key": "cursor_test"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        status, data = self._json("GET", "/api/session")
        self.assertTrue(data["has_key"])
        self.assertEqual(data["worker"], "cursor")

    def test_summary_children_and_branch_scope(self):
        goal = self.memory.save_user('Build a reliable notebook')
        first = self.save('Search works\nVerified literal matching.')
        second = self.save('Snapshots restore correctly')
        summary = self.memory.summarize('Search and snapshots verified')
        view = memory_view(str(self.path))
        top = {c['id']: c for c in view['commits']}
        self.assertIn(goal[:8], top)
        self.assertNotIn(first[:8], top)
        self.assertEqual([c['id'] for c in top[summary[:8]]['children']], [first[:8], second[:8]])
        self.assertIn('Verified literal matching.', top[summary[:8]]['children'][0]['text'])
        # A summary on another branch must not hide this branch's ancestor.
        self.memory._run('branch', 'unfolded', second)
        self.memory._run('symbolic-ref', 'HEAD', 'refs/heads/unfolded')
        view = memory_view(str(self.path))
        self.assertIn(first[:8], [c['id'] for c in view['commits']])
        self.assertNotIn(summary[:8], [c['id'] for c in view['commits']])

    def test_empty_path_rejected(self):
        status, _ = self._json('POST', '/api/open', {'project': ''})
        self.assertEqual(status, 400)

    def test_sdk_loads_project_settings_and_streams(self):
        from types import SimpleNamespace as N
        from unittest.mock import Mock, patch
        from agent_memory.studio import _ask
        SESSION.update(api_key='test', project=str(self.path))
        run = Mock()
        run.events.return_value = [N(sdk_message=N(type='status', message='Working', status='RUNNING')), N(sdk_message=N(type='assistant', message=N(content=[{'type':'text','text':'Complete'}])))]
        run.wait.return_value = N(status='finished', result='Complete')
        run.supports.return_value = False
        agent = Mock()
        agent.send.return_value = run
        events = []
        with patch('cursor_sdk.Agent.create', return_value=agent) as create, patch('agent_memory.studio.project_ready', return_value=True):
            _ask('Verify memory', events.append)
        self.assertEqual(create.call_args.kwargs['local'].setting_sources, [])
        prompt = agent.send.call_args.args[0]
        prompt_event = next(e for e in events if e["type"] == "prompt")
        self.assertEqual(prompt_event["text"], prompt)
        self.assertGreater(prompt_event["prompt_tokens"], prompt_event["memory_tokens"])
        self.assertGreater(prompt_event["memory_tokens"], 0)
        self.assertIn('memory_summarize', prompt)
        self.assertIn('no tools, skill reads, project recap', prompt)
        self.assertIn('During requested project work', prompt)
        self.assertEqual(''.join(e['text'] for e in events if e['type']=='delta').strip(), 'Complete')
        view = memory_view(str(self.path))
        self.assertEqual(view['goal']['text'], 'Verify memory')
        self.assertFalse(any(c['kind'] == 'answer' for c in view['commits']))
        self.assertIn('Verify memory', prompt)
        self.assertNotIn('Respond only at the scope', view['goal']['text'])
        self.assertTrue(any(e.get('stream_protocol') == 2 for e in events))
        self.assertEqual(events[-1]['status'], 'finished')
        self.assertIn('agent_wait_ms', events[-1]['timings'])
        agent.close.assert_called_once()
        self.assertIsNone(SESSION['agent'])

    def test_incremental_text_arrives_before_completed_message_without_duplicates(self):
        from types import SimpleNamespace as N
        from unittest.mock import Mock, patch
        from agent_memory.studio import _ask
        SESSION.update(api_key="test", project=str(self.path))
        output = []
        run = Mock()
        def events():
            yield N(sdk_message=None, interaction_update=N(type="text-delta", text="Hel"))
            self.assertIn({"type": "delta", "text": "Hel"}, output)
            yield N(sdk_message=None, interaction_update=N(type="text-delta", text="lo"))
            yield N(sdk_message=N(type="assistant", message=N(content=[{"type": "text", "text": "Hello!"}])))
        run.events.side_effect = events
        run.wait.return_value = N(status="finished", result="Hello!")
        agent = Mock()
        agent.send.return_value = run
        with patch("cursor_sdk.Agent.create", return_value=agent), patch("agent_memory.studio.project_ready", return_value=True):
            _ask("Hi", output.append)
        self.assertEqual([e["text"] for e in output if e["type"] == "delta"], ["Hel", "lo", "!"])

    def test_completed_assistant_fragments_keep_their_original_spacing(self):
        from types import SimpleNamespace as N
        from unittest.mock import Mock, patch
        from agent_memory.studio import _ask
        SESSION.update(api_key="test", project=str(self.path))
        run = Mock()
        run.events.return_value = [
            N(sdk_message=N(type="assistant", message=N(content=[{"type": "text", "text": text}])))
            for text in ["Hi", " —", " what’s", " up", "?"]
        ]
        run.wait.return_value = N(status="finished", result="Hi — what’s up?")
        agent = Mock()
        agent.send.return_value = run
        output = []
        with patch("cursor_sdk.Agent.create", return_value=agent), patch("agent_memory.studio.project_ready", return_value=True):
            _ask("Hi", output.append)
        answer = "".join(e["text"] for e in output if e["type"] == "delta")
        self.assertEqual(answer, "Hi — what’s up?")
        view = memory_view(str(self.path))
        self.assertEqual([c["text"] for c in view["commits"]], ["Hi"])

    def test_concurrent_ask_does_not_replace_active_run(self):
        from agent_memory.studio import _ask, RUN_LOCK
        events=[]
        with RUN_LOCK:
            _ask('Second request', events.append)
            status, data = self._json('POST', '/api/open', {'project':str(self.path)})
        self.assertEqual(status, 409)
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('already running', events[0]['message'])

    def test_failed_sdk_creation_releases_run_slot(self):
        from unittest.mock import patch
        from agent_memory.studio import _ask, RUN_LOCK
        SESSION.update(api_key='test', project=str(self.path))
        events=[]
        with patch('cursor_sdk.Agent.create', side_effect=RuntimeError('Model unavailable')), patch('agent_memory.studio.project_ready', return_value=True):
            _ask('Try a request', events.append)
        self.assertEqual(events[-1], {'type':'error','message':'Model unavailable'})
        self.assertFalse(RUN_LOCK.locked())
        self.assertIsNone(SESSION['agent'])


    def test_compact_overview_etag_and_lazy_note(self):
        from unittest.mock import patch
        from agent_memory.studio import memory_revision, VIEW_CACHE
        SESSION['project'] = str(self.path)
        first = self.save('First useful finding\n' + 'Detailed evidence. ' * 200)
        status, compact = self._json('GET', '/api/memory?compact=1')
        self.assertEqual(status, 200)
        self.assertNotIn('text', compact['commits'][0])
        status, detail = self._json('GET', '/api/note?id=' + first[:8])
        self.assertIn('Detailed evidence.', detail['text'])
        revision = memory_revision(str(self.path))
        conn = HTTPConnection('127.0.0.1', self.port, timeout=5)
        with patch('agent_memory.studio.memory_view', side_effect=AssertionError('must not rebuild')):
            conn.request('GET', '/api/memory?compact=1', headers={'If-None-Match': revision})
            response = conn.getresponse()
            self.assertEqual(response.status, 304)
            self.assertEqual(response.read(), b'')
        conn.close()
        self.save('New finding')
        self.assertNotEqual(memory_revision(str(self.path)), revision)
        _, fresh = self._json('GET', '/api/memory?compact=1')
        self.assertEqual(fresh['commits'][0]['title'], 'New finding')

    def test_slow_retirement_does_not_block_next_turn_or_session(self):
        from types import SimpleNamespace as N
        from unittest.mock import Mock, patch
        from time import perf_counter
        from agent_memory.studio import _ask, RUN_LOCK
        SESSION.update(api_key='test', project=str(self.path))
        close_started, release_close = threading.Event(), threading.Event()
        agent = Mock()
        def slow_close():
            close_started.set()
            release_close.wait(3)
        agent.close.side_effect = slow_close
        run = Mock()
        run.events.return_value = []
        run.wait.return_value = N(status='finished', result='Done')
        agent.send.return_value = run
        events = []
        def receive(event):
            if event['type'] == 'done':
                self.assertFalse(RUN_LOCK.locked())
            events.append(event)
        try:
            with patch('agent_memory.studio.STANDBY.take', return_value=(agent, {})), patch('agent_memory.studio.project_ready', return_value=True):
                start = perf_counter()
                _ask('A short test', receive)
                elapsed = perf_counter() - start
            self.assertTrue(close_started.wait(1))
            self.assertLess(elapsed, 1)
            status, _ = self._json('GET', '/api/session')
            self.assertEqual(status, 200)
            self.assertEqual(events[-1]['type'], 'done')
        finally:
            release_close.set()


    def test_failed_run_keeps_prompt_but_does_not_save_error_as_answer(self):
        from types import SimpleNamespace as N
        from unittest.mock import Mock, patch
        from agent_memory.studio import _ask
        SESSION.update(api_key='test', project=str(self.path))
        run = Mock()
        run.events.return_value = [N(sdk_message=N(type='status', message='Quota exceeded', status='ERROR'))]
        run.wait.return_value = N(status='error', result='Quota exceeded')
        agent = Mock()
        agent.send.return_value = run
        events = []
        with patch('cursor_sdk.Agent.create', return_value=agent), patch('agent_memory.studio.project_ready', return_value=True):
            _ask('Build a snake game', events.append)
        view = memory_view(str(self.path))
        self.assertEqual(view['goal']['text'], 'Build a snake game')
        self.assertEqual(len(view['commits']), 1)
        self.assertEqual(events[-1]['status'], 'error')
        self.assertTrue(any(e['type'] == 'error' for e in events))

    def _ndjson(self, path: str, payload: dict) -> tuple[int, list]:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=20)
        body = json.dumps(payload).encode()
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read().decode()
        conn.close()
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        return response.status, events

    def test_run_board_with_fake_worker(self) -> None:
        from pathlib import Path
        import agent_memory.studio as studio

        def worker(prompt: str, cwd: Path) -> None:
            tests = cwd / "tests" / "test_goal.py"
            tests.parent.mkdir(parents=True, exist_ok=True)
            app = cwd / "app.py"
            if "acceptance criteria" in prompt:
                if not app.exists():
                    app.write_text("VALUE = 1\n")
                tests.write_text(
                    "from app import VALUE\n\ndef test_value():\n    assert VALUE == 2\n"
                )
            else:
                app.write_text("VALUE = 2\n")

        studio.RUN_WORKER = worker
        status, opened = self._json("POST", "/api/open", {"project": str(self.path)})
        self.assertEqual(status, 200)
        status, events = self._ndjson("/api/run", {"text": "VALUE must be 2"})
        self.assertEqual(status, 200)
        observes = [e for e in events if e.get("type") == "observe"]
        self.assertTrue(observes)
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["status"], "passed")
        self.assertEqual(done["result"]["agent"], 1)
        self.assertTrue(done["result"]["pytest"]["ok"])
        self.assertTrue(done["result"]["goal_id"])
        status, goals = self._json("GET", "/api/goals")
        self.assertEqual(status, 200)
        self.assertEqual(goals["goals"][0]["id"], done["result"]["goal_id"])
        status, session = self._json("GET", "/api/session")
        self.assertEqual(session["worker"], "test")
        self.assertEqual(session["goal_id"], done["result"]["goal_id"])

    def test_connect_codex_spawns_login(self) -> None:
        from unittest.mock import patch

        with patch("agent_memory.runner.start_codex_login", return_value={"ok": True, "hint": "Finish login"}):
            status, data = self._json("POST", "/api/connect-codex", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("login", data["hint"].lower())

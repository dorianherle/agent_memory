from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_memory.core import Memory
from agent_memory.runner import (
    CODEX_MODELS_FALLBACK,
    codex_command,
    codex_worker,
    detect_worker,
    list_codex_models,
    run_goal,
    start_codex_login,
)
from agent_memory.tests.helpers import MemoryTestCase


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class RunnerTests(MemoryTestCase):
    def test_fake_agents_go_red_then_green(self) -> None:
        def worker(prompt: str, cwd: Path) -> None:
            tests = cwd / "tests" / "test_goal.py"
            app = cwd / "app.py"
            if "acceptance criteria" in prompt:
                if not app.exists():
                    app.write_text("VALUE = 1\n")
                _write(
                    tests,
                    "from app import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
                )
            else:
                app.write_text("VALUE = 2\n")

        status = run_goal(
            self.path,
            "VALUE must be 2",
            worker=worker,
            max_agents=4,
        )
        self.assertEqual(status["state"], "passed")
        self.assertEqual(status["agent"], 1)
        self.assertTrue(status["pytest"]["ok"])
        notes = [
            item.message
            for item in Memory(status["workspace"]).state()
        ]
        self.assertTrue(any("pytest fail" in text for text in notes))
        self.assertTrue(any("pytest pass" in text for text in notes))

    def test_vacuous_green_bootstrap_respawns(self) -> None:
        calls = {"n": 0}

        def worker(prompt: str, cwd: Path) -> None:
            calls["n"] += 1
            tests = cwd / "tests" / "test_goal.py"
            if calls["n"] == 1:
                _write(tests, "def test_ok():\n    assert True\n")
                return
            if "acceptance criteria" in prompt:
                (cwd / "app.py").write_text("VALUE = 1\n")
                _write(
                    tests,
                    "from app import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
                )
                return
            (cwd / "app.py").write_text("VALUE = 2\n")

        status = run_goal(
            self.path,
            "VALUE must be 2",
            worker=worker,
            max_agents=4,
        )
        self.assertGreaterEqual(calls["n"], 3)
        self.assertEqual(status["state"], "passed")
        self.assertGreaterEqual(status["agent"], 2)


class WorkerDetectTests(MemoryTestCase):
    def test_prefers_codex_when_logged_in(self) -> None:
        name, worker = detect_worker(
            "",
            which=lambda _: "/usr/bin/codex",
            run=lambda *a, **k: SimpleNamespace(returncode=0),
        )
        self.assertEqual(name, "codex")
        self.assertIs(worker, codex_worker)

    def test_cursor_key_when_codex_missing(self) -> None:
        name, worker = detect_worker(
            "cursor_test",
            which=lambda _: None,
            run=lambda *a, **k: SimpleNamespace(returncode=1),
        )
        self.assertEqual(name, "cursor")
        self.assertTrue(callable(worker))

    def test_none_without_login(self) -> None:
        with patch.dict("os.environ", {"CURSOR_API_KEY": ""}, clear=False):
            name, worker = detect_worker(
                "",
                which=lambda _: None,
                run=lambda *a, **k: SimpleNamespace(returncode=1),
            )
        self.assertEqual(name, "none")
        self.assertIsNone(worker)

    def test_codex_command_shape(self) -> None:
        cwd = self.path
        seen = {}

        def fake_run(command, cwd=None):
            seen["command"] = command
            seen["cwd"] = cwd
            return SimpleNamespace(returncode=0)

        prompt = "write failing tests"
        command = codex_command(prompt, cwd, model="gpt-5.6-luna")
        self.assertEqual(command[:5], ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write"])
        self.assertIn("--model", command)
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn(str(cwd), command)
        self.assertEqual(command[-1], prompt)
        codex_worker(prompt, cwd, model="gpt-5.6-luna", run=fake_run)
        self.assertEqual(seen["command"], command)
        self.assertEqual(seen["cwd"], str(cwd))

    def test_list_codex_models_parses_debug_and_falls_back(self) -> None:
        import json

        payload = json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-5.6-luna",
                        "display_name": "GPT-5.6 Luna",
                        "visibility": "list",
                    },
                    {"slug": "hidden-lab", "visibility": "hidden"},
                ]
            }
        )
        rows = list_codex_models(
            run=lambda *a, **k: SimpleNamespace(stdout=payload, returncode=0)
        )
        self.assertEqual(rows, [{"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna"}])
        rows = list_codex_models(run=lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
        self.assertEqual(rows, [dict(row) for row in CODEX_MODELS_FALLBACK])

    def test_connect_reports_dead_cli(self) -> None:
        with patch("agent_memory.runner.codex_auth_present", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                start_codex_login(
                    which=lambda _: "/usr/bin/codex",
                    run=lambda *a, **k: SimpleNamespace(
                        returncode=1, stderr="ENOENT", stdout=""
                    ),
                    popen=lambda *a, **k: None,
                    wait=lambda _: None,
                )
        self.assertIn("broken", str(ctx.exception).lower())

    def test_connect_already_signed_in(self) -> None:
        with patch("agent_memory.runner.codex_auth_present", return_value=True):
            result = start_codex_login(
                which=lambda _: "/usr/bin/codex",
                run=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout="ok"),
            )
        self.assertIn("Already signed in", result["hint"])

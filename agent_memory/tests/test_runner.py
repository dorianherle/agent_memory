from pathlib import Path

from agent_memory.core import Memory
from agent_memory.runner import run_goal
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

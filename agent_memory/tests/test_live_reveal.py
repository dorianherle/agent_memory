import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from agent_memory import Memory
from agent_memory.canvas import project_slug
from agent_memory.live_reveal import (
    ensure_reveal_server,
    now_state,
    reveal_path,
    reveal_url,
    running_processes,
    working_files,
)


class TestLiveReveal(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.live_dir = Path(self.temporary.name) / "live"
        self.live_dir.mkdir(parents=True)
        self._prev = os.environ.pop("AGENT_MEMORY_NO_REVEAL", None)

    def tearDown(self) -> None:
        pid_file = self.live_dir / ".reveal_pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 15)
            except (OSError, ValueError):
                pass
        if self._prev is None:
            os.environ.pop("AGENT_MEMORY_NO_REVEAL", None)
        else:
            os.environ["AGENT_MEMORY_NO_REVEAL"] = self._prev
        self.temporary.cleanup()

    def test_no_reveal_env_skips(self) -> None:
        os.environ["AGENT_MEMORY_NO_REVEAL"] = "1"
        self.assertIsNone(ensure_reveal_server(self.live_dir))

    def test_ensure_starts_and_health(self) -> None:
        url = ensure_reveal_server(self.live_dir)
        self.assertIsNotNone(url)
        self.assertTrue(str(url).endswith("/reveal"))
        self.assertEqual(reveal_url(self.live_dir), url)
        again = ensure_reveal_server(self.live_dir)
        self.assertEqual(again, url)

    def test_reveal_path_uses_platform_opener(self) -> None:
        sample = self.live_dir / "plot.png"
        sample.write_bytes(b"png")
        with mock.patch("agent_memory.live_reveal.subprocess.run") as run:
            reveal_path(sample)
            run.assert_called()
            args = run.call_args[0][0]
            if sys.platform == "darwin":
                self.assertEqual(args[:2], ["open", "-R"])
                self.assertEqual(args[2], str(sample.resolve()))
            elif sys.platform == "win32":
                self.assertEqual(args[0], "explorer")
            else:
                self.assertEqual(args[0], "xdg-open")

    def test_reveal_endpoint_missing_is_404(self) -> None:
        url = ensure_reveal_server(self.live_dir)
        self.assertIsNotNone(url)
        missing = self.live_dir / "nope" / "missing.png"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{url}?path={missing}", timeout=2)
        self.assertEqual(ctx.exception.code, 404)

    def test_reveal_path_missing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            reveal_path(self.live_dir / "nope" / "missing.png")

    def test_working_lists_dirty_files(self) -> None:
        root = Path(self.temporary.name) / "proj"
        root.mkdir()
        memory = Memory(root)
        tracked = root / "tracked.py"
        tracked.write_text("v1")
        memory.save("base")
        tracked.write_text("v2")
        (root / "new.py").write_text("new")
        payload = working_files(root)
        self.assertTrue(payload["ok"])
        paths = {item["path"] for item in payload["files"]}
        self.assertIn("tracked.py", paths)
        self.assertIn("new.py", paths)

        url = ensure_reveal_server(self.live_dir, root=root)
        self.assertIsNotNone(url)
        working = str(url).rsplit("/", 1)[0] + "/working"
        with urllib.request.urlopen(working, timeout=2) as resp:
            body = json.loads(resp.read().decode())
        self.assertTrue(body["ok"])
        self.assertIn("tracked.py", {item["path"] for item in body["files"]})
        self.assertIn("processes", body)
        self.assertIn("inspecting", body)

    def test_running_processes_from_cursor_terminals(self) -> None:
        root = Path(self.temporary.name) / "proj"
        root.mkdir()
        projects = Path(self.temporary.name) / "projects"
        term = projects / project_slug(root) / "terminals"
        term.mkdir(parents=True)
        (term / "live.txt").write_text(
            "---\n"
            "pid: 9\n"
            f'cwd: "{root}"\n'
            "command: pytest -q\n"
            "running_for_ms: 1200\n"
            "---\n",
            encoding="utf-8",
        )
        (term / "done.txt").write_text(
            "---\n"
            "pid: 8\n"
            f"cwd: {root}\n"
            "last_command: ls\n"
            "last_exit_code: 0\n"
            "---\n",
            encoding="utf-8",
        )
        (term / "finished.txt").write_text(
            "---\n"
            "pid: 7\n"
            f"cwd: {root}\n"
            "command: echo done\n"
            "running_for_ms: 40\n"
            "---\noutput\n---\nexit_code: 0\nelapsed_ms: 50\n---\n",
            encoding="utf-8",
        )
        stale = term / "stale.txt"
        stale.write_text(
            "---\n"
            "pid: 6\n"
            f"cwd: {root}\n"
            "command: sleep 999\n"
            "running_for_ms: 999999\n"
            "---\n",
            encoding="utf-8",
        )
        os.utime(stale, (0, 0))
        rows = running_processes(root, projects=projects)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["command"], "pytest -q")
        self.assertEqual(rows[0]["pid"], 9)

    def test_now_state_includes_inspecting(self) -> None:
        root = Path(self.temporary.name) / "proj"
        root.mkdir()
        Memory(root)
        inspect = root / ".agent_memory" / "INSPECTING"
        inspect.write_text(
            json.dumps({"branch": "main", "id": "abc12345"}),
            encoding="utf-8",
        )
        payload = now_state(root)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["inspecting"]["branch"], "main")
        self.assertEqual(payload["processes"], [])


if __name__ == "__main__":
    unittest.main()

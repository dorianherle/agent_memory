import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from agent_memory.live_reveal import ensure_reveal_server, reveal_path, reveal_url


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


if __name__ == "__main__":
    unittest.main()

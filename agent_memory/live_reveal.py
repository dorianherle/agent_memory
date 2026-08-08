"""Tiny localhost helper so the live HTML monitor can Reveal-in-Finder.

Browsers cannot run ``open -R`` from a static page. When the live monitor is
written, we ensure a loopback server is up; clicks POST/GET a path and this
process reveals it in the system file manager.

- macOS: ``open -R``
- Linux: ``xdg-open`` on the parent directory
- Windows: ``explorer /select,``
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

PORT_FILE = ".reveal_port"
PID_FILE = ".reveal_pid"
DEFAULT_HOST = "127.0.0.1"
# Prefer a stable high port; fall back if busy.
PORT_CANDIDATES = (18765, 18766, 18767, 27654)


def reveal_url(live_dir: Path) -> str | None:
    """Return ``http://127.0.0.1:PORT/reveal`` if the helper is healthy."""
    port = _read_port(live_dir)
    if port is None:
        return None
    base = f"http://{DEFAULT_HOST}:{port}"
    if not _healthy(base):
        return None
    return f"{base}/reveal"


def ensure_reveal_server(live_dir: Path) -> str | None:
    """Start the reveal helper if needed; return the reveal endpoint URL."""
    if os.environ.get("AGENT_MEMORY_NO_REVEAL"):
        return None
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    existing = reveal_url(live_dir)
    if existing:
        return existing

    python = sys.executable
    log = live_dir / ".reveal.log"
    with open(log, "a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [python, "-m", "agent_memory.live_reveal", "--dir", str(live_dir)],
            cwd=str(live_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (live_dir / PID_FILE).write_text(str(proc.pid), encoding="utf-8")

    for _ in range(40):
        time.sleep(0.05)
        url = reveal_url(live_dir)
        if url:
            return url
    return None


def reveal_path(path: str | Path) -> None:
    """Reveal ``path`` in the platform file manager (must exist)."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        # Still try parent if the leaf was deleted; otherwise raise.
        if target.parent.exists():
            target = target.parent
        else:
            raise FileNotFoundError(path)

    system = sys.platform
    if system == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
    elif system == "win32":
        subprocess.run(["explorer", f"/select,{target}"], check=False)
    else:
        folder = target if target.is_dir() else target.parent
        subprocess.run(["xdg-open", str(folder)], check=False)


def _read_port(live_dir: Path) -> int | None:
    path = Path(live_dir) / PORT_FILE
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _healthy(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=0.3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    live_dir: Path = Path(".")

    def log_message(self, format, *args):  # noqa: A003
        return

    def _cors(self) -> None:
        # Live page is often opened as file:// — allow loopback calls.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True})
            return
        if parsed.path == "/reveal":
            qs = urllib.parse.parse_qs(parsed.query)
            raw = (qs.get("path") or [""])[0]
            if not raw:
                self._json(400, {"ok": False, "error": "missing path"})
                return
            try:
                reveal_path(raw)
            except FileNotFoundError as err:
                self._json(404, {"ok": False, "error": str(err)})
                return
            except OSError as err:
                self._json(500, {"ok": False, "error": str(err)})
                return
            self._json(200, {"ok": True, "path": raw})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _pick_port() -> int:
    import socket

    for port in PORT_CANDIDATES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((DEFAULT_HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port for live reveal helper")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Reveal-in-Finder helper for live HTML")
    parser.add_argument("--dir", required=True, help="Live monitor directory")
    args = parser.parse_args(argv)
    live_dir = Path(args.dir).resolve()
    live_dir.mkdir(parents=True, exist_ok=True)

    port = _pick_port()
    (live_dir / PORT_FILE).write_text(str(port), encoding="utf-8")
    (live_dir / PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

    def _cleanup(*_args) -> None:
        for name in (PORT_FILE, PID_FILE):
            try:
                (live_dir / name).unlink()
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    _Handler.live_dir = live_dir
    server = _ThreadedServer((DEFAULT_HOST, port), _Handler)
    try:
        server.serve_forever()
    finally:
        _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

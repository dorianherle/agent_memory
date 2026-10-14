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
import re
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
WORKING_LIMIT = 50
HIDDEN_DIR = ".agent_memory"
INSPECT_FILE = "INSPECTING"
# Cursor rewrites running_for_ms about every 5s; older files are leftovers.
LIVE_TERMINAL_S = 20


def reveal_url(live_dir: Path) -> str | None:
    """Return ``http://127.0.0.1:PORT/reveal`` if the helper is healthy."""
    port = _read_port(live_dir)
    if port is None:
        return None
    base = f"http://{DEFAULT_HOST}:{port}"
    if not _healthy(base):
        return None
    return f"{base}/reveal"


def ensure_reveal_server(live_dir: Path, root: Path | None = None) -> str | None:
    """Start the reveal helper if needed; return the reveal endpoint URL."""
    if os.environ.get("AGENT_MEMORY_NO_REVEAL"):
        return None
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    existing = reveal_url(live_dir)
    if existing and _working_ok(existing):
        return existing
    if existing:
        _stop_reveal(live_dir)

    python = sys.executable
    log = live_dir / ".reveal.log"
    command = [python, "-m", "agent_memory.live_reveal", "--dir", str(live_dir)]
    if root is not None:
        command.extend(["--root", str(Path(root).resolve())])
    with open(log, "a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            command,
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
        if proc.poll() is not None:
            # Child exited before becoming healthy (crash / import error).
            return None
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


def _working_ok(reveal: str) -> bool:
    base = str(reveal).rsplit("/", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/working", timeout=0.3) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode())
        return isinstance(data, dict) and "processes" in data
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ):
        return False


def _stop_reveal(live_dir: Path) -> None:
    pid_path = Path(live_dir) / PID_FILE
    if not pid_path.exists():
        return
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        time.sleep(0.05)
        try:
            os.kill(pid, 0)
        except OSError:
            break


def _infer_project_root(live_dir: Path) -> Path:
    live_dir = Path(live_dir).resolve()
    if live_dir.name == "live" and live_dir.parent.name == HIDDEN_DIR:
        return live_dir.parent.parent
    return live_dir.parent


def _unquote_git_path(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return bytes(raw[1:-1], "utf-8").decode("unicode_escape")
    return raw


def working_files(root: Path, limit: int = WORKING_LIMIT) -> dict:
    """Dirty project files vs the last memory snapshot (``git status``)."""
    root = Path(root).resolve()
    git_dir = root / HIDDEN_DIR
    if not git_dir.exists():
        return {"ok": False, "error": "no memory store", "files": [], "total": 0}
    result = subprocess.run(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={root}",
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain",
            "-uall",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return {
            "ok": False,
            "error": (result.stderr or result.stdout).strip(),
            "files": [],
            "total": 0,
        }
    files: list[dict] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        rest = line[3:]
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        path = _unquote_git_path(rest)
        abs_path = root / path
        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        files.append(
            {
                "path": path,
                "status": status.strip() or status,
                "mtime": mtime,
            }
        )
    files.sort(key=lambda item: (-item["mtime"], item["path"]))
    return {
        "ok": True,
        "files": files[:limit],
        "total": len(files),
        "vs": "last memory snapshot",
    }


def inspecting_state(root: Path) -> dict | None:
    """Inspect marker if the work tree is parked on an old snapshot."""
    path = Path(root).resolve() / HIDDEN_DIR / INSPECT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def project_slug(root: Path) -> str:
    """Cursor project folder name for a workspace path."""
    text = str(root.resolve())
    if text.startswith("/"):
        text = text[1:]
    return text.replace("/", "-")


def _cursor_project_candidates(root: Path) -> list[str]:
    """Possible ~/.cursor/projects/<slug> names for this workspace."""
    slug = project_slug(root)
    names = [slug]
    alt = slug.replace("_", "-")
    if alt != slug:
        names.append(alt)
    return names


def _terminals_dir(root: Path, projects: Path | None = None) -> Path | None:
    base = Path(projects) if projects is not None else Path.home() / ".cursor" / "projects"
    best: Path | None = None
    best_n = -1
    for name in _cursor_project_candidates(root):
        term = base / name / "terminals"
        if not term.is_dir():
            continue
        n = sum(1 for _ in term.glob("*.txt"))
        if n > best_n:
            best_n = n
            best = term
    return best


def _parse_terminal(path: Path) -> tuple[dict, dict] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None

    def _fields(block: str) -> dict:
        out: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            out[key.strip()] = val.strip().strip('"')
        return out

    header = _fields(text[4:end])
    footer: dict[str, str] = {}
    # Trailing `---` after the footer is common; take the last block with fields.
    for block in reversed(re.split(r"(?m)^---\s*$", text[end:])):
        fields = _fields(block)
        if fields:
            footer = fields
            break
    return header, footer


def running_processes(
    root: Path, *, projects: Path | None = None
) -> list[dict]:
    """Cursor terminals in this workspace that still have a live command."""
    term_dir = _terminals_dir(Path(root).resolve(), projects)
    if term_dir is None:
        return []
    now = time.time()
    rows: list[dict] = []
    for path in sorted(term_dir.glob("*.txt")):
        parsed = _parse_terminal(path)
        if parsed is None:
            continue
        meta, footer = parsed
        command = meta.get("command") or ""
        if not command:
            continue
        if "last_exit_code" in meta or "exit_code" in footer:
            continue
        try:
            if now - path.stat().st_mtime > LIVE_TERMINAL_S:
                continue
        except OSError:
            continue
        try:
            pid = int(meta.get("pid") or 0)
        except ValueError:
            pid = 0
        try:
            running_ms = int(float(meta.get("running_for_ms") or 0))
        except ValueError:
            running_ms = 0
        rows.append(
            {
                "pid": pid,
                "command": command,
                "cwd": meta.get("cwd") or "",
                "running_for_ms": running_ms,
                "started_at": meta.get("started_at") or "",
            }
        )
    rows.sort(key=lambda item: (-item["running_for_ms"], item["pid"]))
    return rows


def now_state(
    root: Path,
    limit: int = WORKING_LIMIT,
    *,
    projects: Path | None = None,
) -> dict:
    """Dirty files + live terminals + inspect marker for the live monitor."""
    payload = working_files(root, limit)
    payload["processes"] = running_processes(root, projects=projects)
    payload["inspecting"] = inspecting_state(root)
    return payload


class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    live_dir: Path = Path(".")
    project_root: Path | None = None

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
        if parsed.path == "/working":
            root = self.project_root or _infer_project_root(self.live_dir)
            self._json(200, now_state(root))
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
    # Preferred ports taken (other projects / stale helpers) — use ephemeral.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Reveal-in-Finder helper for live HTML")
    parser.add_argument("--dir", required=True, help="Live monitor directory")
    parser.add_argument("--root", default=None, help="Project root (memory work tree)")
    args = parser.parse_args(argv)
    live_dir = Path(args.dir).resolve()
    live_dir.mkdir(parents=True, exist_ok=True)
    project_root = (
        Path(args.root).resolve() if args.root else _infer_project_root(live_dir)
    )

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
    _Handler.project_root = project_root
    server = _ThreadedServer((DEFAULT_HOST, port), _Handler)
    try:
        server.serve_forever()
    finally:
        _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

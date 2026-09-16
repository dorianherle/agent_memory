"""Local Cursor SDK studio: one chat, fresh agent per question, memory on the side."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent_memory.core import Memory
from agent_memory.prompt import _goal, _handoff, _headline, split_open_questions

PAGE = Path(__file__).with_name("studio.html")
SESSION: dict = {
    "api_key": os.environ.get("CURSOR_API_KEY", "").strip(),
    "project": "",
    "model": "composer-2.5",
    "agent": None,
    "run": None,
    "cancel": False,
}
LOCK = threading.Lock()


def _item(item) -> dict | None:
    if item is None:
        return None
    return {"id": item.id[:8], "text": _headline(item)}


def memory_view(root: str) -> dict:
    memory = Memory(root)
    items = memory.state()
    goal = _goal(items)
    handoff = _handoff(items)
    graph = memory.graph_data()
    nodes = sorted(
        graph.get("nodes") or [],
        key=lambda node: node.get("committed_at") or "",
        reverse=True,
    )
    commits = []
    for node in nodes:
        if node.get("folded"):
            continue
        commits.append(
            {
                "id": node["id"][:8],
                "kind": node.get("kind") or "step",
                "agent": node.get("agent") or "",
                "title": (node.get("message") or "").split("\n", 1)[0][:120],
                "at": node.get("committed_at") or "",
            }
        )
    return {
        "packet": memory.render_packet(),
        "goal": _item(goal),
        "handoff": _item(handoff),
        "questions": [_item(item) for item in split_open_questions(items)],
        "commits": commits[:80],
        "branches": [
            {
                "name": row["name"],
                "current": bool(row.get("current")),
            }
            for row in graph.get("branches") or []
        ],
    }


def _close_agent() -> None:
    run = SESSION.get("run")
    agent = SESSION.get("agent")
    SESSION["cancel"] = True
    if run is not None and hasattr(run, "supports") and run.supports("cancel"):
        try:
            run.cancel()
        except Exception:
            pass
    if agent is not None:
        try:
            agent.close()
        except Exception:
            pass
    SESSION["agent"] = None
    SESSION["run"] = None


def _ask(question: str, write) -> None:
    try:
        from cursor_sdk import Agent, LocalAgentOptions
    except ImportError:
        write({"type": "error", "message": 'Install the SDK: pip install "cursor-sdk"'})
        return
    key = SESSION["api_key"]
    project = SESSION["project"]
    if not key:
        write({"type": "error", "message": "Add a Cursor API key."})
        return
    if not project:
        write({"type": "error", "message": "Open a project first."})
        return
    with LOCK:
        _close_agent()
        SESSION["cancel"] = False
        agent = Agent.create(
            model=SESSION["model"],
            api_key=key,
            local=LocalAgentOptions(
                cwd=project,
                setting_sources=["project"],
            ),
        )
        SESSION["agent"] = agent
        run = agent.send(question)
        SESSION["run"] = run
    try:
        stream = run.messages() if hasattr(run, "messages") else run.stream()
        for message in stream:
            if SESSION.get("cancel"):
                break
            kind = getattr(message, "type", None)
            payload = getattr(message, "message", None)
            if kind == "assistant" and payload is not None:
                for block in getattr(payload, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        write({"type": "delta", "text": getattr(block, "text", "")})
        result = run.wait()
        status = getattr(result, "status", "finished")
        write({"type": "done", "status": status})
    except Exception as error:
        write({"type": "error", "message": str(error)})
    finally:
        with LOCK:
            _close_agent()
            SESSION["cancel"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/memory":
            project = SESSION.get("project")
            if not project:
                return self._json(400, {"error": "Open a project first."})
            try:
                return self._json(200, memory_view(project))
            except Exception as error:
                return self._json(500, {"error": str(error)})
        if path == "/api/session":
            project = SESSION.get("project") or ""
            return self._json(
                200,
                {
                    "project": project,
                    "has_key": bool(SESSION.get("api_key")),
                    "model": SESSION.get("model"),
                    "ready": bool(
                        project
                        and (Path(project) / ".cursor" / "mcp.json").exists()
                    ),
                },
            )
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._read_json()
        if path == "/api/open":
            project = str(Path(data.get("project") or "").expanduser()).strip()
            key = str(data.get("api_key") or "").strip() or SESSION.get("api_key")
            model = str(data.get("model") or SESSION.get("model") or "composer-2.5")
            root = Path(project).resolve() if project else None
            if root is None or not root.is_dir():
                return self._json(400, {"error": "That path is not a folder."})
            with LOCK:
                _close_agent()
                SESSION["project"] = str(root)
                SESSION["api_key"] = key or ""
                SESSION["model"] = model
            ready = (root / ".cursor" / "mcp.json").exists()
            return self._json(
                200,
                {
                    "project": str(root),
                    "ready": ready,
                    "hint": None
                    if ready
                    else 'Run: agent-memory cursor install --skip-gemini',
                },
            )
        if path == "/api/ask":
            question = str(data.get("question") or "").strip()
            if not question:
                return self._json(400, {"error": "Type a question."})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def write(event: dict) -> None:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()

            _ask(question, write)
            return
        if path == "/api/cancel":
            with LOCK:
                _close_agent()
            return self._json(200, {"ok": True})
        self.send_error(404)


def serve(host: str = "127.0.0.1", port: int = 8788, *, open_browser: bool = True) -> None:
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        if port == 0:
            raise
        server = ThreadingHTTPServer((host, 0), Handler)
        print(f"Port {port} in use; using {server.server_address[1]}", flush=True)
    bound = server.server_address[1]
    url = f"http://{host}:{bound}"
    print(f"Studio {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        with LOCK:
            _close_agent()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="agent-memory studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve(args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

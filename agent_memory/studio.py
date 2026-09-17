"""Local Studio: goal board that auto-runs agents against pytest."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import webbrowser
from time import perf_counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from agent_memory.core import Memory
from agent_memory.studio_runtime import AgentConfig, DEFAULT_MODEL, StandbyAgent
from agent_memory.prompt import _goal, _handoff, _headline, split_open_questions

PAGE = Path(__file__).with_name("studio.html")
SESSION: dict = {
    "api_key": os.environ.get("CURSOR_API_KEY", "").strip(),
    "project": "",
    "model": DEFAULT_MODEL,
    "agent": None,
    "run": None,
    "cancel": False,
    "goal_id": "",
}
RUN_WORKER = None  # tests inject a fake worker
LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
STANDBY = StandbyAgent()
VIEW_LOCK = threading.Lock()
VIEW_CACHE: dict = {}


def memory_revision(root: str) -> str:
    """Git reference contents invalidate the overview without opening SQLite."""
    git = Path(root) / ".agent_memory"
    paths = [git / "HEAD", git / "packed-refs", git / "INSPECTING"]
    paths += sorted((git / "refs").rglob("*")) if (git / "refs").exists() else []
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(str(path.relative_to(git)).encode())
            try:
                digest.update(path.read_bytes())
            except FileNotFoundError:
                pass  # A concurrent Git ref replacement causes another refresh.
    return '"' + digest.hexdigest()[:24] + '"'


def cached_overview(root: str, revision: str) -> dict:
    with VIEW_LOCK:
        cached = VIEW_CACHE.get(root)
        if cached and cached[0] == revision:
            return cached[1]
        data = memory_view(root, compact=True)
        VIEW_CACHE.clear()  # Studio has one active project; bound retained history.
        VIEW_CACHE[root] = (revision, data)
        return data


def retire_agent(agent, run=None) -> None:
    """Remote cleanup cannot hold the session lock or delay the next turn."""
    if agent is None and run is None:
        return
    def cleanup():
        if run is not None:
            try:
                if run.supports("cancel"):
                    run.cancel()
            except Exception:
                pass
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass
    threading.Thread(target=cleanup, name="studio-retire", daemon=True).start()



def _item(item) -> dict | None:
    if item is None:
        return None
    return {"id": item.id[:8], "text": _headline(item)}


def session_worker_name() -> str:
    if RUN_WORKER is not None:
        return "test"
    from agent_memory.runner import detect_worker

    name, _ = detect_worker(SESSION.get("api_key"))
    return name


def bound_root(project: str, goal_id: str | None = None) -> str:
    goal_id = goal_id or SESSION.get("goal_id") or ""
    if not goal_id:
        return project
    from agent_memory.goals import resolve_goal

    return resolve_goal(project, goal_id)["workspace"]


def goals_view(project: str) -> dict:
    from agent_memory.goals import list_goals

    return {"goals": list_goals(project)}


def memory_view(root: str, *, compact: bool = False) -> dict:
    memory = Memory(root)
    items = memory.state()
    goal = _goal(items)
    handoff = _handoff(items)
    # Scope folding to this branch. The all-branch graph can hide a shared
    # ancestor because an unrelated branch summarized it.
    history = memory.history()
    by_id = {item.id: item for item in history}

    def entry(item, seen=frozenset()):
        payload = item.payload
        kind = ("summary" if "covers" in payload else
                "open_question" if payload.get("open_question") else
                "user" if payload.get("user") else
                "answer" if payload.get("auto") else "step")
        return {
            "id": item.id[:8], "kind": kind,
            "agent": payload.get("agent", ""),
            "title": _headline(item) if compact else item.message.split("\n", 1)[0],
            **({} if compact else {"text": item.message}),
            "at": item.datetime.isoformat(),
            "children": [entry(by_id[child], seen | {item.id})
                         for child in payload.get("covers", [])
                         if child in by_id and child not in seen | {item.id}],
        }

    result = {
        **({} if compact else {"packet": memory.render_packet()}),
        "goal": _item(goal), "handoff": _item(handoff),
        "questions": [_item(item) for item in split_open_questions(items)],
        "commits": [entry(item) for item in reversed(items)],
        "branches": [{"name": row["name"], "current": row["current"]}
                     for row in memory.branches()],
    }
    memory._catalog.close()
    return result


def note_view(root: str, note_id: str) -> dict:
    memory = Memory(root)
    try:
        found = memory.find(ids=note_id, k=1)
        if not found:
            raise ValueError("This note is no longer available.")
        note = found[0]
        return {"id": note.id[:8], "title": note.message.split("\n", 1)[0],
                "text": note.message, "agent": note.payload.get("agent", ""),
                "at": note.datetime.isoformat()}
    finally:
        memory._catalog.close()


def project_ready(root: Path) -> bool:
    try:
        config = json.loads((root / ".cursor" / "mcp.json").read_text())
        return bool(config.get("mcpServers", {}).get("agent-memory"))
    except (OSError, ValueError):
        return False


def _warm_next() -> None:
    with LOCK:
        project = SESSION.get("project") or ""
        key = SESSION.get("api_key") or ""
        config = AgentConfig(project, SESSION.get("model") or DEFAULT_MODEL, key)
    if project and key and project_ready(Path(project)):
        STANDBY.warm(config)
    else:
        STANDBY.clear()


def pick_folder() -> str | None:
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Open a project")',
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return None
        return out.strip().rstrip("/")
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(title="Open a project")
    root.destroy()
    return path or None


def _open_root(root: Path, key: str, model: str) -> dict:
    # Opening a folder in Studio must leave it usable without a terminal step.
    # Preserve existing embedding credentials when available.
    if not project_ready(root):
        from agent_memory.cursor.install import install_cursor, GeminiSetupError
        try:
            install_cursor(root, interactive=False)
        except GeminiSetupError:
            install_cursor(root, skip_gemini=True, interactive=False)
    with LOCK:
        _close_agent()
        SESSION["project"] = str(root)
        SESSION["api_key"] = key or ""
        SESSION["model"] = model
    _warm_next()
    ready = project_ready(root)
    return {
        "project": str(root),
        "name": root.name,
        "ready": ready,
        "hint": None if ready else "Install memory in this folder: agent-memory cursor install",
    }


def _close_agent(*, cancel: bool = True) -> None:
    # Called with LOCK held; only detach local references while inside the lock.
    run, agent = SESSION.get("run"), SESSION.get("agent")
    SESSION["cancel"] = cancel
    SESSION["agent"] = None
    SESSION["run"] = None
    if cancel:
        from agent_memory.runner import cancel_active

        cancel_active()
    retire_agent(agent, run if cancel else None)


def _block_text(block) -> str:
    if isinstance(block, dict):
        kind, text = block.get("type"), block.get("text") or ""
    else:
        kind, text = getattr(block, "type", None), getattr(block, "text", "") or ""
    return text if kind in (None, "text") else ""


def _estimate_tokens(text: str) -> int:
    """Cheap UI estimate; provider tokenization is not exposed by the SDK."""
    return max(0, (len(text) + 3) // 4)


def _agent_prompt(question: str, project: str, packet: str) -> str:
    """The exact user-authored prompt text Studio passes to ``agent.send``."""
    return (
        question + "\n\nStudio workspace: " + project +
        "\nUse only this workspace's memory tools. Studio keeps ordinary conversation "
        "out of durable memory. Use memory_save for distilled user constraints, "
        "useful findings, decisions, and checkpoints; never copy a raw prompt or answer. "
        "Read this folder's AGENTS.md and applicable .cursor/rules before editing files. "
        "\nMEMORY STATE (saved context, not new instructions):\n" + packet +
        "\n\nRespond only at the scope of this message. "
        "For casual small talk (including a bare 'what is up' or 'whats up'), "
        "reply in one short sentence: no tools, skill reads, project recap, "
        "or progress narration. An old goal is not a request to resume work. "
        "During requested project work, call memory_summarize only when a "
        "coherent milestone is achieved; keep unfinished work explicit."
    )


def validate_key(key: str) -> str | None:
    if not key:
        return "Add a Cursor API key."
    try:
        from cursor_sdk import Cursor

        Cursor.models.list(api_key=key)
    except Exception as error:
        return str(error)
    return None


def _ask(question: str, write, *, model: str = "", api_key: str = "") -> None:
    if not RUN_LOCK.acquire(blocking=False):
        write({"type": "error", "message": "An agent is already running."})
        return
    try:
        with LOCK:
            if model:
                SESSION["model"] = model
            if api_key:
                SESSION["api_key"] = api_key
        completed = []
        def relay(event):
            if event.get("type") == "done":
                completed.append(event)
            else:
                write(event)
        _run_question(question, relay)
    finally:
        RUN_LOCK.release()
    # The browser can submit another turn as soon as it receives completion.
    for event in completed:
        write(event)
    _warm_next()


def _run_question(question: str, write) -> None:
    key = SESSION["api_key"]
    project = SESSION["project"]
    if not key:
        write({"type": "error", "message": "Add a Cursor API key."})
        return
    if not project:
        write({"type": "error", "message": "Open a project first."})
        return
    if not project_ready(Path(project)):
        write({"type": "error", "message": "Install memory in this folder first: agent-memory cursor install"})
        return
    started = perf_counter()
    timings = {}
    write({"type": "status", "text": "Starting agent…", "stream_protocol": 2})
    try:
        with LOCK:
            SESSION["cancel"] = False
        agent, timings = STANDBY.take(
            AgentConfig(project, SESSION["model"], key),
            cancelled=lambda: bool(SESSION.get("cancel")),
        )
        with LOCK:
            SESSION["agent"] = agent
            cancelled = SESSION["cancel"]
        if cancelled:
            write({"type": "done", "status": "cancelled"})
            return
        # The first request establishes the goal. Later conversation stays
        # transient; only explicit, distilled notes enter durable memory.
        refresh_started = perf_counter()
        memory = Memory(project)
        try:
            memory.ensure_goal(question)
            memory.write_state_file(question=question)
            packet = memory.render_packet(question)
        finally:
            memory._catalog.close()
        timings["memory_refresh_ms"] = round((perf_counter() - refresh_started) * 1000, 1)
        write({"type": "memory"})
        _warm_next()  # Prepare the successor while send/connect is still in flight.
        write({"type": "status", "text": "Reading the handoff…"})
        send_started = perf_counter()
        prompt = _agent_prompt(question, project, packet)
        run = agent.send(prompt)
        timings["send_ms"] = round((perf_counter() - send_started) * 1000, 1)
        write({
            "type": "prompt",
            "text": prompt,
            "prompt_tokens": _estimate_tokens(prompt),
            "memory_tokens": _estimate_tokens(packet),
        })
        with LOCK:
            SESSION["run"] = run
            cancelled = SESSION["cancel"]
        if cancelled:
            if run.supports("cancel"):
                run.cancel()
            write({"type": "done", "status": "cancelled"})
            return
        had_text = False
        partial_text = ""
        fail = ""
        for event in run.events():
            if SESSION.get("cancel"):
                break
            if "first_event_ms" not in timings:
                timings["first_event_ms"] = round((perf_counter() - started) * 1000, 1)
            update = getattr(event, "interaction_update", None)
            if getattr(update, "type", None) == "text-delta":
                text = getattr(update, "text", "")
                if text:
                    if not had_text:
                        timings["first_text_ms"] = round((perf_counter() - started) * 1000, 1)
                    had_text = True
                    partial_text += text
                    write({"type": "delta", "text": text})
            msg = event.sdk_message
            if msg is None:
                continue
            kind = getattr(msg, "type", None)
            if kind == "assistant":
                payload = getattr(msg, "message", None)
                text = "".join(_block_text(block) for block in getattr(payload, "content", None) or [])
                # Completed SDK messages may repeat the incremental text.
                if partial_text and text.startswith(partial_text):
                    text = text[len(partial_text):]
                partial_text = ""
                if text:
                    if not had_text:
                        timings["first_text_ms"] = round((perf_counter() - started) * 1000, 1)
                    had_text = True
                    write({"type": "delta", "text": text})
            elif kind == "tool_call":
                write({"type": "status", "text": f"{getattr(msg, 'name', 'Tool')} · {getattr(msg, 'status', '')}"})
            elif kind == "status":
                note = (getattr(msg, "message", "") or "").strip()
                state = (getattr(msg, "status", "") or "").upper()
                if state == "ERROR" and note:
                    fail = note
                    write({"type": "error", "message": note})
                elif note:
                    write({"type": "status", "text": note})
        result = run.wait()
        status = "cancelled" if SESSION.get("cancel") else getattr(result, "status", "finished")
        leftover = (getattr(result, "result", None) or "").strip()
        if leftover and not had_text and status != "cancelled":
            write({"type": "delta", "text": leftover})
        if status == "error" and not fail:
            write({"type": "error", "message": leftover or "Agent run failed."})
        if status not in ("cancelled", "error") and not fail:
            memory = Memory(project)
            try:
                memory.write_state_file()
            finally:
                memory._catalog.close()
            write({"type": "memory"})
        write({"type": "done", "status": status, "timings": timings})
    except Exception as error:
        if SESSION.get("cancel"):
            write({"type": "done", "status": "cancelled", "timings": timings})
        else:
            write({"type": "error", "message": str(error)})
    finally:
        with LOCK:
            _close_agent(cancel=False)
            SESSION["cancel"] = False


def _studio_worker():
    if RUN_WORKER is not None:
        return "test", RUN_WORKER
    from agent_memory.runner import detect_worker

    return detect_worker(SESSION.get("api_key"))


def _run_goal_loop(text: str, goal_id: str, write) -> None:
    from agent_memory.runner import run_goal

    if not RUN_LOCK.acquire(blocking=False):
        write({"type": "error", "message": "An agent is already running."})
        return
    try:
        project = SESSION.get("project") or ""
        if not project:
            write({"type": "error", "message": "Open a project first."})
            return
        name, worker = _studio_worker()
        if worker is None:
            write({"type": "error", "message": "Connect Codex or a Cursor API key in Settings."})
            return
        with LOCK:
            SESSION["cancel"] = False
        write({"type": "status", "text": f"Starting {name} agent…", "worker": name})

        def observe(status: dict) -> None:
            with LOCK:
                SESSION["goal_id"] = status.get("goal_id") or SESSION.get("goal_id")
            write({"type": "observe", **status})
            write({"type": "memory"})

        result = run_goal(
            project,
            text or None,
            goal_id=goal_id or None,
            worker=worker,
            on_observe=observe,
            cancelled=lambda: bool(SESSION.get("cancel")),
        )
        with LOCK:
            SESSION["goal_id"] = result.get("goal_id") or SESSION.get("goal_id")
        write({"type": "done", "status": result.get("state"), "result": result})
    except Exception as error:
        if SESSION.get("cancel"):
            write({"type": "done", "status": "cancelled"})
        else:
            write({"type": "error", "message": str(error)})
    finally:
        with LOCK:
            SESSION["cancel"] = False
        RUN_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, code: int, payload: dict, *, etag: str | None = None) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
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
                root = bound_root(project, query.get("goal", [None])[0])
                if query.get("compact") == ["1"]:
                    revision = memory_revision(root)
                    if self.headers.get("If-None-Match") == revision:
                        self.send_response(304)
                        self.send_header("ETag", revision)
                        self.end_headers()
                        return
                    return self._json(200, cached_overview(root, revision), etag=revision)
                return self._json(200, memory_view(root))
            except Exception as error:
                return self._json(500, {"error": str(error)})
        if path == "/api/note":
            project = SESSION.get("project")
            if not project:
                return self._json(400, {"error": "Open a project first."})
            try:
                root = bound_root(project, query.get("goal", [None])[0])
                return self._json(200, note_view(root, query.get("id", [""])[0]))
            except ValueError as error:
                return self._json(404, {"error": str(error)})
        if path == "/api/goals":
            project = SESSION.get("project")
            if not project:
                return self._json(400, {"error": "Open a project first."})
            try:
                return self._json(200, goals_view(project))
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
                    "standby": STANDBY.status(),
                    "running": RUN_LOCK.locked(),
                    "ready": bool(
                        project and project_ready(Path(project))
                    ),
                    "worker": session_worker_name(),
                    "goal_id": SESSION.get("goal_id") or "",
                },
            )
        if path == "/api/models":
            key = SESSION.get("api_key") or ""
            if not key:
                return self._json(400, {"error": "Add a Cursor API key."})
            try:
                from cursor_sdk import Cursor

                items = Cursor.models.list(api_key=key)
                return self._json(
                    200,
                    {
                        "models": [
                            {"id": item.id, "name": "Grok 4.5 Fast" if item.id == DEFAULT_MODEL else item.display_name or item.id}
                            for item in items
                        ]
                    },
                )
            except Exception as error:
                return self._json(500, {"error": str(error)})
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
        except (ValueError, TypeError):
            return self._json(400, {"error": "Invalid JSON request."})
        if path in ("/api/open", "/api/pick") and RUN_LOCK.locked():
            return self._json(409, {"error": "Stop the running agent first."})
        if path == "/api/model":
            if RUN_LOCK.locked():
                return self._json(409, {"error": "Stop the running agent first."})
            model = str(data.get("model") or DEFAULT_MODEL).strip()
            with LOCK:
                SESSION["model"] = model
            _warm_next()
            return self._json(200, {"model": model, "standby": STANDBY.status()})
        if path == "/api/open":
            project = str(data.get("project") or "").strip()
            key = str(data.get("api_key") or "").strip() or SESSION.get("api_key")
            model = str(data.get("model") or SESSION.get("model") or DEFAULT_MODEL)
            root = Path(project).expanduser().resolve() if project else None
            if root is None or not root.is_dir():
                return self._json(400, {"error": "That path is not a folder."})
            try:
                opened = _open_root(root, key or "", model)
            except (OSError, ValueError, RuntimeError) as error:
                return self._json(400, {"error": f"Could not prepare this folder: {error}"})
            return self._json(200, opened)
        if path == "/api/pick":
            key = str(data.get("api_key") or "").strip() or SESSION.get("api_key")
            model = str(data.get("model") or SESSION.get("model") or DEFAULT_MODEL)
            picked = pick_folder()
            if not picked:
                return self._json(400, {"error": "Cancelled."})
            root = Path(picked).expanduser().resolve()
            if not root.is_dir():
                return self._json(400, {"error": "That path is not a folder."})
            try:
                opened = _open_root(root, key or "", model)
            except (OSError, ValueError, RuntimeError) as error:
                return self._json(400, {"error": f"Could not prepare this folder: {error}"})
            return self._json(200, opened)
        if path == "/api/key":
            key = str(data.get("api_key") or "").strip()
            SESSION["api_key"] = key
            error = validate_key(key)
            if error is None:
                _warm_next()
            else:
                STANDBY.clear()
            return self._json(200, {"ok": error is None, "error": error})
        if path == "/api/connect-codex":
            from agent_memory.runner import start_codex_login

            try:
                return self._json(200, start_codex_login())
            except Exception as error:
                return self._json(400, {"error": str(error)})
        if path == "/api/run":
            text = str(data.get("text") or data.get("question") or "").strip()
            goal_id = str(data.get("goal_id") or "").strip()
            if not text and not goal_id:
                return self._json(400, {"error": "Type a goal or resume an id."})
            if goal_id:
                with LOCK:
                    SESSION["goal_id"] = goal_id
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def write_run(event: dict) -> None:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()

            _run_goal_loop(text, goal_id, write_run)
            return
        if path == "/api/ask":
            question = str(data.get("question") or "").strip()
            key = str(data.get("api_key") or "").strip()
            model = str(data.get("model") or "").strip()
            if not question:
                return self._json(400, {"error": "Type a question."})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def write(event: dict) -> None:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()

            _ask(question, write, model=model, api_key=key)
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
    _warm_next()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        with LOCK:
            _close_agent()
        STANDBY.clear()
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

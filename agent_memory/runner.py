"""Auto-run one goal: write failing pytests, then spawn until they pass."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_ACTIVE: dict = {}

from agent_memory.core import Memory
from agent_memory.goals import (
    bind_packet,
    create_goal,
    read_status,
    resolve_goal,
    write_status,
)

Worker = Callable[[str, Path], None]


def observe_pytest(workspace: str | Path, extra: list[str] | None = None) -> dict:
    command = [sys.executable, "-m", "pytest", "-q", *(extra or [])]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        command,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        env=env,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    lines = [line for line in combined.strip().splitlines() if line.strip()]
    summary = lines[-1] if lines else f"exit {result.returncode}"
    tail = "\n".join(lines[-40:])
    return {
        "ok": result.returncode == 0,
        "code": result.returncode,
        "summary": summary,
        "tail": tail,
        "command": " ".join(command),
    }


def bootstrap_prompt(record: dict) -> str:
    return (
        f"Goal [{record['id']}] {record['title']}\n"
        f"Workspace: {record['workspace']}\n\n"
        "Inspect this repository. Write pytest(s) that encode the goal's "
        "acceptance criteria. Do not implement the feature. The new tests "
        "must fail on the current tree.\n"
        "This workspace is the goal tree. Memory tools here need no goal=; "
        "from the parent repo pass goal=" + record["id"] + "."
    )


def work_prompt(record: dict, packet: str, pytest_result: dict) -> str:
    return (
        f"{packet}\n\n"
        f"Last pytest ({pytest_result.get('summary', '')}):\n"
        f"{pytest_result.get('tail', '')}\n\n"
        "Make the failing tests pass. Do not retry an approach that produced "
        "this same failure. Do not weaken the tests."
    )


def _save_observe(workspace: Path, agent: int, result: dict, next_step: str) -> None:
    memory = Memory(workspace)
    verdict = "pass" if result["ok"] else "fail"
    memory.save(
        f"Agent {agent} pytest {verdict}: {result['summary']}\n\n"
        f"{result['tail']}\n"
        f"Next: {next_step}",
        agent="runner",
    )


def _status_payload(record: dict, agent: int, max_agents: int, phase: str, result: dict, state: str) -> dict:
    return {
        "goal_id": record["id"],
        "goal": record["title"],
        "workspace": record["workspace"],
        "branch": record.get("branch") or "",
        "agent": agent,
        "max_agents": max_agents,
        "phase": phase,
        "pytest": {
            "ok": result.get("ok"),
            "summary": result.get("summary", ""),
            "tail": result.get("tail", ""),
        },
        "state": state,
        "updated": datetime.now(timezone.utc).isoformat(),
    }


def run_goal(
    project: str | Path,
    text: str | None = None,
    *,
    goal_id: str | None = None,
    worker: Worker,
    max_agents: int = 8,
    pytest_args: list[str] | None = None,
    on_observe: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Create or resume a goal, then loop worker → pytest until green or max."""
    project = Path(project).resolve()
    last: dict = {}
    if goal_id:
        record = resolve_goal(project, goal_id)
        prior = read_status(project, record["id"])
        last = dict(prior.get("pytest") or {})
        phase = prior.get("phase") or "bootstrap"
        if last.get("ok") is False:
            phase = "work"
    else:
        if not text:
            raise ValueError("goal text is required")
        record = create_goal(project, text)
        phase = "bootstrap"
    workspace = Path(record["workspace"])
    for agent in range(max_agents):
        packet = bind_packet(project, record["id"])
        prompt = (
            bootstrap_prompt(record)
            if phase == "bootstrap"
            else work_prompt(record, packet, last)
        )
        if cancelled and cancelled():
            status = _status_payload(
                record, max(agent - 1, 0), max_agents, phase, last, "stopped"
            )
            write_status(project, record["id"], status)
            if on_observe:
                on_observe(status)
            return status
        worker(prompt, workspace)
        last = observe_pytest(workspace, pytest_args)
        if phase == "bootstrap":
            if last["ok"]:
                next_step = "rewrite failing acceptance tests"
                state = "running"
            else:
                next_step = "make the failing tests pass"
                state = "running"
                phase = "work"
        elif last["ok"]:
            next_step = "done"
            state = "passed"
        else:
            next_step = "spawn next agent on the same tree"
            state = "running"
        _save_observe(workspace, agent, last, next_step)
        status = _status_payload(record, agent, max_agents, phase, last, state)
        write_status(project, record["id"], status)
        if on_observe:
            on_observe(status)
        if state == "passed":
            return status
    status = _status_payload(record, max_agents - 1, max_agents, phase, last, "stopped")
    write_status(project, record["id"], status)
    if on_observe:
        on_observe(status)
    return status


def cursor_worker(prompt: str, cwd: Path, api_key: str | None = None) -> None:
    key = (api_key or os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("CURSOR_API_KEY required for the Cursor runner adapter")
    from agent_memory.studio_runtime import (
        DEFAULT_MODEL,
        AgentConfig,
        close_agent,
        create_agent,
    )

    agent = create_agent(
        AgentConfig(project=str(cwd), model=DEFAULT_MODEL, api_key=key)
    )
    try:
        run = agent.send(prompt)
        run.wait()
    finally:
        close_agent(agent)


def codex_command(prompt: str, cwd: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(cwd),
        prompt,
    ]


def codex_logged_in(*, which=shutil.which, run=subprocess.run) -> bool:
    if not which("codex"):
        return False
    result = run(
        ["codex", "login", "status"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def detect_worker(
    cursor_key: str | None = None,
    *,
    which=shutil.which,
    run=subprocess.run,
) -> tuple[str, Worker | None]:
    """Pick Codex if already logged in, else a Cursor key, else none."""
    if codex_logged_in(which=which, run=run):
        return "codex", codex_worker
    key = (cursor_key or os.environ.get("CURSOR_API_KEY") or "").strip()
    if key:
        return "cursor", lambda prompt, cwd: cursor_worker(prompt, cwd, api_key=key)
    return "none", None


def cancel_active() -> None:
    proc = _ACTIVE.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()


def start_codex_login(*, popen=subprocess.Popen) -> dict:
    if not shutil.which("codex"):
        raise RuntimeError("codex is not on PATH. Install Codex CLI, then retry.")
    popen(["codex", "login"])
    return {"ok": True, "hint": "Finish ChatGPT login in the browser, then refresh."}


def codex_worker(prompt: str, cwd: Path, *, run=None) -> None:
    command = codex_command(prompt, cwd)
    if run is not None:
        result = run(command, cwd=str(cwd))
        if getattr(result, "returncode", 0):
            raise RuntimeError(f"codex exec exited {result.returncode}")
        return
    proc = subprocess.Popen(command, cwd=str(cwd))
    _ACTIVE["proc"] = proc
    try:
        code = proc.wait()
        if code:
            raise RuntimeError(f"codex exec exited {code}")
    finally:
        _ACTIVE.pop("proc", None)

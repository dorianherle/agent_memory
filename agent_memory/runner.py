"""Auto-run one goal: write failing pytests, then spawn until they pass."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
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

CODEX_MODELS_FALLBACK = (
    {"id": "gpt-6-astra", "name": "GPT-6 Astra"},
    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol"},
    {"id": "gpt-5.6-terra", "name": "GPT-5.6 Terra"},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna"},
    {"id": "gpt-5.5", "name": "GPT-5.5"},
    {"id": "gpt-5.2", "name": "GPT-5.2"},
)
DEFAULT_CODEX_MODEL = "gpt-5.6-luna"


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
        f"Goal [{record['id']}] {record.get('goal') or record['title']}\n"
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
        "Treat every explicit requirement in the Goal and latest Steering "
        "section as required behavior. Before implementation, add a regression "
        "test for every requirement that is not already covered, and confirm "
        "that test fails for the "
        "reported reason. Then make the failing tests pass. Do not retry an "
        "approach that produced this same failure. Do not weaken the tests."
    )


def review_prompt(record: dict, packet: str, pytest_result: dict) -> str:
    return (
        f"{packet}\n\n"
        f"Last pytest ({pytest_result.get('summary', '')}):\n"
        f"{pytest_result.get('tail', '')}\n\n"
        "You are the Thinker. Review why the last coder failed. Refine the "
        "failing tests or reject the failed approach. Do not implement the "
        "feature. Tests must still fail for a real reason."
    )


def _role(last: dict, prior_role: str) -> str:
    if not last or last.get("ok") is True:
        return "thinker"
    return "coder" if prior_role == "thinker" else "thinker"


def _phase_for(role: str, last: dict) -> str:
    if role == "coder":
        return "work"
    return "review" if last.get("ok") is False else "bootstrap"


def _call_worker(worker: Worker, prompt: str, cwd: Path, **extra) -> None:
    try:
        worker(prompt, cwd, **extra)
    except TypeError:
        worker(prompt, cwd)


def _save_observe(workspace: Path, agent: int, result: dict, next_step: str) -> None:
    memory = Memory(workspace)
    verdict = "pass" if result["ok"] else "fail"
    memory.save(
        f"Agent {agent} pytest {verdict}: {result['summary']}\n\n"
        f"{result['tail']}\n"
        f"Next: {next_step}",
        agent="runner",
    )


def _status_payload(
    record: dict,
    agent: int,
    max_agents: int,
    phase: str,
    result: dict,
    state: str,
    *,
    role: str = "",
    model: str = "",
    worker: str = "",
    turns: list | None = None,
    thinker_model: str = "",
    coder_model: str = "",
) -> dict:
    return {
        "goal_id": record["id"],
        "goal": record["title"],
        "workspace": record["workspace"],
        "branch": record.get("branch") or "",
        "agent": agent,
        "max_agents": max_agents,
        "phase": phase,
        "role": role,
        "model": model,
        "worker": worker,
        "thinker_model": thinker_model,
        "coder_model": coder_model,
        "turns": list(turns or []),
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
    on_turn: Callable[[dict], None] | None = None,
    on_live: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    thinker_model: str = "",
    coder_model: str = "",
    worker_name: str = "",
) -> dict:
    """Create or resume a goal, then loop Thinker ↔ Coder until green or max."""
    project = Path(project).resolve()
    last: dict = {}
    start_agent = 0
    pending_steering = None
    prior_role = ""
    turns: list = []
    thinker_model = thinker_model or DEFAULT_CODEX_MODEL
    coder_model = coder_model or DEFAULT_CODEX_MODEL
    if goal_id:
        record = resolve_goal(project, goal_id)
        prior = read_status(project, record["id"])
        last = dict(prior.get("pytest") or {})
        start_agent = int(prior.get("agent", -1)) + 1
        pending_steering = prior.get("steering_pending")
        prior_role = prior.get("role") or ""
        turns = list(prior.get("turns") or [])
        thinker_model = prior.get("thinker_model") or thinker_model
        coder_model = prior.get("coder_model") or coder_model
    else:
        if not text:
            raise ValueError("goal text is required")
        record = create_goal(project, text)
    workspace = Path(record["workspace"])
    phase = "bootstrap"
    role = "thinker"
    model = thinker_model
    for agent in range(start_agent, max_agents):
        role = _role(last, prior_role)
        phase = _phase_for(role, last)
        model = thinker_model if role == "thinker" else coder_model
        packet = bind_packet(project, record["id"])
        if role == "coder":
            prompt = work_prompt(record, packet, last)
        elif last.get("ok") is False:
            prompt = review_prompt(record, packet, last)
        else:
            prompt = bootstrap_prompt(record)
        if cancelled and cancelled():
            status = _status_payload(
                record, max(agent - 1, 0), max_agents, phase, last, "stopped",
                role=prior_role or role, model=model, worker=worker_name,
                turns=turns, thinker_model=thinker_model, coder_model=coder_model,
            )
            write_status(project, record["id"], status)
            if on_observe:
                on_observe(status)
            return status
        if on_turn:
            on_turn({
                "agent": agent, "role": role, "model": model, "phase": phase,
                "max_agents": max_agents, "state": "running",
                "goal_id": record["id"], "goal": record["title"],
                "workspace": str(workspace),
            })
        def _line(text, _agent=agent, _role=role, _model=model, _phase=phase):
            if on_live:
                on_live({
                    "type": "live", "text": text, "agent": _agent,
                    "role": _role, "model": _model, "phase": _phase,
                })
        _call_worker(worker, prompt, workspace, model=model, on_line=_line)
        last = observe_pytest(workspace, pytest_args)
        if role == "thinker":
            next_step = (
                "rewrite failing acceptance tests" if last["ok"]
                else "make the failing tests pass"
            )
            state = "running"
        elif last["ok"]:
            next_step = "done"
            state = "passed"
        else:
            next_step = "spawn next agent on the same tree"
            state = "running"
        _save_observe(workspace, agent, last, next_step)
        turns.append({
            "agent": agent, "role": role, "phase": phase,
            "ok": last.get("ok"), "summary": last.get("summary", ""),
            "model": model, "worker": worker_name,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        status = _status_payload(
            record, agent, max_agents, phase, last, state,
            role=role, model=model, worker=worker_name, turns=turns,
            thinker_model=thinker_model, coder_model=coder_model,
        )
        if pending_steering:
            if state == "passed":
                status["steering_applied"] = pending_steering
            else:
                status["steering_pending"] = pending_steering
        write_status(project, record["id"], status)
        if on_observe:
            on_observe(status)
        if state == "passed":
            return status
        prior_role = role
    status = _status_payload(
        record, max_agents - 1, max_agents, phase, last, "stopped",
        role=role, model=model, worker=worker_name, turns=turns,
        thinker_model=thinker_model, coder_model=coder_model,
    )
    write_status(project, record["id"], status)
    if on_observe:
        on_observe(status)
    return status


def list_codex_models(*, run=subprocess.run) -> list[dict]:
    try:
        result = run(
            ["codex", "debug", "models", "--bundled"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        data = json.loads(result.stdout or "{}")
        rows = [
            {
                "id": item["slug"],
                "name": item.get("display_name") or item["slug"],
            }
            for item in data.get("models") or []
            if item.get("slug") and item.get("visibility", "list") == "list"
        ]
        if rows:
            return rows
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return [dict(row) for row in CODEX_MODELS_FALLBACK]


def cursor_worker(
    prompt: str,
    cwd: Path,
    api_key: str | None = None,
    *,
    model: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> None:
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
        AgentConfig(project=str(cwd), model=model or DEFAULT_MODEL, api_key=key)
    )
    try:
        run = agent.send(prompt)
        if on_line:
            for event in run.events():
                update = getattr(event, "interaction_update", None)
                if getattr(update, "type", None) == "text-delta":
                    text = (getattr(update, "text", "") or "").strip()
                    if text:
                        on_line(text[:200])
                msg = getattr(event, "sdk_message", None)
                if msg is None:
                    continue
                kind = getattr(msg, "type", None)
                if kind == "status":
                    note = (getattr(msg, "message", "") or "").strip()
                    if note:
                        on_line(note[:200])
                elif kind == "tool_call":
                    on_line(f"{getattr(msg, 'name', 'Tool')} · {getattr(msg, 'status', '')}"[:200])
        run.wait()
    finally:
        close_agent(agent)


def codex_command(prompt: str, cwd: Path, model: str | None = None) -> list[str]:
    command = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
    ]
    if model:
        command += ["--model", model]
    command += ["--cd", str(cwd), prompt]
    return command


def _codex_bin(*, which=shutil.which) -> str | None:
    return which("codex")


def codex_auth_present(path: str | Path | None = None) -> bool:
    auth = Path(path or Path.home() / ".codex" / "auth.json")
    if not auth.is_file():
        return False
    try:
        data = json.loads(auth.read_text())
    except (OSError, ValueError):
        return False
    tokens = data.get("tokens") or {}
    return bool(
        data.get("OPENAI_API_KEY")
        or tokens.get("access_token")
        or tokens.get("id_token")
    )


def codex_cli_error(*, which=shutil.which, run=subprocess.run) -> str | None:
    exe = _codex_bin(which=which)
    if not exe:
        return "codex is not on PATH. Install Codex CLI, then retry."
    result = run([exe, "--version"], capture_output=True, text=True)
    if result.returncode == 0:
        return None
    err = (result.stderr or result.stdout or "codex --version failed").strip()
    return f"Codex CLI is installed but broken: {err[:300]}"


def codex_logged_in(*, which=shutil.which, run=subprocess.run) -> bool:
    if codex_cli_error(which=which, run=run):
        return False
    result = run(
        ["codex", "login", "status"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 or codex_auth_present()


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


def start_codex_login(
    *,
    popen=subprocess.Popen,
    which=shutil.which,
    run=subprocess.run,
    wait=time.sleep,
) -> dict:
    broken = codex_cli_error(which=which, run=run)
    if broken:
        extra = (
            " A ChatGPT session is already saved. Reinstall (`npm i -g @openai/codex`) so agents can launch."
            if codex_auth_present()
            else " Reinstall with `npm i -g @openai/codex`."
        )
        raise RuntimeError(broken + extra)
    if codex_auth_present():
        return {"ok": True, "hint": "Already signed in. Close Settings and Run a goal."}
    proc = popen(
        ["codex", "login"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wait(1.0)
    if proc.poll() is not None:
        err = ""
        if proc.stderr:
            err = proc.stderr.read()
        raise RuntimeError(
            "codex login exited without opening a browser: "
            + (err or f"exit {proc.returncode}").strip()[:400]
        )
    return {"ok": True, "hint": "Finish ChatGPT login in the browser, then refresh."}


def codex_worker(prompt: str, cwd: Path, *, model: str | None = None, run=None, on_line=None) -> None:
    command = codex_command(prompt, cwd, model=model)
    if run is not None:
        result = run(command, cwd=str(cwd))
        if getattr(result, "returncode", 0):
            raise RuntimeError(f"codex exec exited {result.returncode}")
        return
    # ponytail: line-split stdout, not a Codex event parser
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _ACTIVE["proc"] = proc
    try:
        stream = proc.stdout
        if stream is not None:
            for raw in stream:
                text = raw.strip()[:200]
                if text and on_line:
                    on_line(text)
        code = proc.wait()
        if code:
            raise RuntimeError(f"codex exec exited {code}")
    finally:
        _ACTIVE.pop("proc", None)

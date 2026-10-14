"""One unused local agent, prepared without sending a model prompt."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError, CancelledError
from dataclasses import dataclass, field
import threading
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Callable, Any

DEFAULT_MODEL = "grok-4.5"


def model_selection(model: str) -> dict:
    selection = {"id": model}
    if model == DEFAULT_MODEL:
        selection["params"] = [
            {"id": "effort", "value": "high"},
            {"id": "fast", "value": "true"},
        ]
    return selection


@dataclass(frozen=True)
class AgentConfig:
    project: str
    model: str
    api_key: str = field(repr=False)


_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def workspace_client():
    """One bridge and executor cache shared by every fresh Studio agent."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            import atexit
            from cursor_sdk import CursorClient
            from cursor_sdk._vendor import resolve_bridge_path
            bridge = Path(resolve_bridge_path()).resolve().parent.parent
            _CLIENT = CursorClient.launch_bridge(command=[
                str(bridge / "bin" / "node"),
                str(Path(__file__).with_name("studio_bridge.mjs")),
                str(bridge / "dist" / "bin" / "cursor-sdk-bridge.js"),
            ])
            atexit.register(_CLIENT.close)
        return _CLIENT


def create_agent(config: AgentConfig):
    from cursor_sdk import Agent, LocalAgentOptions
    # The SDK discovers project settings at the enclosing Git root. That can
    # silently connect a nested workspace to its parent's notebook and hooks.
    # Load this folder's MCP servers explicitly; Studio owns turn durability.
    settings = Path(config.project) / ".cursor" / "mcp.json"
    servers = json.loads(settings.read_text()).get("mcpServers", {}) if settings.exists() else {}
    servers["agent-memory"] = {
        **servers.get("agent-memory", {}),
        "command": sys.executable,
        "args": ["-m", "agent_memory.cursor.mcp", "--root", config.project],
    }
    return Agent.create(
        {"mcp_servers": servers},
        client=workspace_client(),
        api_key=config.api_key,
        model=model_selection(config.model),
        local=LocalAgentOptions(cwd=config.project, setting_sources=[]),
    )


def close_agent(agent) -> None:
    try:
        agent.close()
    except Exception:
        pass


class StandbyAgent:
    """At most one available agent; each handle is consumed only once.

    Changing project/key/model invalidates the standby. An obsolete in-flight
    creation closes its own result rather than publishing it to the new config.
    No question, chat history, or model inference is used to warm the handle.
    """
    def __init__(self, factory: Callable[[AgentConfig], Any] = create_agent):
        self._factory = factory
        self._lock = threading.Lock()
        self._job: Future | None = None
        self._config: AgentConfig | None = None

    def _discard_locked(self):
        job, self._job = self._job, None
        self._config = None
        if job is None:
            return None
        if not job.done():
            job.cancel()
        elif not job.cancelled() and job.exception() is None:
            return job.result()[0]
        return None

    def clear(self):
        with self._lock:
            old = self._discard_locked()
        if old is not None:
            close_agent(old)

    def warm(self, config: AgentConfig) -> Future:
        with self._lock:
            if self._job is not None and self._config == config:
                return self._job
            old = self._discard_locked()
            job = self._job = Future()
            self._config = config
        if old is not None:
            close_agent(old)

        def prepare():
            start = perf_counter()
            try:
                agent = self._factory(config)
            except Exception as error:
                with self._lock:
                    if not job.cancelled():
                        job.set_exception(error)
                return
            with self._lock:
                keep = self._job is job and not job.cancelled()
                if keep:
                    job.set_result((agent, (perf_counter() - start) * 1000))
            if not keep:
                close_agent(agent)

        threading.Thread(target=prepare, name="studio-standby", daemon=True).start()
        return job

    def take(self, config: AgentConfig, *, cancelled=lambda: False):
        job = self.warm(config)
        ready = job.done() and not job.cancelled() and job.exception() is None
        start = perf_counter()
        try:
            while True:
                if cancelled():
                    raise CancelledError("Agent start cancelled.")
                try:
                    agent, preparation_ms = job.result(timeout=0.05)
                    break
                except TimeoutError:
                    continue
        except Exception:
            old = None
            with self._lock:
                if self._job is job:
                    old = self._discard_locked()
            if old is not None:
                threading.Thread(target=close_agent, args=(old,), daemon=True).start()
            raise
        with self._lock:
            if self._job is not job:
                raise RuntimeError("Project or model changed while preparing the agent.")
            self._job = None
            self._config = None
        return agent, {
            "standby_ready": ready,
            "agent_wait_ms": round((perf_counter() - start) * 1000, 1),
            "preparation_ms": round(preparation_ms, 1),
        }

    def status(self) -> str:
        with self._lock:
            if self._job is None:
                return "idle"
            if not self._job.done():
                return "preparing"
            if self._job.cancelled() or self._job.exception() is not None:
                return "unavailable"
            return "ready"

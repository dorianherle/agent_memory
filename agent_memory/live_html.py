"""Self-updating HTML live memory monitor.

Writes into ``.agent_memory/live/``:

- ``index.html`` — UI shell (created once; rewritten when the template version changes)
- ``data.js`` — latest graph payload (rewritten on every durable memory event)
- ``actions.js`` — recent tool calls, including retrievals (rewritten on each call)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# Bump when live_shell.html changes so existing installs pick up the new shell.
SHELL_VERSION = "24"
SHELL_PATH = Path(__file__).with_name("live_shell.html")

# ponytail: last 40 tool calls; raise the cap if one session outgrows the card
ACTIONS_CAP = 40


def default_live_dir(memory) -> Path:
    """Directory for the browser live monitor (inside the memory git dir)."""
    return Path(memory._git_dir) / "live"


def _actions_path(live_dir: Path) -> Path:
    return Path(live_dir) / "actions.json"


def load_actions(live_dir: Path) -> list:
    path = _actions_path(live_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)][-ACTIONS_CAP:]


def _write_actions_js(live_dir: Path, rows: list) -> None:
    encoded = json.dumps(rows).replace("</", "<\\/")
    (Path(live_dir) / "actions.js").write_text(
        f"window.__AGENT_MEMORY_ACTIONS__ = {encoded};\n",
        encoding="utf-8",
    )


def record_action(memory, action: str, detail: str = "") -> None:
    """Append one tool call for the live page. Failures are ignored."""
    try:
        live = default_live_dir(memory)
        live.mkdir(parents=True, exist_ok=True)
        rows = load_actions(live)
        rows.append(
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action": str(action or "")[:40],
                "detail": str(detail or "").replace("\n", " ")[:140],
            }
        )
        rows = rows[-ACTIONS_CAP:]
        _actions_path(live).write_text(
            json.dumps(rows, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_actions_js(live, rows)
    except Exception:
        return


def _shell_version(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(
        r'name="agent-memory-live-shell"\s+content="([^"]+)"',
        path.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None


def write_live_html(
    memory,
    path: str | Path | None = None,
    *,
    force_shell: bool = False,
) -> Path:
    """Write / refresh the self-updating HTML live monitor. Returns index path."""
    from agent_memory.live_reveal import ensure_reveal_server
    from agent_memory.turn_prompts import attach_turn_prompts
    from agent_memory.viz import compact_graph, graph_data

    out_dir = Path(path) if path is not None else default_live_dir(memory)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = compact_graph(graph_data(memory))
    attach_turn_prompts(payload["nodes"], out_dir)
    payload["prompt"] = memory.prompt_payload()
    try:
        stale_map = memory._stale_files(memory.state())
    except Exception:
        stale_map = {}
    for node in payload.get("nodes", []):
        drifted = stale_map.get(node.get("id") or "", [])
        if drifted:
            node["stale"] = True
            node["stale_files"] = drifted[:4]
    for finding in (payload.get("prompt") or {}).get("findings") or []:
        drifted = stale_map.get(finding.get("id") or "", [])
        if drifted:
            finding["stale_files"] = drifted[:4]
    payload["root"] = str(Path(memory._path).resolve())
    try:
        payload["reveal"] = ensure_reveal_server(
            out_dir, root=Path(memory._path).resolve()
        )
    except Exception:
        payload["reveal"] = None
    if payload.get("reveal"):
        payload["working"] = str(payload["reveal"]).rsplit("/", 1)[0] + "/working"
    payload["actions"] = load_actions(out_dir)
    _write_actions_js(out_dir, payload["actions"])
    encoded = json.dumps(payload, indent=2).replace("</", "<\\/")
    (out_dir / "data.js").write_text(
        f"window.__AGENT_MEMORY_LIVE__ = {encoded};\n",
        encoding="utf-8",
    )

    index = out_dir / "index.html"
    need_shell = (
        force_shell
        or not index.exists()
        or _shell_version(index) != SHELL_VERSION
    )
    if need_shell:
        template = SHELL_PATH.read_text(encoding="utf-8")
        index.write_text(
            template.replace("__SHELL_VERSION__", SHELL_VERSION),
            encoding="utf-8",
        )
    return index

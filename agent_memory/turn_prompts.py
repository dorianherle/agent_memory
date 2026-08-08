"""Sidecar: exact goal + injected MEMORY STATE per user turn (for live HTML).

Stored outside the git memory commits so injection snapshots do not bloat the
commit graph. Keyed by entry id; pruned to ids still present in the graph.
"""

from __future__ import annotations

import json
from pathlib import Path

TURN_PROMPTS_NAME = "turn_prompts.json"


def turn_prompts_path(live_dir: Path) -> Path:
    return Path(live_dir) / TURN_PROMPTS_NAME


def record_turn_prompt(
    live_dir: Path,
    entry_id: str,
    *,
    goal: str,
    injected: str,
) -> Path:
    """Remember the exact prompt packet sent for this user entry."""
    live_dir = Path(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    path = turn_prompts_path(live_dir)
    data = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except json.JSONDecodeError:
            data = {}
    data[entry_id] = {
        "goal": goal,
        "injected": injected,
        "packet": f"{goal.strip()}\n\n{injected.strip()}\n",
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_turn_prompts(live_dir: Path) -> dict[str, dict]:
    path = turn_prompts_path(live_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def attach_turn_prompts(nodes: list[dict], live_dir: Path) -> None:
    """Merge sidecar packets onto matching nodes; prune stale ids."""
    turns = load_turn_prompts(live_dir)
    if not turns:
        return
    alive = {node.get("id") for node in nodes}
    changed = False
    for node in nodes:
        packet = turns.get(node.get("id") or "")
        if not packet:
            continue
        node["goal"] = packet.get("goal") or node.get("body") or ""
        node["injected"] = packet.get("injected") or ""
        node["packet"] = packet.get("packet") or ""
    stale = [key for key in turns if key not in alive]
    if stale:
        for key in stale:
            turns.pop(key, None)
        changed = True
    if changed:
        turn_prompts_path(live_dir).write_text(
            json.dumps(turns, indent=2) + "\n", encoding="utf-8"
        )

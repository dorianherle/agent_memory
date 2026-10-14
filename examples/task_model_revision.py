"""Verify Git-backed source-claim staleness detection and evidence revision.

Run with:
    .venv/bin/python examples/task_model_revision.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory


def fingerprint(root: Path, relative_path: str) -> dict:
    path = root / relative_path
    if not path.exists():
        return {"exists": False, "sha256": None}
    data = path.read_bytes()
    return {"exists": True, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def run(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "review-policy.txt").write_text(
        "Review a source claim when its declared or referenced source state changes.\n"
    )
    memory = Memory(root)
    memory.save_user("Determine which source claims need a new check.\nKeep old evidence and Git versions.")

    checks = []
    notes = {}
    for artifact, original, modified in (
        ("app.py", "workers = 6\n", "workers = 18\n"),
        ("config.json", '{"region":"eu"}\n', '{"region":"us"}\n'),
        ("calibration.json", None, '{"rate":12}\n'),
    ):
        if original is not None:
            (root / artifact).write_text(original)
        before = fingerprint(root, artifact)
        note_id = memory.save(
            f"Verified {artifact} state: {json.dumps(before)}",
            {"files": [artifact], "handoff": True},
        )
        notes[artifact] = note_id
        # Before modifying the file, the claim is fresh (not stale).
        packet_before = memory.render_packet(artifact)
        assert f"[{note_id[:8]}] [stale]" not in packet_before
        checks.append({"artifact": artifact, "case": "unchanged", "stale": False, "pass": True})

        # Modify or create the file on disk: claim must become [stale].
        (root / artifact).write_text(modified)
        packet_after = memory.render_packet(artifact)
        assert f"[{note_id[:8]}] [stale]" in packet_after
        checks.append({"artifact": artifact, "case": "changed", "stale": True, "pass": True})

    summary_id = memory.summarize(
        "Completed source drift checks across app.py, config.json, and calibration.json."
    )
    fresh = Memory(root)
    packet = fresh.render_packet("source drift checks")
    assert len(packet) <= 1200
    assert summary_id[:8] in packet
    output = {
        "kind": "actual filesystem integration check",
        "summary_id": summary_id,
        "checks": checks,
        "passed": len(checks),
        "total": len(checks),
        "packet_chars": len(packet),
        "packet": packet,
    }
    (root / "result.json").write_text(json.dumps(output, indent=2) + "\n")
    fresh._catalog.close()
    memory._catalog.close()
    print(json.dumps({"passed": len(checks), "total": len(checks), "report": str(root / "result.json")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="task-model-example-")) / "workspace"
    )
    run(root)

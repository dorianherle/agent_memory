"""Revise a Git-backed model using actual software source-file changes.

Run with .venv/bin/python examples/task_model_revision.py --output NEW_DIRECTORY.
This is an executable integration example, not an LLM performance benchmark.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory
from agent_memory.models import fingerprint


def expr(name, *args):
    return {"op": name, "args": list(args)}


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    (root / "review-policy.txt").write_text("Review a source claim when its declared source state changes.\n")
    memory = Memory(root)
    memory.save_user("Determine which source claims need a new check.\nKeep old evidence and Git versions.")
    model = {"title": "Source claim review", "inputs": {"artifact": "string", "before": "object", "after": "object"},
             "rules": [], "cases": [], "unresolved": []}
    observations = []
    for artifact, original, modified in (("app.py", "workers = 6\n", "workers = 18\n"),
                                         ("config.json", '{"region":"eu"}', '{"region":"us"}'),
                                         ("calibration.json", None, '{"rate":12}')):
        if original is not None:
            (root / artifact).write_text(original)
        before = fingerprint(root, artifact)
        observations.append({"id": artifact + "-same", "target": "needs_review",
                             "inputs": {"artifact": artifact, "before": before, "after": fingerprint(root, artifact)},
                             "expected": False, "challenge": False})
        (root / artifact).write_text(modified)
        observations.append({"id": artifact + "-changed", "target": "needs_review",
                             "inputs": {"artifact": artifact, "before": before, "after": fingerprint(root, artifact)},
                             "expected": True, "challenge": True})
    evidence = memory.save("Source review observations from actual file changes.\n" + json.dumps(observations, indent=2))
    for case in observations:
        model["cases"].append({**case, "evidence": [evidence]})
    for artifact in ("app.py", "config.json", "calibration.json"):
        model["rules"].append({"id": artifact, "target": "needs_review", "claim": f"Review {artifact} after a source change.",
                               "when": expr("eq", {"var": "artifact"}, {"value": artifact}),
                               "then": expr("ne", {"var": "before"}, {"var": "after"}),
                               "dependencies": ["review-policy.txt"], "evidence": [evidence]})
    before_packet = memory.render_packet("Source review observations")
    proposal = memory.model_propose(model)
    assert memory.model_check(proposal)["status"] == "tested"
    first = memory.model_accept(proposal)
    first_model = memory.model_get()
    reduction = memory.model_reduce()
    assert reduction["status"] == "proposed"
    second = memory.model_accept(reduction["id"])
    second_model = memory.model_get()
    # A generalized rule is a separate, tested proposal; the reducer does not
    # silently remove the conditions that limited the earlier model.
    generalized = copy.deepcopy(second_model["model"])
    generalized["rules"][0]["when"] = {"value": True}
    generalized["rules"][0]["claim"] = "Review when a declared source fingerprint changes."
    before = fingerprint(root, "new-settings.json")
    (root / "new-settings.json").write_text('{"workers":8}')
    after = fingerprint(root, "new-settings.json")
    note = memory.save("A newly created settings source needs a claim review.")
    generalized["cases"].append({"id": "new-source", "target": "needs_review",
                                  "inputs": {"artifact": "new-settings.json", "before": before, "after": after},
                                  "expected": True, "challenge": True, "evidence": [note]})
    third_proposal = memory.model_propose(generalized, base=second)
    third = memory.model_accept(third_proposal)
    # Holdout checks use source names and contents that did not enter the cases.
    checks = []
    for index in range(8):
        artifact = f"worker-{index}.json"
        path = root / artifact
        old = fingerprint(root, artifact)
        path.write_text(json.dumps({"worker": index, "enabled": True}))
        new = fingerprint(root, artifact)
        for label, a, b, expected in (("created", old, new, True), ("unchanged", new, new, False)):
            result = memory.model_predict({"artifact": artifact, "before": a, "after": b}, "needs_review")
            assert result["status"] == "predicted" and result["value"] is expected
            checks.append({"artifact": artifact, "case": label, "pass": True})
        path.write_text(json.dumps({"worker": index, "enabled": False}))
        changed = fingerprint(root, artifact)
        result = memory.model_predict({"artifact": artifact, "before": new, "after": changed}, "needs_review")
        assert result["value"] is True
        checks.append({"artifact": artifact, "case": "changed", "pass": True})
        path.unlink()
        result = memory.model_predict({"artifact": artifact, "before": changed,
                                       "after": fingerprint(root, artifact)}, "needs_review")
        assert result["value"] is True
        checks.append({"artifact": artifact, "case": "deleted", "pass": True})
    fresh = Memory(root)
    assert fresh.model_get()["id"] == third
    assert fresh.find(ids=evidence)[0].message.startswith("Source review observations")
    packet = fresh.render_packet("Source review observations")
    assert len(packet) <= 1200
    assert evidence[:8] not in packet
    assert "## Task model" in packet
    output = {"kind": "actual filesystem integration check; no LLM performance measurement",
              "models": [{"id": first, "rules": 3, "complexity": first_model["complexity"]},
                         {"id": second, "rules": 1, "complexity": second_model["complexity"]},
                         {"id": third, "rules": 1, "complexity": fresh.model_get()["complexity"]}],
              "heldout_checks": checks, "passed": len(checks), "total": len(checks),
              "retained_cases": len(fresh.model_get()["model"]["cases"]),
              "retained_evidence": bool(fresh.find(ids=evidence)),
              "before_packet_chars": len(before_packet), "after_packet_chars": len(packet),
              "packet": packet}
    (root / "result.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"passed": len(checks), "total": len(checks), "report": str(root / "result.json")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="task-model-example-")) / "workspace"
    run(root)

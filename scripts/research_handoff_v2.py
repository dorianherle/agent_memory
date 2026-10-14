"""Prepare a new study with a common output schema in every condition.

Version 1 and its frozen grader stay unchanged. This study tests method recovery
after all agents receive the same function and report field names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from research_handoff import prepare, write_json


CONTRACTS = {
    "convergence": """Common API contract: assess(records, config) returns a dictionary with
observed_order (number), extrapolated (number), relative_error (number), and
qualified (boolean). main() writes these fields to report.json. Records contain
intervals, length, and eigenvalue. Config contains length, tolerance, order_min,
and order_max. Recover the research method from the available context.
""",
    "vector_error": """Common API contract: field_error(predicted, reference) returns a finite
nonnegative number. Inputs are complex one-dimensional vectors. main() reads
[real, imag] pairs from fields.json and writes field_error (number) and qualified
(boolean) to report.json. Policy contains field_error_limit. Recover the
research method from the available context.
""",
    "runtime_audit": """Common API contract: summarize(rows, policy) returns a dictionary with
attempted (integer), qualified (integer), median_attempt_ms (number),
median_qualified_speedup (number or null), median_inference_speedup (number),
baseline_contract (string), and claim (string). main() writes these fields to
report.json. Rows contain load_ms, predict_ms, correct_ms, verify_ms, output_ms,
training_ms, baseline_ms, and residual. matrix_id is optional metadata. Policy
contains residual_limit. Valid claim labels are no_qualified_speedup,
accuracy_limited, faster, and slower_or_equal. Recover the timing and accuracy
method from the available context.
""",
}


def prepare_v2(output: Path) -> None:
    prepare(output)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["protocol_version"] = 2
    manifest["difference_from_v1"] = "Common input and output schemas supplied to every condition."
    for row in manifest["rows"]:
        task = output / row["workspace"] / "TASK.md"
        task.write_text(task.read_text() + "\n" + CONTRACTS[row["case"]])
        row["input_sha256"]["TASK.md"] = hashlib.sha256(task.read_bytes()).hexdigest()
    write_json(output / "manifest.json", manifest)
    root = Path(__file__).resolve().parents[1]
    frozen = ["scripts/research_handoff.py", "scripts/research_handoff_v2.py",
              "scripts/grade_research_handoff.py", "agent_memory/core.py",
              "agent_memory/prompt.py", "agent_memory/tools.py"]
    write_json(output / "protocol-freeze.json", {
        "protocol": "research-handoff-v2",
        "status": "frozen before fresh-agent evaluation",
        "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in frozen},
        "assessment": "Same executable grader as version 1; no feedback during a trial.",
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    prepare_v2(parser.parse_args().output)

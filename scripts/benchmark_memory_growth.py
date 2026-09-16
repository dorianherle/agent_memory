"""Measure storage/prompt growth on a disposable small repository, offline.

Run: .venv/bin/python scripts/benchmark_memory_growth.py --sizes 10,100,500
These microbenchmarks do not measure agent success or large-worktree scanning.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_memory import Memory


def elapsed_ms(operation):
    started = time.perf_counter()
    operation()
    return (time.perf_counter() - started) * 1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="10,100,500")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sizes = sorted(set(int(x) for x in args.sizes.split(",")))
    if not sizes or sizes[0] < 1 or args.samples < 1:
        parser.error("sizes and samples must be positive")
    for key in list(os.environ):
        if key.startswith("AGENT_MEMORY_") or key == "GEMINI_API_KEY":
            os.environ.pop(key)
    os.environ["AGENT_MEMORY_NO_REVEAL"] = "1"
    observations = []
    with tempfile.TemporaryDirectory(prefix="agent-memory-growth-") as folder:
        root = Path(folder)
        (root / "work.txt").write_text("Small unchanged synthetic worktree\n")
        memory = Memory(root)
        saves = []

        def reopen():
            reopened = Memory(root)
            reopened._catalog.close()

        for count in range(1, sizes[-1] + 1):
            saves.append(elapsed_ms(lambda: memory.save(
                f"Observation {count}: parser invariant verified\n\n"
                "Synthetic evidence: parser fixtures remain compatible. " * 5,
            )))
            if count not in sizes:
                continue
            row = {"records": count}
            row["save_last_10_median_ms"] = round(statistics.median(saves[-10:]), 3)
            operations = {
                "reopen": reopen,
                "render_state": memory.render_state,
                "literal_find": lambda: memory.find(text="parser", k=5),
                "live_export": memory.write_live_html,
            }
            for name, operation in operations.items():
                timings = [elapsed_ms(operation) for _ in range(args.samples)]
                row[f"{name}_median_ms"] = round(statistics.median(timings), 3)
            row["state_characters"] = len(memory.render_state())
            row["live_data_bytes"] = (root / ".agent_memory/live/data.js").stat().st_size
            observations.append(row)
            print(json.dumps(row), flush=True)
        memory._catalog.close()
    report = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "samples_per_read_operation": args.samples,
        "worktree": "one small unchanged text file; no provider; no summaries",
        "observations": observations,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

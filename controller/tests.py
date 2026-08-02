"""The test suite, run as a subprocess and read back as two lists of names.

This is the only objective signal in the loop. Everything the memory derives
(the frontier, the pivot, the loop detector) comes from these two lists, so
they go into every record() call.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# `test_file.py::test_name PASSED   [ 33%]`. The bare name is what memory's
# space-separated trailers can hold, so the file path is dropped.
_RESULT = re.compile(r"^\S+::(\S+)\s+(PASSED|FAILED|ERROR)", re.MULTILINE)


def run_pytest(repo_path: str | Path) -> tuple[list[str], list[str], str]:
    """Run the whole suite. Returns (passed, failed, output).

    The whole suite, not just the target tests, because a test traded away is
    only visible if it is still being measured.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--no-header"],
        cwd=repo_path, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr

    passed, failed = [], []
    for name, status in _RESULT.findall(output):
        (passed if status == "PASSED" else failed).append(name)
    return passed, failed, output

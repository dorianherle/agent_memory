# Private development review

This work stays private. No public repository, package, or website was created.
No changes were pushed to GitHub.

## Concrete use case

A research agent must continue an experiment after context loss. It must recover
the current goal, the research method, rejected approaches, and supporting
files. It must also reject a saved result after the experiment inputs change.

The cavity example demonstrates this sequence in a new Python process. It uses
numerical measurements with an analytical check. It makes no model calls and
does not establish an AI capability improvement.

The fresh-agent studies separately test method recovery. They use convergence,
complex-vector error, and complete solver timing. The [results](research-handoff-results.md)
and [procedure](research-handoff-study.md) state the controls and limits.

## Reliability and installation

The runner now checks a worker signature before it starts the experiment.
An internal TypeError propagates after one call. It cannot start the experiment
again through the compatibility fallback. Supported model options remain
available to workers with older signatures.

The package suite passed 259 tests. One live embedding-provider test was
skipped because no API key was supplied. One MCP test produced a Pydantic
forward-reference warning. The run used macOS and Python 3.14.

A private wheel built and installed. The installed package passed a local
save, find, and packet check. The Studio HTML and bridge files were present.
This installation used dependencies from the existing project environment.
It does not prove installation of all dependencies on a clean machine.

## Evidence that remains necessary

- Use tasks and acceptance checks from an independent research author.
- Run several actual agent handoffs over one experiment.
- Compare against a maintained research notebook with retrieval.
- Measure native model usage, elapsed time, and complete tool use.
- Verify a clean installation and tests on Linux and supported Python versions.
- Resolve the MCP warning and review concurrent workspace operations.
- Choose a license and inspect the intended public files before any release.

The current evidence supports recovery on these development tasks. It does not
prove general research improvement, long autonomous runs, or lower model cost.

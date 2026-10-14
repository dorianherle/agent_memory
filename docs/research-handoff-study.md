# Research recovery after context loss

## Research question

Can a fresh agent recover the original research contract from a bounded memory
packet, retrieve missing evidence, and complete the task correctly?

The comparator with complete history tests whether memory preserves useful
information. The equal-budget recent-history comparator tests loss of older
decisions under truncation. It does not represent every possible memory-free
agent or every context-management method.

## Tasks

1. **Numerical convergence.** Estimate the observed order and a Richardson
   extrapolation from a cavity grid study. Reject stale geometry and invalid
   grids. Regenerate changed geometry before reporting.
2. **Complex field error.** Normalize the whole predicted and reference
   vectors. Use one global phase alignment. Detect missing components and
   relative phase errors that a magnitude plot can hide.
3. **Runtime assessment.** Include load, prediction, correction, verification,
   and output in fresh solver construction time. Keep accuracy failures and
   pure-inference timing separate.

These are synthetic development tasks. Numerical inputs and acceptance tests
were designed for this study. They are not proprietary CSEM data or a sample
of production OpenAI research.

## Conditions and controls

- Three context conditions receive identical code and numerical inputs.
- Agent Memory uses the actual packet generator and retrieval API.
- The recent-history condition receives the chronological history tail with
  the same character budget as that case's memory packet.
- The complete-history condition receives every authored study note.
- Each fresh agent handles three separate task workspaces. Two fresh agents
  run each condition. The three tasks within one agent are a cluster.
- The agents have separate workspaces and explicit read limits. This is
  instruction-based isolation. The shared filesystem does not provide a
  claimed operating-system barrier against reading other conditions.
- The same inherited Codex model is used without an override. Native model
  usage and hidden reasoning tokens are unavailable in this harness.
- Agents receive no independent grader or reference control. The grader is
  frozen before the trials. No grader feedback is sent to an active trial.

The memory condition has retrieval tools because retrieval is part of the
method under test. The complete-history comparator needs no retrieval. The
recent-history comparator intentionally cannot recover the discarded history.

## Independent assessment

Each case has ten function checks and one executable current-data report
check. The checks use inputs absent from the agent task data. They include
invalid values, phase changes, scale changes, missing field components,
first-order convergence, changed geometry, and failed accuracy cases.

Correct reference controls must pass all checks. Unfinished stubs must fail
all checks. These controls validate the grader before it scores agent output.
They do not make the development tasks a held-out scientific domain.

All original agent candidates and scores remain preserved. A later repair
must be recorded as a separate development result.

## Measurement limits

The token metric counts only initial evidence and explicit retrieved evidence
with `cl100k_base`. It is a proxy for visible text. It excludes common task
instructions, repeated model requests, other tool output, hidden prompts,
reasoning, provider caches, and billing. It is not a total-token or cost claim.

Task success and function-check counts are reported separately. Two repeats
are too few for a general reliability claim. Cases are not independent within
one agent. The procedure does not establish hours-long autonomous research,
LLM training improvements, or a production research productivity gain.

## Reproduce

```sh
python -m pip install -e '.[test,benchmark]'
python scripts/research_handoff.py --output /tmp/research-handoff-study
python scripts/check_research_grader.py --output /tmp/grader-controls.json
```

Start a fresh agent for each condition and trial. Use the three case directories
under `workspaces/trial-N/CONDITION`. Supply the corresponding `TASK.md`,
`context.txt`, and current data. Permit only `study.py`, local tests,
`report.json`, and `HANDOFF.md` to change. The convergence case can regenerate
`samples.json` through the supplied `measure.py`.

For the memory condition, expose `memory_read.py` with `--id`, `--text`,
`--query`, and `--full`. Disable remote embedding configuration for this test.
Use the same model and tool policy for all conditions. Keep the grader and
other condition directories outside the agent's evidence.

After all agents finish:

```sh
python scripts/grade_research_handoff.py /tmp/research-handoff-study
```

The study directory contains `manifest.json`, original input hashes, candidate
code, reports, retrieval logs, and the complete independent score.

The next stronger study must use tasks from an independent author, multiple
actual agent handoffs, a fixed context-management comparator, native usage
accounting, and more trials. Freeze its procedure before running it.

## Version 2: common API schema

Version 1 preserved the output schema in historical notes. Some recent-context
failures were missing dictionary keys. Its score measures recovery of the
complete contract, including field names. It is not a numerical accuracy score.

Version 2 supplies all required input and output field names to every agent in
TASK.md. It also states that matrix_id is optional. The scientific method stays
in the condition-specific context. The executable grader is unchanged. Fresh
agents run in new workspaces. Version 1 candidates and results stay preserved.

```sh
python scripts/research_handoff_v2.py --output /tmp/research-handoff-study-v2
# Start fresh agents under the same task and tool rules described above.
python scripts/grade_research_handoff.py /tmp/research-handoff-study-v2
python scripts/stamp_research_protocol.py /tmp/research-handoff-study-v2
python scripts/verify_research_study.py /tmp/research-handoff-study-v2
```

Version 2 is a development follow-up after inspection of version 1. It is not
an independent replication or a new held-out task set. Compare the two versions
separately. Do not pool their trials to suggest independent evidence.

The frozen scorer writes a version-1 metadata label. The stamp operation keeps
the raw output and assigns the manifest version to results.json. It changes no
candidate, check, score, or token count.

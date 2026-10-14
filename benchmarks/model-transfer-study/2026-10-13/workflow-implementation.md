# Model transfer implementation

The backend now has a combined model handoff operation. It proposes a model,
checks it, accepts a passing proposal, attempts reduction, and records a
challenge prediction. It records each operation. A failed check blocks
acceptance. A valid existing model can be reused without another Git revision.

Later agents can submit only new observations. The backend retains the old
rules and cases. It rejects changed old cases and blocks new observations that
contradict the rules. Conditions and unresolved questions remain in the model.
Complementary Boolean decisions can reduce to one conditional rule. Invalid
predicate types still produce an unknown result.

The Python API returns a complete audit. Generic and MCP tools return working
check summaries by default. Full results remain available. The study records
the full audit and the exact result given to the agent. Other tool output has
no text-length cap. Model JSON size is not a token-saving measurement.

All study methods can batch up to eight tools. Each operation counts toward
40 calls. The model workflow reserves seven slots before execution and charges
actual attempted operations. Memory results and model proposals can use local
JSON files. This avoids copying large rule trees into escaped commands.
Malformed agent decisions have up to three consecutive repair attempts. All
attempts remain in native token cost.

Verification has 83 passing tests. A separate code and research handoff run
passed all six stages with fresh agents and complete native token events.
Its 1,655,807 task-worker tokens are development cost. They are not a saving.
Source-rule replay passed 81 checks after correction of a checker input from
null to an empty evidence array. The original records remain available.

The first revised pilot stopped after a task-contract fault. Data instructions
did not specify accepted-record fields. The private grader rejected extra unit
metadata that the instructions did not forbid. Those grades cannot establish
an added failure. The run stays available as development evidence.

Every new task now has a public output schema. The instructions define
accepted-record fields, accepted-ID tracking, integer environment parsing, and
unknown research results. Reference outputs are checked against those schemas.
A controller stop file ends new dispatch after any current completion.

The scored pilot is pilot-v7. It has eight work samples, two repeats, three
methods, and three stages per run. Its report will use native input and output
tokens per fully completed task. Cached tokens remain separate. Confirmation
requires at least 15% lower token use against both baselines and no added
failures. The pilot cannot establish the full five-percentage-point safety
bound. Failed and stopped development runs remain outside its matched sample.

Reproduce the scored study from the repository root:

```sh
.venv/bin/python -m evaluation.model_transfer verify --output benchmarks/model-transfer-study/2026-10-13/pilot-v7
.venv/bin/python -m evaluation.model_transfer report --output benchmarks/model-transfer-study/2026-10-13/pilot-v7
```

Do not restart a scored run in its existing directory. New execution requires
a new freeze. See docs/model-transfer-evaluation.md for the full protocol.

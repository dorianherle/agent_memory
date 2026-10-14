# Measure model transfer

The study compares a searchable notebook, Agent Memory without task models,
and Agent Memory with task models. It measures all native input and output
tokens per fully completed task. Failed attempts remain in the totals.

The pilot has eight tasks and two runs per method. Each task has three stages.
Each stage starts a new agent with at most 1,200 characters of initial memory.
The agent can retrieve more evidence. A stage has 600 seconds and 40 tool calls.

The tasks require code changes and source-based research. They use constructed
work samples. Research records are test evidence, not measured fabrication
data. The same author wrote the contracts and reference functions. This is a
private development study. It is not an independent public benchmark.

## Run the study

Use the same verified model and reasoning setting for every method. Current
access checks found `gpt-5.6-sol` available with this login. The original
`gpt-6.1-sol` request failed. The user authorized an available replacement.

```sh
.venv/bin/python -m evaluation.model_transfer build --output /absolute/path/build
.venv/bin/python -m evaluation.model_transfer prepare --output /absolute/path/pilot --phase pilot --model gpt-5.6-sol
.venv/bin/python -m evaluation.model_transfer run --output /absolute/path/pilot
.venv/bin/python -m evaluation.model_transfer verify --output /absolute/path/pilot
.venv/bin/python -m evaluation.model_transfer report --output /absolute/path/pilot
```

Use a new output directory. Frozen studies cannot be edited or restarted in
place. Fixes require a new freeze. Failed runs and their logs stay available.

The confirmation study has 96 separate work samples from the same eight task
families. Its source packs do not overlap the pilot. These are new instances,
not 96 new domains. Run confirmation only when the pilot gate passes:

```sh
.venv/bin/python -m evaluation.model_transfer prepare --output /absolute/path/confirmation --phase confirmation --model gpt-5.6-sol
.venv/bin/python -m evaluation.model_transfer run --output /absolute/path/confirmation --pilot /absolute/path/pilot
```

The gate requires complete metering and isolation, at least 15% fewer tokens
per completed task against both baselines, and no additional failures. Model
and reasoning settings must match the pilot. A failed gate stops confirmation.

## Isolation and token counts

The model makes one structured decision per completion. The harness retains
the full task conversation within a stage. Each handoff starts with no prior
conversation. There is no native Codex tool use in the model process. This is
a controlled agent loop; it does not measure native IDE-agent performance.
The completion command disables native shell tools, subagents, connectors,
global memory, goals, hooks, plugins, and web search. Unexpected native tool
events stop the study. A runner fault is not a memory-method failure.

The model container has credentials and no task workspace. The tool container
has one workspace and no credentials or network access. It has no task
generator or reference code. The grader uses a separate container. It receives
test inputs after the agent finishes. Expected answers remain outside it.
The next agent cannot see private grades.

The harness enforces the tool limit before a call runs. The logs retain full
prompts, events, tool arguments, tool results, and outputs. Native token counts
come from `turn.completed.usage`. Cached input tokens are already part of input
tokens. Reasoning tokens are already part of output tokens. Do not add either
again. Missing counts make the result inconclusive. No text-size estimate is
used as a token count. See the [official event format](https://learn.chatgpt.com/docs/non-interactive-mode).

The sender's investigation and model construction count toward task cost.
Shared setup probes and grading compute are recorded separately. There is no
money-saving claim without verified billing data.

All three methods can batch up to eight sequential tools per completion. The
record retains each argument and result. Each tool still counts toward the
40-call limit. A batch stops on a tool error. Nested batches are prohibited.
The model method can use `model_handoff` to check, accept, reduce, and predict
without another agent decision. It reserves seven operation slots before
execution and charges the number of operations actually attempted. Each
operation remains in the record. A valid existing model can be reused. New
evidence can require revision or additional conditions.
Later agents can submit only new observations when existing rules still apply.
The backend retains old cases and blocks a new case that contradicts the rules.
Default handoff results contain working check summaries. Complete workflow
audits remain in JSONL, with the exact agent-visible result recorded separately.
An agent can request full results or read a complete result file. Other tool
outputs are retained without a text-length cap. Resource totals use native
token events, not model JSON size.
Memory tools in all methods can save complete results to a local JSON file.
Models can be proposed from a JSON file. This avoids copying large rule trees
into an escaped command. Invalid agent decisions with complete native usage
receive up to three consecutive repair attempts. Each attempt remains in token
cost. Native tool use, access errors, and missing usage still stop the study.

Before another pilot, run a separate full handoff check. Its two work samples
use different variants from the scored pilot. They cover code and research.
These checks have no performance conclusion:

```sh
.venv/bin/python -m evaluation.model_transfer prepare --output /absolute/path/handoff --phase handoff --model gpt-5.6-sol
.venv/bin/python -m evaluation.model_transfer handoff-check --output /absolute/path/handoff
```

## Scoring and limits

The final deliverable and all its private checks must pass. Correct
reference programs and two incomplete controls run through the same isolated
grader before workers start. The final checks preserve earlier requirements.
The worker receives no private check output.
Workers receive a public output schema with every task. The schema defines
all result fields, including accepted data records. The data contract defines
when an accepted ID enters the seen set and requires errors from all rows.
Correct reference outputs must conform to this public schema before execution.
Earlier stages receive separate diagnostic scores. A later agent can repair
an earlier defect. Final grading also tests earlier inputs with their original
source and policy context. A changed policy must not erase old observations.

The model method must transfer an accepted, tested model at every handoff.
The instructions require model retrieval, actual evidence, checking, acceptance,
reduction, and a prediction check. The handoff guard verifies the active model.
Missing model transfer makes the study inconclusive. It does not silently turn
the new method into the notes baseline.

The report also records repeated reads of identical files, unsupported
conclusions, missed source changes, elapsed time, model versions, and the first
cumulative cost crossing. A repeated read can be necessary. Do not label it
as wasted work without further evidence.

Stored model cases are agent-supplied evidence. Their replay does not establish
external correctness. The study adds separate unseen prediction inputs when
the model uses the declared raw inputs and a known target. Other models are
reported as unprobed. A model may grow when evidence requires more conditions.

At the last handoff, a separate comparison gives two new agents identical
files, raw memory, model content, and check status. One uses written rules.
One has executable model tools. Tasks are selected before results exist. If
the sender did not create a model, the comparison is unavailable; the harness
does not supply a model to make the result look better.

The primary analysis keeps repeated runs inside their task unit. It uses a
paired bootstrap with fixed domain counts. It reports an exact upper bound
on tasks where the new method failed and a baseline passed. Each baseline
uses a one-sided 97.5% bound. Counts of case checks are not independent trials.

Useful: token ratio at most 0.75 against both baselines, supported by the cost
interval, with an added-failure upper bound at most 0.05. Exceptional: ratio
at most 0.50 under the same correctness rule. Pilot results remain preliminary.
The report separates code and research results. It restricts claims to this
constructed task population. Confirmation variants share task families;
they do not establish broad performance across independent real-world projects.

## Verify the implementation

```sh
.venv/bin/python -m pytest evaluation/tests -q
```

Tests cover native metering, missing counts, typed values, complete logs, tool
limits, source changes, path escape, search, private controls, frozen inputs,
paired analysis, failure costs, confidence bounds, and the continuation gate.
These tests establish harness behavior. They do not establish memory benefits.

## Current result: 13 October 2026

The scored pilot stopped. Its conclusion is **inconclusive**. The model and
native token checks passed before the run. A source-dependency agent then
reached the 600-second limit. Its last completion had fewer than eight seconds
left. The CLI returned no usage event for that call. The plan requires a stop
when token counts are missing.

Eight of the 48 planned trajectories started. Four baseline trajectories
completed their final checks. No model-method trajectory completed all three
stages. Research tasks, the written-rule comparison, and confirmation did not
run. These partial results cannot give a matched task-level token ratio.

The diagnostic checks found a model error as well. One accepted model passed
its two saved cases. It answered 16 of 27 unseen source cases correctly and
answered 11 incorrectly. Its code deliverable passed. This shows why a saved
case check must remain separate from external correctness.

See the saved [report](../benchmarks/model-transfer-study/2026-10-13/pilot-v3/report.md),
[complete and partial records](../benchmarks/model-transfer-study/2026-10-13/pilot-v3/records.jsonl),
[prediction audit](../benchmarks/model-transfer-study/2026-10-13/pilot-v3/prediction-audit.json),
and [source verification](../benchmarks/model-transfer-study/2026-10-13/pilot-v3/frozen-source-verification.json).
Earlier development runs and their known costs remain in `setup-resources.json`.
Missing usage stays unknown. It is never estimated as zero.

The revised model instructions require independent local boundary predictions,
retained mismatches, and explicit limits. Saved-case replay alone is insufficient.
These instructions have not received a new scored study.

The revised runner will not start a completion with fewer than 60 seconds
left. It stops further tool calls and stage dispatch after a shared metering
fault. It saves each completed trajectory at once. These changes passed 32
harness tests. They do not establish a performance gain. A future scored run
requires a new freeze. The original source remains in `pilot-v3/private/source`.

The first revised pilot (`pilot-v6`) stopped after a contract fault was found.
Its data instructions omitted the fields in accepted records. The private
grader rejected an extra `unit` field that the instructions did not forbid.
These records remain development evidence. They do not establish an added
failure or a performance gain. A new freeze supplies explicit output schemas
to every method. A controller stop file now ends dispatch after the current
completion and preserves its native token events.

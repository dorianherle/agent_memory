# Private fresh-agent handoff results

Agent Memory and a searchable Markdown notebook produced equal results in this test. This test does not establish an Agent Memory advantage.

| Stage | Condition | Complete task attempts | Full case passes | Mean partial score |
| --- | --- | --- | --- | --- |
| Shared stage 0 | Sender | 3/4 | 25/26 | 98.59375% |
| First handoff | Agent Memory | 3/4 | 39/40 | 99.0625% |
| First handoff | Notebook with BM25 | 3/4 | 39/40 | 99.0625% |
| Second handoff | Agent Memory | 3/4 | 55/56 | 99.25% |
| Second handoff | Notebook with BM25 | 3/4 | 55/56 | 99.25% |

All failures are the research `equality_pass` case in trial 1. This defect was present in the shared sender code before the first comparison. The fit limit is zero. Floating-point residuals give reduced chi-square `6.07e-31`, so a strict comparison rejects the fit. The numerical values pass the grader's tolerance, but the Boolean decision does not match mathematical equality. This is a narrow numerical boundary case. Both arms retained the same defect. The notebook and memory systems did not cause this starting defect. No grader feedback was sent to the workers.

## Design

A separate fresh agent wrote and froze the two tasks, private checks, references, and incomplete controls before the workers started. It did not inspect the memory implementation or prior handoff results. This is a model-authored development set. It is not an independently authored human test set.

Two fresh sender agents each completed two stage-0 tasks. Eight fresh receiver agents completed 16 task attempts across two conditions, two trials, two tasks, and two handoff stages. The research task adds correlated measurement uncertainty, then selected observations. The software task adds expiring holds, then atomic amendment and request conflicts. Both use executable checks and preserve earlier behavior.

The first handoff starts each paired arm from the same code and byte-identical input notes. Each initial packet has at most 1200 characters. Both arms can retrieve all stored notes. The notebook has real local BM25 search and has no added distracting filler. Code and local tests carry forward. Original task rules are stored as notes. Current steering and the API are available to both arms.

The second handoff continues each arm's actual stage-1 output. Its sender code and new notes can differ across conditions. This sequential design was declared in the run protocol before receivers started. It differs from the author pack's suggested matched stage-2 design. Therefore, stage 2 is a continuation test. It does not isolate a second-handoff retrieval effect.

The source used by these workers is the frozen v3 candidate, before the exact-text fixes. Inputs to the stores are equal, but v3 can normalize note whitespace. The later source fixes and final public retrieval replay use v6. Do not relabel the worker study as a test of v6.

All agents inherit the same root settings, with no model override. Exact native model identity and token use were unavailable. A local CLI probe failed before completion because the backend did not support the configured model for that login. There is no model cost or token saving claim.

## Checks and limits

The grader has fixed numerical expected values and discrete state checks. It runs candidate code in fresh subprocesses. The correct references pass all checks. Incomplete controls fail. Each later stage checks earlier cases as well.

The grader and task files retain their original hashes. Verification checked 196 immutable receiver and protocol files. Helper output logs record note retrieval. They omit other reads and do not measure native token use. Workspace isolation uses instructions. No OS read barrier is claimed.

The small sample has two task families and two trials. Attempts share sender code and earlier-stage state. They are not independent observations. No general success rate or statistical significance claim is supported. The complete JSON reports include all schema, numerical, decision, response, and state check counts.

Code carryover and full notebook retrieval make this a strong control. Agent Memory still offers Git history, branches, declared-file stale checks, and bounded packets. Those features need a separate test of changed evidence and branch decisions before claiming a task-quality gain.

Evidence is in `benchmarks/research/evidence/v3/handoffs/` and the private handoff archive. The new source audit and published benchmark comparison are separate reports.

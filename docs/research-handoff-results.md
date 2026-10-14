# Research recovery: private development results

Agent Memory matched complete history on the corrected development study.
Both passed all 66 checks. Recent history passed 54 of 66. The result supports
method recovery on these tasks. It does not establish general research improvement.

## Main result: common API schema supplied to every agent

| Context condition | Checks passed | Complete tasks | Mean visible evidence proxy tokens |
|---|---:|---:|---:|
| Agent Memory | 66/66 | 6/6 | 408.7 |
| Recent history | 54/66 | 2/6 | 156.3 |
| Complete history | 66/66 | 6/6 | 1085.7 |

Each condition used two fresh agents. Each agent handled three separate cases.
There were six agents and 18 task attempts in this version. Tasks within one
agent are a cluster. The same inherited model ran without an override.

Agent Memory used 13 explicit evidence reads. Its mean visible evidence was
62.4% below complete history. This is a cl100k_base text proxy. It includes
initial evidence and explicit retrieval. It excludes common prompts, other
tool output, repeated requests, hidden reasoning, caches, and billing.
It is not a total-token or model-cost claim. Recent history used the same
initial character budget as memory, with different token counts.

## What the checks found

- **Convergence:** every condition recovered the current geometry in both
  trials. All passed the external convergence checks.
- **Complex fields:** recent-context implementations used a different
  normalization. Scale invariance, missing components, and arbitrary vectors
  exposed the difference. Some invalid-input checks also failed.
- **Timing:** recent-context reports did not recover the saved baseline
  construction contract. One invalid-timing check also failed in trial 1.
  All conditions retained the current-data accuracy failure.

The current-data field results fail the qualification limit in both trials.
The timing results have only three accuracy passes out of four attempts.
A correct agent must retain these negative results. A passed task means correct
assessment; it does not mean that the tested field or solver succeeded.

## Why version 1 is secondary evidence

Version 1 kept the report schema in historical notes. Agent Memory and complete
history passed 66/66 checks; recent history passed 21/66. Of the 45 recent-history
failed checks, 32 stopped on missing dictionary keys. These failures included
output names and an unnecessary matrix_id requirement. They do not all indicate
wrong numerical results.

Version 2 supplies input and output schemas to every agent and states that
matrix_id is optional. The numerical scorer and acceptance checks stay unchanged.
Fresh agents ran in new workspaces. Version 2 is a development correction after
inspection of version 1. It is not an independent replication. The original
candidates and results remain available. No trial received grader feedback.

## Verification and evidence

Frozen source hashes and immutable input hashes passed in both versions.
Common task and numerical inputs matched across conditions. Correct reference
controls passed 11/11 checks in each case. Unfinished controls passed 0/11.
These checks validate scorer behavior; they do not establish independent task authorship.

- [Corrected results](../benchmarks/research/evidence/v2-results.json)
- [Version 2 complete evidence](../benchmarks/research/evidence/v2-complete-study.zip)
- [Original results](../benchmarks/research/evidence/v1-results.json)
- [Version 1 complete evidence](../benchmarks/research/evidence/v1-complete-study.zip)
- [Archive checksums](../benchmarks/research/evidence/archive-index.json)
- [Package and demo verification](../benchmarks/research/evidence/private-verification.json)
- [Procedure and reproduction commands](research-handoff-study.md)

The frozen scorer uses a version-1 metadata label. The corrected result records
the manifest version and preserves the exact raw scorer output inside its
archive. Check results and scores are unchanged by this metadata operation.

## Practical limits

The histories and tasks were authored for development. There are only two
repeats per condition. Workspace isolation relies on instructions, not an
operating-system barrier. Recent-history truncation has no retrieval and does
not represent all context-management methods. Complete history is the useful
control for information preservation.

The next evidence must include independent tasks, actual sequential agent
handoffs, a maintained notebook comparator, and native model usage. This work
has not been published or pushed to GitHub.

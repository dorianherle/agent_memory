# Private Agent Memory validation, 13 October 2026

Agent Memory has not shown a task accuracy advantage over a searchable notebook in these tests. The work did find and correct seven source defects. All changes, packages, and results remain private.

| Completed test | Agent Memory | Searchable notebook | Meaning |
| --- | --- | --- | --- |
| LongMemEval-S: all required evidence in the first 10 results | 424/470 (90.21%) | 424/470 (90.21%) | Session retrieval only |
| First fresh-agent handoff | 39/40 cases; 3/4 complete tasks | 39/40 cases; 3/4 complete tasks | Two trials across research and software tasks |
| Second fresh-agent handoff | 55/56 cases; 3/4 complete tasks | 55/56 cases; 3/4 complete tasks | Each condition continues its own first-stage work |

The case counts are not counts of independent agents. The two handoff stages share earlier work and have cumulative checks. These small development studies do not establish a general result.

## Published benchmark and input checks

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) provides 500 questions. Its retrieval procedure excludes 30 abstention questions. This comparison uses all 470 eligible questions from the pinned cleaned S dataset. Both stores receive the same session dates, IDs, roles, and text. Answers and answer-location labels do not enter either store. Both use local SQLite BM25. There are no model calls or embeddings in this retrieval test.

Both conditions returned the same ranked session IDs for every question. All required evidence was found for 390/470 questions at five results and 424/470 at ten results. The latter score does not mean that 90.21% of answers are correct. No reader or answer judge was tested.

The final verifier passed for all 22,419 stored sessions. It compares every decoded note and its allowed metadata with the source input. It retains repeated session IDs as separate note occurrences. Twelve questions have repeated session IDs. A separate metric check passed 400 examples against the pinned author code, including repeated corpus IDs. The dataset, code, source snapshot, and output hashes are recorded.

The final run is a query replay on copied stores, with input checks before each query. One older history required restoration from the source corpus because Git had normalized a combining character. Earlier repairs addressed line endings and Unicode noncharacters. Restoration uses only input fields. The final replay does not measure ingestion time. The supplied fresh-ingestion script provides a reproduction route with the final source.

Observed median query time was 89.50 ms for Agent Memory and 1.92 ms for the notebook. Agent Memory was slower in this run. These are single queries per case on one Mac during concurrent copy and cache work. They do not provide a production latency distribution. The profile in the next-test record identifies repeated Git reads and duplicate SQL work as further optimization targets.

Read the [benchmark selection and reproduction procedure](external-memory-benchmarks.md). Inspect the [final raw result](../benchmarks/research/evidence/v3/longmemeval-final/results.json), [summary](../benchmarks/research/evidence/v3/longmemeval-final/run-summary.json), [protocol](../benchmarks/research/evidence/v3/longmemeval-final/protocol-freeze.json), and [verification](../benchmarks/research/evidence/v3/longmemeval-final/verification.json).

## Fresh-agent task checks

A separate fresh agent prepared the research and software tasks without access to the implementation or earlier study. The task author was a model, not a human. Frozen private graders passed reference implementations and rejected unfinished controls. Two sender agents prepared starting work. Eight fresh receiver agents then completed two tasks each across the two conditions and two stages.

The first handoff starts with equal code and input notes. Each condition can search all notes with BM25. The second handoff continues the code and notes from its own condition. It therefore tests sequential use; it does not isolate a second-stage memory effect. The workers used frozen v3 source. The later source fixes use v6. No grader feedback went to workers.

Every failed case came from the same research boundary error inherited from a common sender. At a zero fit limit, floating-point residual error of about 6.07e-31 caused a strict decision to fail. Numerical values passed the tolerance check. This failure remains in the scores.

Exact native model IDs, token counts, and billing data were unavailable. Isolation used instructions, not an operating-system read barrier. Full native worker transcripts were unavailable. The evidence includes source, notes, helper traces, frozen graders, and grades. Read the [handoff result and limits](fresh-agent-handoff-results-2026-10-13.md) and [private evidence archive](../benchmarks/research/evidence/v3/handoff-evidence.zip).

## Source changes and verification

Seven reproduced defects are corrected: filtered search limits, invalid embedding values, missing untracked dependencies, whitespace changes, control-character framing, line-ending conversion, and Unicode changes. Fifteen regression methods pass. The full final suite reports 274 passed, one skipped, one warning, and three passed subtests. The skipped test requires a Gemini API key. The warning concerns a Pydantic forward reference.

A separate deterministic 100-case text check passes after reopening the store. This sample includes control characters, whitespace, reserved-looking strings, and Unicode. It is not exhaustive. An installed private wheel also passed checks of text storage, search, stale-file detection, reopening, and Studio assets. The wheel check reused existing dependencies; it was not a clean dependency installation.

The final core files and regression tests are integrated into the original private checkout. Existing runner and Studio edits were preserved. Backups and source hashes are retained. New notes use ASCII JSON inside Git and return their original text through the API. They require the updated reader. Text already changed by an older save needs its original input for recovery.

Read the [source review](source-reliability-audit-2026-10-13.md). The [evidence checksum index](../benchmarks/research/evidence/v3/sha256.json) covers compact private records. Large raw stores and the pinned dataset remain local; their paths are in the evidence record. Failed and intermediate runs are retained. The first full attempt stopped after 69 completed cases. Its failure is not presented as a completed result.

## Next test

[MemoryArena](https://memoryarena.github.io/) tests dependent agent actions across sessions. Its formal-reasoning tasks are a suitable next test for the research use case. This is a recommendation based on task design. No MemoryArena score exists for Agent Memory. [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) also provides tests of retrieval, learning, long-range understanding, and conflicting facts.

Use the official tasks and scoring. Compare full history, the searchable notebook, and Agent Memory with equal reader and tool settings. Freeze the sample and scoring before execution. Record task-level results, uncertainty, failures, and native resource use. Keep a separate development set. See the [next validation procedure](next-memory-evaluation.md).

The present evidence supports the tested reliability fixes and reproducible retrieval. It supports no general accuracy advantage, no native token or cost savings, and no public benchmark answer score.

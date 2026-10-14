# Published memory benchmarks

These sources were checked on 13 October 2026. All changes and results remain private.

| Benchmark | What it tests | Use for Agent Memory |
| --- | --- | --- |
| [MemoryArena](https://memoryarena.github.io/) | Dependent tasks across sessions; web use, planning, search, and formal reasoning | Best next test of memory-guided action. This is a selection based on its task design. No MemoryArena score is claimed. |
| [LongMemEval](https://github.com/xiaowu0162/LongMemEval) | Chat history; fact extraction, updates, time, reasoning, and abstention | A reproducible session-retrieval check is implemented. Answer quality needs a reader and a separate judge. |
| [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | Memory from long agent work histories | A useful later test of workflow and environment knowledge. Its full evaluation needs model endpoints. |
| [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | Retrieval, learning during testing, long-range understanding, and conflicting facts | A useful later stress test for memory updates and conflicts. No score is claimed. |

The LongMemEval-S cleaned dataset is pinned to revision `98d7416c24c778c2fee6e6f3006e7a073259d48f`. The source metric code is pinned to commit `9e0b455f4ef0e2ab8f2e582289761153549043fc`. Dataset and source hashes are in the provenance record.

The 12-question pilot uses two questions from each of six types. A fixed hash order selects them before the run. Both stores receive the same session text and dates. The answer text and answer-location labels are excluded. The test uses local SQLite BM25 in both stores, with no model calls or embeddings. It measures retrieval only.

All-evidence recall at 10 was 10/12 for both stores. Their retrieved session order was equal. Agent Memory used more time. The median observed query time was 63.75 ms for Agent Memory and 1.51 ms for the notebook. The test used one query per case on one host during concurrent store construction. These timings are observations, not a production latency estimate.

The full retrieval run includes all 470 eligible questions. The 30 abstention questions are excluded, as specified by the authors for retrieval assessment. The final comparison found all required evidence in the first ten results for 424/470 questions (90.21%) in each condition. All ranked results were equal. The final verifier checked exact decoded text and metadata for 22,419 sessions. Read the [completed result and limits](memory-benchmark-results-2026-10-13.md).

Do not compare these retrieval scores with an end-to-end answer leaderboard. Retrieval does not prove correct reasoning, correct answers, or correct action. A later MemoryArena run must use the official environments, equal reader settings, and answer scoring. Preserve tool failures and unsuccessful runs.

## Local reproduction

Install this private checkout and its test dependencies. Use Python 3.10 or later as specified by the package. The completed run used Python 3.14.3.

```sh
python scripts/fetch_longmemeval.py --output /tmp/longmemeval-data
python scripts/benchmark_public_retrieval.py /tmp/longmemeval-data/longmemeval_s_cleaned.json --output /tmp/agent-memory-longmemeval-run --per-type 1000 --workers 4
python scripts/verify_public_retrieval.py /tmp/longmemeval-data/longmemeval_s_cleaned.json /tmp/agent-memory-longmemeval-run /tmp/longmemeval-data/eval_utils.py
```

The run output directory must not exist. The downloader checks pinned hashes. The verifier compares metric results with the pinned author code and checks every stored session. The corpus and raw stores use about two gigabytes for the full comparison. No model or embedding endpoint is used.

The old pilot source normalized whitespace in 82 of 585 sessions. Its words and retrieval order were preserved, but its raw stored strings were not exact. The corrected source preserves the raw strings. The final corrected-reader replay verification is the authority for this check. It checks every note occurrence, because 12 questions repeat a session ID. Original failed and intermediate runs are preserved. The final replay verifies input text before each query and restores any changed older notes from the original corpus. No answers or answer-location labels enter the note stores.

The JSON timing field names include `warm` and `durable`. They describe a query after ingestion and a completed save operation. No repeated-query warmup, crash recovery test, or power-loss test was performed. Use these observations as query and save-call times.

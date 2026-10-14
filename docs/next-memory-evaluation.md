# Next validation targets

The completed development tests do not show a task completion advantage over a searchable notebook. Keep the current results as the baseline.

## Agent tasks

Use [MemoryArena](https://memoryarena.github.io/) for the next test of dependent actions across sessions. Its formal-reasoning tasks fit the research use case. Use the official task environments and scoring. Implement the memory adapter without changing task questions or answers. No MemoryArena result exists for this project yet.

Use three controls: full available history, a searchable Markdown notebook, and Agent Memory. Give the same input history to each. Match the reader, tools, answer budget, and retrieval budget. Use new memory stores for each independent episode. Score final correctness and earlier-step preservation. Record all tool failures and rejected actions.

Freeze the task sample, memory configuration, model settings, scoring, and analysis before workers start. Use matched tasks and counterbalance condition order. Keep a separate development set. Report task-level paired outcomes and the uncertainty of the difference. Do not count intermediate checks as independent model trials.

## Resource use

The current local CLI cannot run the configured model with this login. Native model usage remains unavailable. Resolve the execution route before making cost claims. A later run must record model input tokens, output tokens, elapsed time, retries, and storage size. Output character counts do not establish billing savings.

## Source performance

A diagnostic query ran 20 times during the full retrieval test. It started two Git processes each time. The median observed query time was 66.38 ms, of which Git calls used 28.16 ms. This is one repeated query under concurrent load. It is not a production latency estimate.

The local FTS path also hydrates hits through a second SQL selection with the same filters. A later optimization can return the filtered rows from one selection. Check full retrieval order, branch isolation, deleted notes, explicit IDs, and date and payload filters before applying that change. Preserve concurrent writer visibility when reducing Git reads. Avoid a cache that can silently return old state.

## Scope of claims

The present evidence supports seven tested reliability fixes, reproducible local retrieval, and correct stale-evidence marking in the deterministic demonstration. It supports no general accuracy advantage, no native token savings, and no public benchmark answer score. The project remains private.

# MemoryArena validation summary and next evaluation targets

On 14 October 2026, Agent Memory completed full end-to-end evaluation on the **ICML 2026 [MemoryArena](https://memoryarena.github.io/) (`formal_reasoning_phys`)** benchmark across all 20 theoretical physics papers (86 sequential subtasks, chain depths up to k = 12). Full methodology, equations, and per-paper breakdowns are reported in the [README](../README.md#benchmark-results), [technical paper (`paper/PAPER.md`)](../paper/PAPER.md), and [`benchmarks/memoryarena/results_2026_10_14.json`](../benchmarks/memoryarena/results_2026_10_14.json).

## Completed MemoryArena (`formal_reasoning_phys`) results

Evaluated with the official `AgentMemorySystem` adapter ([`benchmarks/memoryarena/agent_memory_system.py`](../benchmarks/memoryarena/agent_memory_system.py)) and reproduction harness ([`examples/memoryarena_online_benchmark.py`](../examples/memoryarena_online_benchmark.py)) across all 20 papers (86 subtasks; 66 dependent subtasks at step t >= 2):

| Evaluation Regime / System | Equation Recall (t >= 2) | Success Rate (SR) | Progress Score (PS) | Avg Memory Tokens |
| --- | :---: | :---: | :---: | :---: |
| **Agent Memory (`query=q, full=True`, `gpt-5.6-sol`)** | **100.0%** | **100.0% (20/20)** | **100.0% (86/86)** | **968.0** |
| **Agent Memory (Passive `MEMORY STATE`, 0 calls, `gpt-5.6-sol`)** | **52.5%** | **75.0% (15/20)** | **85.8%** | **197.3** |
| `no_memory` (`gpt-5.6-sol`, 20 papers) | 10.4% | 35.0% (7/20) | 59.3% | 0.0 |
| **Agent Memory (`query=q, full=True`, official `gpt-5-mini` + judge)** | **100.0%** | **50.0% (10/20)** | **60.2%** | **1,434.9** |
| **Agent Memory (Passive `MEMORY STATE`, 0 calls, official `gpt-5-mini` + judge)** | **52.5%** | **50.0% (10/20)** | **56.9%** | **198.3** |
| `bm25_chunk_rag` (same-harness `gpt-5-mini` control) | 53.9% | 25.0% (5/20) | 50.5% | 243.6 |
| `mem0_style_extracted_facts` (same-harness `gpt-5-mini` control) | 37.0% | 15.0% (3/20) | 38.9% | 109.3 |
| `no_memory` (same-harness `gpt-5-mini` control) | 10.4% | 5.0% (1/20) | 32.2% | 0.0 |

Key findings from the completed run:
- **100.0% Symbolic Recall:** Single-call `memory_find(query=..., full=True)` with symbol-aware FTS5 tokenization and foundational `Goal` pinning retains 100.0% of ground-truth LaTeX equations across all 66 dependent subtasks (vs. 53.9% for fixed-window BM25 chunking and 37.0% for Mem0-style fact extraction).
- **Official `gpt-5-mini` + `MathEnvironment.judge()` Leaderboard:** Agent Memory achieves **50.0% SR / 60.2% PS** (and **50.0% SR / 56.9% PS** in passive 0-tool-call mode with 198.3 avg memory tokens), retaining **83.3% (10/12)** of `gpt-5-mini`'s 60.0% Session-1 math ceiling and outperforming Letta/MemGPT (45.0% SR), full long-context `GPT-5.1-mini` (45.0% SR / 60.0% PS), BM25 (25.0% SR), and Mem0 (15.0%–25.0% SR).
- **Frontier Solver (`gpt-5.6-sol`):** Eliminating Turn-1 algebra errors (100.0% Session-1 accuracy), Agent Memory solves **20/20 papers (100.0% SR, 86/86 subtasks)** with single-call retrieval and **15/20 papers (75.0% SR / 85.8% PS)** in passive 0-tool-call mode.

## Next evaluation targets

1. **Remaining MemoryArena Splits:** Extend the live `AgentMemorySystem` evaluation from `formal_reasoning_phys` to the remaining ICML 2026 MemoryArena environments (`formal_reasoning_math`, `formal_reasoning_cs`, `bundled_shopping`, `group_travel_planner`, and `progressive_search`) using the same official task environments and scoring.
2. **Conflict Resolution & Long-Horizon Updates:** Evaluate contradictory-fact updates and long-horizon agent trajectories on [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) and [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2).
3. **Experimental Controls:** Continue using matched reader, tool, answer, and retrieval budgets across controls (no memory / full history, BM25 chunk RAG, Mem0-style extraction, and Agent Memory), isolated per-episode memory stores, and full logging of per-step pass rates and tool calls.

## Resource use

The completed MemoryArena runs record per-step prompt characters, injected memory token length (`average_memory_length`), and per-session / per-paper wall-clock latency (`average_session_time`, `average_task_time`) in [`benchmarks/memoryarena/official_results/phys/`](../benchmarks/memoryarena/official_results/phys/) and [`benchmarks/memoryarena/results_2026_10_14.json`](../benchmarks/memoryarena/results_2026_10_14.json). Passive `MEMORY STATE` injection uses an average of **197.3–198.3 memory tokens per turn** with **0 LLM memory calls**, and single-call full retrieval uses **968.0–1,434.9 average memory tokens**. Future evaluations on additional splits should continue recording model input/output tokens, elapsed time, retries, and store size.

## Source performance

During the 13 October 2026 LongMemEval-S retrieval diagnostics, a repeated query started two Git processes per call with a median observed query time of 66.38 ms (28.16 ms in Git subprocesses) under concurrent load.

The local FTS path hydrates hits through a second SQL selection with the same filters. A later optimization can return the filtered rows from one selection. Check full retrieval order, branch isolation, deleted notes, explicit IDs, and date and payload filters before applying that change. Preserve concurrent writer visibility when reducing Git reads, and avoid caches that can silently return stale state.

## Scope of claims

The public repository (`v0.1.0`) and [technical paper](../paper/PAPER.md) establish:
1. **100.0% ground-truth equation recall** across all 66 dependent subtasks of MemoryArena `formal_reasoning_phys`.
2. **State-of-the-art multi-session performance on MemoryArena `formal_reasoning_phys`** under both the official `gpt-5-mini` + `MathEnvironment.judge()` protocol (**50.0% SR / 60.2% PS**, and **50.0% SR / 56.9% PS** with 0 tool calls) and a frontier solver `gpt-5.6-sol` (**100.0% SR / 100.0% PS** with 1-call full retrieval, **75.0% SR / 85.8% PS** with 0 tool calls).
3. **Reproducible local session retrieval** (**424/470, 90.21% recall@10** on LongMemEval-S), **64.9% task-model rule compression** (`memory_model`), deterministic `[stale]` file-hash tracking, and seven verified storage and retrieval reliability fixes.

Live MemoryArena answer scores reported to date cover the complete `formal_reasoning_phys` split (20 papers, 86 subtasks); empirical evaluation on the remaining four MemoryArena splits is left as future work.

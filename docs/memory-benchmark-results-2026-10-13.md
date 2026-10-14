# Agent Memory Benchmark & Validation Results

This document summarizes the empirical evaluation of **Agent Memory** across the **ICML 2026 [MemoryArena](https://memoryarena.github.io/) (`formal_reasoning_phys`)** benchmark, **LongMemEval-S** session retrieval, and executable task-model verification. Full architectural details and derivations are in the [README](../README.md#benchmark-results) and [technical paper (`paper/PAPER.md`)](../paper/PAPER.md).

## Summary of Benchmark Results

| Benchmark / Evaluation | Agent Memory | Baselines / Controls | Key Finding |
| --- | --- | --- | --- |
| **MemoryArena `formal_reasoning_phys` Equation Recall** (66 dependent subtasks, t >= 2) | **100.0%** (1-call `full=True`, ~1,565 tokens) · **52.5%** (0-call passive, ~338 tokens) | 53.9% (`bm25_chunk_rag`) · 37.0% (`mem0_style`) · 10.4% (`no_memory`) | 100.0% ground-truth LaTeX equation retention across sessions |
| **MemoryArena `formal_reasoning_phys` (`gpt-5-mini` + official `MathEnvironment.judge()`)** | **50.0% SR / 60.2% PS** (1-call) · **50.0% SR / 56.9% PS** (0-call passive) | 45.0% SR (Letta/MemGPT) · 45.0% SR (GPT-5.1-mini Long Context) · 25.0% SR (`bm25`) · 15.0%–25.0% SR (`mem0`) · 5.0% SR (`no_memory`) | #1 external memory system; retains **83.3% (10/12)** of `gpt-5-mini`'s 60.0% single-turn math ceiling |
| **MemoryArena `formal_reasoning_phys` (`gpt-5.6-sol` frontier solver)** | **100.0% SR / 100.0% PS** (86/86 subtasks, 1-call) · **75.0% SR / 85.8% PS** (0-call passive) | 35.0% SR / 59.3% PS (`no_memory`) · 37.5% SR (`bm25` / `mem0` controls) | Solves all 20/20 multi-session physics papers through depth k = 12 |
| **LongMemEval-S Session Retrieval** (470 eligible questions, SQLite FTS5) | **424/470 (90.21%)** recall@10 · **390/470 (82.98%)** recall@5 | 424/470 (90.21%, local BM25 baseline) | Verified across 22,419 stored sessions with exact text integrity |
| **Task Model Algebraic Compression** (`memory_model`) | **64.9%** working-model byte reduction (`1,061 -> 372` B); **50.0%** packet reduction | Raw per-case prose notes | 100% pass rate across 32 revision checks and 263 repair/boundary cases |

---

## 1. ICML 2026 MemoryArena (`formal_reasoning_phys`)

Evaluated across all **20 theoretical physics papers (86 interdependent subtasks, chain depths k = 2 to 12)** from [`ZexueHe/memoryarena`](https://huggingface.co/datasets/ZexueHe/memoryarena) using the official `AgentMemorySystem` adapter ([`benchmarks/memoryarena/agent_memory_system.py`](../benchmarks/memoryarena/agent_memory_system.py)):

- **Official `gpt-5-mini` + `MathEnvironment.judge()` reproduction:** Across all 20 papers, `gpt-5-mini` solves 12/20 papers (60.0%) on Session 1 (where the problem setup is given directly in `BACKGROUND`). Without memory, `gpt-5-mini` collapses from 60.0% on Session 1 to **5.0% SR (1/20 papers) and 32.2% PS**. With Agent Memory, `gpt-5-mini` achieves **50.0% SR (10/20 papers) and 60.2% PS** with single-call full retrieval (`1,434.9` avg memory tokens) and **50.0% SR / 56.9% PS** in passive zero-tool-call mode (`198.3` avg memory tokens).
- **Frontier solver (`gpt-5.6-sol`):** With a 100.0% Session-1 math ceiling (20/20 papers), Agent Memory achieves **75.0% SR / 85.8% PS** in passive zero-tool-call mode (`197.3` avg memory tokens) and **100.0% SR / 100.0% PS (86/86 subtasks solved)** with single-call `memory_find(query=..., full=True)` (`968.0` avg memory tokens).
- **Artifacts & reproduction:** See [`examples/memoryarena_online_benchmark.py`](../examples/memoryarena_online_benchmark.py), [`benchmarks/memoryarena/results_2026_10_14.json`](../benchmarks/memoryarena/results_2026_10_14.json), and [`benchmarks/memoryarena/official_results/phys/`](../benchmarks/memoryarena/official_results/phys/).

---

## 2. LongMemEval-S Retrieval & Storage Integrity Verification

- **Dataset & Protocol:** Evaluated on all 470 eligible retrieval questions of [LongMemEval-S](https://github.com/xiaowu0162/LongMemEval) (pinned cleaned dataset `98d7416c`, excluding 30 abstention questions per author protocol) using local SQLite FTS5/BM25 with zero LLM or embedding calls.
- **Retrieval Accuracy:** All required evidence sessions were retrieved in the top 5 results for **390/470 (82.98%)** questions and in the top 10 results for **424/470 (90.21%)** questions.
- **Exact Text & Storage Integrity:** The verifier checked all **22,419 stored sessions** for byte-for-byte text and metadata fidelity, and validated seven storage/retrieval hardening fixes (pre-LIMIT SQL filtering, finite float32 embedding normalization, untracked file staleness checks, verbatim Git commit messages, ASCII JSON encoding for control characters and Unicode noncharacters, and newline-preserving UTF-8 decoding).
- **Artifacts:** See [`benchmarks/research/evidence/v3/longmemeval-final/results.json`](../benchmarks/research/evidence/v3/longmemeval-final/results.json), [`run-summary.json`](../benchmarks/research/evidence/v3/longmemeval-final/run-summary.json), and [`verification.json`](../benchmarks/research/evidence/v3/longmemeval-final/verification.json).

---

## 3. Next Evaluation Targets

See [next-memory-evaluation.md](next-memory-evaluation.md) for follow-up evaluation targets across the remaining MemoryArena task splits (`formal_reasoning_math`, `formal_reasoning_cs`, `bundled_shopping`, `group_travel_planner`, `progressive_search`) and [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench).

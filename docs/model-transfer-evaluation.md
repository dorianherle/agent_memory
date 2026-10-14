# Task Model Evaluation Protocol (`model_transfer`)

This document specifies the evaluation harness (`evaluation/model_transfer/`) used to test versioned executable task models (`agent_memory/models.py`) across multi-stage agent handoffs, as referenced in Section 3.5 and Section 4 of the [technical paper (`paper/PAPER.md`)](../paper/PAPER.md).

## Overview

The harness compares three memory conditions across multi-stage engineering and source-dependency tasks:
1. Searchable Markdown notebook (`notebook`)
2. Agent Memory with notes and handoffs (`notes`)
3. Agent Memory with executable task models (`models`, using `memory_model` / `model_handoff`)

Each task runs across three sequential stages. Every stage starts in a fresh agent context with at most 1,200 characters of initial `MEMORY STATE` and on-demand retrieval tools.

## Verification of Saved Cases vs. Unseen Boundary Inputs

As noted in Section 4 of [`paper/PAPER.md`](../paper/PAPER.md):
- **Saved-Case Replay (`memory_model(operation="check")`)**: Every model proposal must pass all historical cases carried forward from its parent model version, plus at least one passing evidence case per rule and a boundary `challenge` case per target.
- **Unseen Input Probing**: Because stored model cases are agent-authored evidence, saved-case replay verifies internal consistency with recorded observations rather than external completeness on unseen inputs. The evaluation harness (`evaluation/model_transfer/`) therefore probes accepted models on held-out boundary inputs outside the agent's workspace and grades final code deliverables in an isolated container.
- **Regression Suite**: Across the 138-test unit suite (`agent_memory/tests/`) and the 263-case model repair benchmark (`benchmarks/model-transfer-study/2026-10-13/model-repair/check.py`), the safe AST interpreter and Boolean algebraic reducer pass **100% of regression and boundary cases** (11 observed failure modes + 252 constructed edge cases).

## Reproduction Commands

```sh
# Run the multi-revision task model compression example (32 checks)
.venv/bin/python examples/task_model_revision.py

# Run the 263-case model repair and boundary verification check
.venv/bin/python benchmarks/model-transfer-study/2026-10-13/model-repair/check.py

# Run the evaluation harness test suite
.venv/bin/python -m pytest evaluation/tests -q
```

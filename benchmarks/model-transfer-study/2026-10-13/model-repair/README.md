# Correct the source review rule

The old model compared the complete saved and current source maps. It therefore
requested review after unrelated changes. The corrected model compares only
declared paths and excludes ignored paths. It retains missing-state behavior.

The interpreter now has `get(object, key, default)`. It reads JSON data. It does
not execute code. The default is evaluated only when the key is absent. Explicit
false, zero, empty strings, and null remain values.

Run from the repository root:

```sh
.venv/bin/python benchmarks/model-transfer-study/2026-10-13/model-repair/check.py
.venv/bin/python -m pytest agent_memory/tests/test_models.py evaluation/tests -q
```

Results: 263 of 263 rule checks pass. The combined test run has 64 passing tests.
The tests also cover Git transfer, fresh memory access, invalid lookup types,
lazy defaults, and unknown results outside the collection limit.

This is a repair check. It has no LLM workers and no measured token-saving
result. The earlier scored pilot remains inconclusive. A new study must use a
new source freeze and a rebuilt worker image.

Next steps:

1. Reduce agent round trips for mechanical model checks. Retain each operation
   and its result in the record. Preserve all study tool limits.
2. Run a complete three-stage handoff check with fresh agents and verified usage.
3. Run the full eight-task pilot with matched baselines and final checks.
4. Run confirmation only if the pilot saves at least 15% of tokens per completed
   task against both baselines and has no additional failures.

A shorter rule is acceptable only when it retains the required conditions.
All model construction and repair costs must remain in the measured task cost.

# Versioned task models

Agent Memory retains raw observations in Git. A task model contains executable
rules that explain those observations within stated conditions. A new agent
receives the working model first. It can request the stored cases and notes.

The task objective, current user constraints, handoff action, and open questions
stay separate from the model. The model does not decide whether work is complete.

## Revision cycle

1. Use `memory_model(operation="get")` to read the active rules and conditions.
2. Do the work. Save actual observations or test results with `memory_save`.
3. Propose a better model with `operation="propose", data=model, base=active_id`.
4. Use `operation="check", id=proposal_id` to replay the recorded cases.
5. Use `operation="accept", id=proposal_id` when the checks pass.

The first model needs no base. Later revisions require the current model ID.
Each proposal and accepted version is a normal Git-backed memory entry. The
parent ID links model versions. Competing proposals cannot overwrite a newer
accepted model. Inspection reads the model at the inspected Git snapshot.
Branches and isolated goals retain their own model lineages.
The ordinary state query excludes model archive entries. It loads the active
model through a separate query. Earlier versions remain in history and Git.

The default MCP and generic tool results omit passing case bodies. `full=True`
returns complete stored cases and check details. The Python API returns the
complete record by default; `memory.model_get(full=False)` returns working rules.

## Model format

This example describes a software review decision. Replace `EVIDENCE_NOTE_ID`
with a real observation note ID on the current branch.

```json
{
  "schema": 1,
  "title": "Source claim review",
  "inputs": {"before": "object", "after": "object"},
  "rules": [
    {
      "id": "source-change",
      "target": "needs_review",
      "claim": "Review a claim when its declared source state differs.",
      "when": true,
      "then": "before != after",
      "evidence": ["EVIDENCE_NOTE_ID"],
      "dependencies": ["review-policy.txt"]
    }
  ],
  "cases": [
    {
      "id": "unchanged",
      "target": "needs_review",
      "inputs": {"before": {"state": "missing"}, "after": {"state": "missing"}},
      "expected": false,
      "evidence": ["EVIDENCE_NOTE_ID"],
      "challenge": false
    },
    {
      "id": "created",
      "target": "needs_review",
      "inputs": {"before": {"state": "missing"}, "after": {"state": "file", "sha256": "example"}},
      "expected": true,
      "evidence": ["EVIDENCE_NOTE_ID"],
      "challenge": true
    }
  ],
  "alternatives": [],
  "unresolved": [],
  "resolutions": []
}
```

The `inputs` map declares types: string, number, integer, boolean, array, or
object. Extra observed fields can remain in archived cases when a revision
removes an unnecessary input. Every variable used by a rule must be declared.
Nested variables use names such as `request.tenant`.

Expressions use a fixed, safe interpreter. There is no `eval`, generated Python,
or shell execution. Rules can be written either as sleek 1-line formulas (parsed
by `ast.parse(..., mode="eval")` against a strict operator whitelist) or as
explicit JSON AST objects:

- Comparisons and logic: `"before != after"`, `"18.0 <= temp <= 28.0"`,
  `"artifact in ['app.py', 'config.json']"`, `"path not in policy.get('ignored', [])"`
- Conditionals: `"2.75 if serial == 'M17' and temp > 26.0 else 4.0"`
- Dictionary lookups: `"saved.get(path, {'kind': 'missing'})"`
- Collection comprehensions: `"all(dep.saved == dep.current for dep in dependencies)"`,
  `"any(path not in policy.get('ignored', []) and saved.get(path) != current.get(path) for path in declared)"`

When `claim` is omitted on a rule, it is generated automatically from the
rule's formula (`target = then when cond`).

Supported AST operators are `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`,
`not`, `and`, `or`, `len`, `add`, `sub`, `if`, and `get`. `get` takes an object,
a string key, and a default expression. It evaluates the default only if the
key is absent. It preserves explicit false, zero, empty strings, and null.
This permits lookup by a path supplied by a collection predicate. For example:

```json
{"op":"get","args":[{"var":"saved"},{"var":"path"},{"value":{"kind":"missing"}}]}
```

Collection predicates use `"all(dep.saved == dep.current for dep in dependencies)"` or:

```json
{
  "op": "all",
  "items": {"var": "dependencies"},
  "as": "dep",
  "test": {"op": "eq", "args": [{"var": "dep.saved"}, {"var": "dep.current"}]}
}
```

`any` has the same format. Numeric operators require finite numbers. Boolean
operators require Boolean values. Equality and list membership (`in`) compare
canonical JSON values (so `True` and `1` never collide). Missing fields,
missing conditions, unsupported input types, and evaluation limits return an
unknown result. Conditions define where a rule applies. When multiple rules
match a target, a rule with a strictly more specific condition (a strict
superset of conjuncts, or a conditional exception over an unconditional default
law) overrides the broader law; unrelated conflicting rules return `ambiguous`.

## Acceptance and uncertainty

All recorded cases must pass. Every rule needs a passing evidence case. Each
prediction target needs a challenge case. A challenge should test a boundary
or distinguish competing explanations. The agent chooses this test; the
interpreter does not prove that the test is sufficient.

Earlier cases cannot be rewritten or removed. If a revision omits them, the
store carries them forward. A case is a desired or observed result with an
evidence reference. The interpreter checks the proposed rule against that
result. It does not independently repeat an external experiment.

Dependencies name individual files inside the workspace. Their content hashes
and missing-file states bind the model to its source versions. File creation,
deletion, and content changes block use of the affected model. Explicit file
dependencies on evidence notes are included. Undeclared dependencies cannot
be detected. Historical observations remain evidence when the observed state
changes; they do not become current-state claims.

After a source change, a new proposal needs a new challenge case with an
evidence note created after the active model. Rechecking sources is agent work.
The reducer cannot refresh a blocked model by capturing new file hashes.

Alternatives have an ID, claim, and rule list. Revisions retain them. A recorded
case can reject an alternative, but its original definition stays available.
If an unseparated alternative predicts a different result, `predict` reports
ambiguity. It does not select a preferred explanation silently.

Unresolved results stay in the lineage. A resolution names `result`, `reason`,
and `evidence`. It closes that result in the active view while retaining the
original text and resolution evidence. The agent remains responsible for the
truth of the observation and resolution.

## Reduction

`memory_model(operation="reduce")` creates a proposal. It flattens repeated
Boolean expressions, merges rules with the same target and effect, factors
common conjuncts across disjunctions (`(A and B) or (A and C) -> A and (B or C)`),
and collapses repeated equality checks into set membership
(`(x == a) or (x == b) -> x in [a, b]`). Dependencies and evidence are retained.

This transformation factors an existing program. It does not remove its domain
conditions or discover a new physical law. Broader generalizations need an
agent proposal and new evidence. A model can grow when evidence requires an
additional distinction.

The store reports rule count, working-model JSON bytes, complete JSON bytes,
and case count. Working size excludes archived cases and resolutions. These
counts are representation measures, not minimum-description-length scores.

## Verified example and limits

`examples/task_model_revision.py` uses actual files. Three separate review rules
become one shared rule. A later tested proposal extends that rule to new source
names. The run passed 32 checks of file creation, unchanged files, changed
contents, and deletion. Git retained all three model versions and the original
observations. Seven cases remained in the final model.

In that example, working-model size changed from 1,061 to 575 to 372 JSON bytes.
The active packet changed from 476 to 238 characters. These are example results.
They do not establish general token savings, lower latency, or better agent
task accuracy. The receivers were software processes, not fresh LLM agents.

Run the example in a new directory. Run `pytest agent_memory/tests/test_models.py`
for revision, evidence, source-change, interpreter, tool, and Git checks.

## Raw source-dependency example

`examples/source_dependency_model.json` uses a single rule for `needs_review`.
It compares only declared paths and excludes ignored paths. It uses `get` for
lookup by file name. An absent source state has the explicit missing-state
value. Other source changes do not trigger review. The rule reads raw request
and policy objects. It does not need a precomputed answer feature.

Replace the evidence placeholders with real note IDs before proposal. The
example states its input and collection limits. Its cases include unrelated
changes, ignored changes, file creation, deletion, and missing sources.

The repair check at
`benchmarks/model-transfer-study/2026-10-13/model-repair/check.py` passes all 11
observed model failures and 252 additional constructed cases. These are
engineering regression checks. They do not measure agent token savings.
# Transfer without repeated agent calls

`memory(action="model_handoff", payload=model, id=active_model_id)` proposes,
checks, accepts, reduces, and predicts a stored challenge. Each operation and
its result remain in the returned record. A failed check blocks acceptance.
The operation does not create evidence. Save actual observations first.

Use `payload=null` to reuse the active model. This does not create a new model
revision when the existing model passes and no reduction is available. Revise
the model when new evidence shows missing conditions. Retain earlier cases.
The separate `model_reduce` operation still creates a proposal without accepting
it. The combined handoff explicitly includes acceptance after a passing check.

Use `payload={"observations":[new_cases]}` with the active model ID when the rules
still explain new observations, or `payload={"observations":[new_cases], "residuals":[exception_rules]}`
when a surprise observation requires a residual exception rule. The backend
retains the old rules and cases, merges any residual rules, checks all historical
and new cases, accepts the revision, and runs algebraic reduction (`reduce`) to
factor shared laws when that reduces working size.
Changed sources still require a new challenge with fresh saved evidence.

The tool returns working check summaries by default. `full=true` returns the
complete workflow audit. The Python API returns the complete audit. The study
records both the full audit and the exact working result sent to the agent.
This is an explicit API result, not a limit on arbitrary tool output.

Reduction can combine complementary Boolean decisions into one conditional
rule. It retains the predicate, evidence, dependencies, and all cases. Invalid
predicate types still produce an unknown result. A reduction is accepted only
after the recorded cases pass. Smaller JSON does not establish token savings.

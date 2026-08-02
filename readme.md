# agent-memory

Git as the memory for a long-running research agent. Commits are the log,
branches are the hypotheses, and the test suite is the only thing it trusts.

```bash
uv pip install -e ".[search]"     # drop [search] if you do not want semantic search
```

Semantic search needs `GEMINI_API_KEY` in a `.env` at the project root. It is
gitignored, and the memory refuses to commit it.

## The one rule

**Always pass the test results to `record()`.** Everything that makes this
better than a pile of commits is derived from them. A commit without them is
invisible to the frontier, the pivot, the loop detector and the prompt.

```python
memory.record("Add the implicit solver", passed=["test_mass"], failed=["test_order"])
```

## The loop

```python
from memory import Memory, Tag

memory = Memory(".")

while True:
    worker(memory.prompt_for("worker"))          # write code
    passed, failed = run_tests()
    memory.record(what_i_did, passed=passed, failed=failed)

    if not failed:
        memory.record("Milestone reached", Tag.MILESTONE, passed=passed)
        continue

    memory.record(diagnosis, Tag.FAIL, passed=passed, failed=failed)

    plan = thinker(memory.prompt_for("thinker"))  # decide what to try next
    memory.branch_from(memory.last_passing().id, plan.branch_name)
    memory.record(plan.directive, Tag.PLAN)
```

`prompt_for()` is the product. It leads with what has already been ruled out
for the tests failing right now, and its size does not grow with history.

## Recording

| call | meaning |
|---|---|
| `record(msg, passed=, failed=, metrics=)` | a work step and what the suite said |
| `record(msg, Tag.FAIL, ...)` | a diagnosed dead end |
| `record(msg, Tag.PLAN)` | the directive for the next worker |
| `record(msg, Tag.LESSON, tests=["test_x"])` | a claim that `test_x` is unreachable this way; retires itself if it ever passes |
| `record(msg, Tag.MILESTONE, ...)` | worth syncing to the remote |

## Asking

```python
memory.prompt_for("worker" | "thinker")  # the context block; start here
memory.last_passing("test_x")            # where to branch back to
memory.frontier()                        # best state ever reached
memory.elites()                          # best state per distinct failure signature
memory.ruled_out()                       # attempts already known to fail here
memory.advice()                          # directives that worked in this same predicament
memory.attempts("test_x")                # every commit that failed one test
memory.episodes()                        # every directive scored by what followed it
memory.regressions()                     # tests traded away (the Goodhart alarm)
memory.stuck()                           # the same failure n times running
memory.lessons()                         # live lessons, disproven ones dropped
memory.history(tag=Tag.FAIL, all_branches=True, limit=0)
memory.visualize()                       # the branch tree, for humans
```

## Moving

```python
memory.branch_from(commit, "new-branch")  # pivot; commit may be an int index or a SHA
memory.switch_to("branch")
memory.inspect(commit); memory.inspect_exit()   # look at an old state, then come back
memory.worktree("branch")                 # a second checkout to run in parallel
memory.diff(); memory.working_diff()
```

Uncommitted work is never lost: anything dirty is committed as
`[CODE] work in progress` before HEAD moves.

## Searching

```python
from memory.semantic import Search

Search(memory).find("have we tried an explicit stepper", tag=Tag.FAIL)
```

Ranks across every branch. Trust the order, not the number: a real match scores
around 0.72 and nonsense around 0.50, so anything near 0.65 is a coin flip.
Prefer `attempts()` and `ruled_out()` when the question has an exact answer.

The index is a `.npz` inside `.agent_memory/`, derived and disposable. Delete it
and the next search rebuilds it.

## Notes

- The memory lives in `.agent_memory/`, never in your project's `.git`.
- `set_remote(url)` plus `auto_sync=True` pushes on milestones and new branches.
- `clear()` archives the old memory; `clear(archive=False)` deletes it.
- Writes are locked, so parallel agent processes can share one memory safely.

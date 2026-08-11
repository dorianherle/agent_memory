# Playbooks

Worked sequences for the situations where memory actually changes the outcome.
Each one is: the trigger, the calls, and what to tell the user.

## 1. Resuming after the session died

**Trigger:** the user says "continue", "where were we", or the injected Recent
ends mid-task with no summary.

The tail of Recent is your crash report. Read it first — it is free. Then unfold
only the last few steps that matter:

```
memory_find(ids=["6ca98198", "a92240bf"])
```

Verify before continuing: the last entry says the tests passed, so run them.
Files may have moved on, or the session may have died mid-edit.

```
memory_save(text="Resumed after session loss; tests still green at 127 passed\n\n"
                 "Evidence: pytest -q → 127 passed, 1 skipped.\n"
                 "Next: finish the rotation path in src/auth/tokens.py.",
            agent="agent")
```

Tell the user in one line what you picked up and what you verified.

## 2. "Why did we do it this way?"

**Trigger:** the user questions a decision, or you are about to undo something
that looks arbitrary.

Search for the decision, not the code. Include one literal token so the query
survives the substring fallback:

```
memory_find(query="decided cookie refresh token instead of localStorage")
```

Empty? Widen once — the reasoning may live on an abandoned branch:

```
memory_find(query="refresh token localStorage", all_branches=True)
```

Still empty means it was never recorded. Say that plainly instead of inventing
a rationale, and save the decision once it is made this time.

## 3. Risky refactor with a safety net

**Trigger:** a wide rename, a migration, deleting a subsystem, anything you
would want to undo in one move.

The snapshot is taken **at save time**, so the save must come first:

```
memory_save(text="Pre-refactor checkpoint: 42 call sites of get_cwd, tests green\n\n"
                 "Next: rename to get_current_working_directory across src/ and tests/.",
            agent="agent")
```

Do the work. Then either:

```
memory_save(text="Rename landed: get_cwd → get_current_working_directory, 42 sites\n\n"
                 "Evidence: pytest -q → 127 passed, 1 skipped; rg get_cwd → no hits.",
            agent="agent")
```

or, if it went badly, offer the rewind — and get consent before touching files:

> The rename broke the plugin loader in a way that needs a different approach.
> I can roll the files back to the checkpoint from before the rename. The work
> stays in memory either way, but the files go back.

```
memory_save(text="Abandoning the flat rename: plugin loader resolves get_cwd by name")
memory_branch(id="<checkpoint id>", name="rename-v2",
              label="Flat rename failed: plugin loader does name-based lookup")
memory_save(text="Rewound to pre-rename tree; next attempt keeps a get_cwd alias")
```

## 4. Comparing against how it used to work

**Trigger:** "this used to work", or you need to diff current behaviour against
a known-good snapshot.

```
memory_save(text="Checkpoint before inspecting the last known-good tree")
memory_inspect(id="39d43eec")
… read files, run nothing that writes …
memory_inspect_done()
```

Non-negotiable: close it in the same turn. While inspect is open every save is
blocked, including the hook that would have saved your answer.

If you need to *keep* the old state rather than glance at it, that is
`memory_branch`, and it needs the user's consent.

## 5. Parallel subagents

**Trigger:** fanning out an investigation across several agents.

Before spawning, write the shared context down. Subagents start with none of
your reasoning, but they can read this store:

```
memory_save(text="Plan: find why cold start regressed from 400ms to 2.1s\n\n"
                 "Split: (a) import graph, (b) DB pool warmup, (c) container image.\n"
                 "Constraint: cannot change the base image this quarter.",
            agent="agent")
```

Each subagent saves findings under its own name:

```
memory_save(text="Import of pandas at module scope costs 1.3s of cold start\n\n"
                 "Evidence: python -X importtime -c 'import app' → pandas 1.31s.\n"
                 "Next: move it under TYPE_CHECKING and import lazily in report.py.",
            agent="explore·a1b2c3")
```

The parent does not re-save what the subagents wrote — the hook already
captured their completions. The parent saves the synthesis, then folds:

```
memory_summarize(text="Cold start 2.1s → 520ms: lazy pandas import was 1.3s of it\n\n"
                      "Changed: src/report.py imports pandas inside the function.\n"
                      "Ruled out: DB pool warmup (18ms), image size (unchanged).\n"
                      "Open: 120ms still unexplained in the settings loader.",
                 agent="agent")
```

## 6. A long investigation that outlasts the turn

**Trigger:** anything that will run for more than a few minutes of tool calls.

Cadence, not volume. Save when your understanding changes:

```
memory_save(text="Reproduced the flake: only fails when tests run in parallel")
… narrowing …
memory_save(text="Flake is a shared tmpdir: two tests write the same fixture file\n\n"
                 "Evidence: tests/conftest.py:41 uses a module-scoped tmpdir.")
… fix and verify …
memory_summarize(text="Fixed the parallel-test flake by scoping tmpdir per test\n\n"
                      "Changed: tests/conftest.py:41 module → function scope.\n"
                      "Evidence: pytest -n 4 green over 20 consecutive runs.")
```

Three saves for an hour of work is right. Thirty is noise; zero loses
everything if the session dies.

## 7. The user states a durable preference

**Trigger:** any "always", "never", "we use", "don't ever" aimed at future work
rather than the current task.

The hook saves their prompt verbatim, which is enough for one turn but will be
buried in Recent by tomorrow. Promote it to something retrievable:

```
memory_save(text="Convention: pytest only, never unittest, for all new tests\n\n"
                 "Source: user, this session. Applies repo-wide.",
            agent="agent")
```

Phrase the headline as the rule itself, so it reads correctly as a one-liner in
state a month from now.

## 8. Handing the task back

**Trigger:** the work is done and you are about to answer.

Do not save your final answer — the hook does that. Do fold, so the next
session starts from one clean line instead of forty:

```
memory_summarize(text="<what is now true> — <the headline outcome>\n\n"
                      "Changed: <files>\n"
                      "Decided: <choice and why>\n"
                      "Open: <what a future agent should pick up>",
                 agent="agent")
```

If anything is unfinished, put it in `Open:`. That line is the single highest
value thing you can leave behind for whoever comes next.

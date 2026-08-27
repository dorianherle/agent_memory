---
name: agent-memory
description: Expert operation of the agent-memory MCP tools (memory_save, memory_find, memory_summarize, memory_open_questions, memory_delete, memory_history, memory_state, memory_check, memory_branch, memory_inspect, memory_inspect_done). Use whenever MEMORY STATE appears in context, at the start of any multi-step or long-running task, when the user refers to earlier work ("continue", "why did we", "like last time", "the thing we decided"), before risky or irreversible edits, when recalling past decisions or dead ends, when folding finished work, when deleting noise from Recent, when coordinating subagents, or when recovering a lost session.
---

# Agent Memory

This is a notebook plus snapshots. Every note is what was decided
**and** the project files at that moment. The notebook is
`.agent_memory/` — it never touches the project's own `.git/`.

Hooks already save the user's prompt and your finished answer.
Everything in the middle only exists if you save it.

## What you see each turn

Already in your context. Do not call `memory_state` to fetch it.

```
## Goal                      ← the original job
[a1b2c3d4] Why is login so slow?

## Steering                  ← latest user course-correct, if any
[e5f6g7h8] wait, also look at the cookie path

## Open questions            ← zero or more
[11111111] Why is it still slow after caching? [6ca98198]

* main (you are here)        ← only when there is more than one line of work

## Recent                    ← newest 10 headlines
[6ca98198] @agent Login loads the whole users table every time
(+4 earlier — memory_find / memory_history)
```

- **Goal** is the original job. Later user notes do not replace it.
- **Steering** is the latest human course-correct. It does not
  replace open questions.
- **Open questions** are unsolved things. A trailing `[id]` is an
  older note the question stands on (here, the finding in Recent).
  Find those ids before you cite or extend them. Close when answered.
  Do not add a question that is already listed.
- `[8-char id]` is real. Use only ids you can see. Never invent one.
- `[stale]` on a Recent line means that note's files have changed
  since it was saved. Check the files before you treat it as current.
  Goal, Steering, and open questions are never marked stale.
- Only the first line of a note appears here, cut at 100 characters.
  Write line 1 like a commit subject. `…` means there is more.
- Finished work is not in this view. Open it with `memory_history`
  or `memory_find`. A `Summary:` line is one finished chunk.
- **Avoid** is each `memory_branch` autopsy: what went wrong.
  A failing `memory_check` latches red until you branch
  back with that sentence.
- `RED` at the top means a check failed. Only `memory_branch`
  is allowed. Agent-memory cannot stop Shell/Write; route
  checks through `memory_check`.
- `INSPECTING [id]` means the working files are **not** current.
  Call `memory_inspect_done` before you do anything else.

`memory_state` is only for SDK runs without hooks, or right after
`memory_branch` changed which line you are on.

## Rules

1. Read the injected state. Do not refetch it.
2. Retrieve before you re-derive. One targeted `memory_find`.
3. Memory is evidence, not truth. Check the files.
4. Line 1 is the whole memory the next agent will see.
5. Save at milestones and **before** risky edits (that save is the undo).
6. Summarize finished work with `memory_summarize`.
7. Hide noise with `memory_delete`. Do not delete findings or the
   current user request.
8. Keep open questions current. Close them when answered or dropped.
   Do not add one that is already listed.
9. Route tests through `memory_check`. A fail latches red: the
   only write left is `memory_branch` to last green. `label` is
   the whole lesson the next agent gets under Avoid — write it
   like a good note (below). Do not keep editing on a red run.

## The loop

| | Ask | Do |
|---|---|---|
| Orient | Does this depend on something already decided? | Read the state. No → skip find. |
| Retrieve | Is the headline enough? | No → `memory_find(ids=…)` |
| Check | Is it still true? | Read the file. |
| Work | — | Normal tools. |
| Save | Would a fresh agent need this? | `memory_save` |
| Open | Still unsolved? | `memory_open_questions` |
| Summarize | Is a chunk finished? | `memory_summarize` |
| Clean | Is a headline just noise? | `memory_delete` |
| Fail | Did `memory_check` fail? | `memory_branch` to last green + what went wrong |

## Writing a note

Only line 1 shows up in state.

```
<what is now true, under ~90 characters>

Why: <the reason you will not remember later>
Evidence: <file:line, command, test, exact error>
Next: <the one next step, or "done">
```

Good: `Duplicate subagent saves come from an empty subagent_id at stop`

Bad:

- `"Investigated the hook file"` — activity, not an outcome
- `"Fixed it"` — fixed what?
- A paragraph before the point — state shows only line 1
- API keys or passwords — notes are plain git

`memory_branch` `label` is the same job: one line the next agent
will treat as law. Name the approach, the failure, and the
constraint.

Good: `Redis cache made p95 worse under this schema. Do not retry unless the session store changes.`

Bad: `"didn't work"` / `"tests failed"` / `"dead end"` — the next
agent will retry the same thing.

Save when your **understanding** changes: a finding, a decision and
why the other option lost, a working checkpoint, a user constraint,
the plan right before you spawn subagents, or ~15 tool calls of
real progress with no save. Save **before** a dangerous edit.

Do not save narration, a restatement of what is already in state,
a thought you will overwrite in a minute, or your final answer
(the hook saves that).

Pass `agent=` (it is in `AGENT_MEMORY_AGENT`) so voices stay apart.

## Finding a note

`memory_find` returns up to 50 full notes. A broad query can dump
thousands of tokens. Start narrow.

| You have | Call |
|---|---|
| An id from state | `memory_find(ids=["a1b2c3d4"])` |
| A filename, symbol, or error | `memory_find(text="EigenModeSource")` |
| Only an idea | `memory_find(query="why we dropped polling")` |
| Work on another line | add `all_branches=True` |
| "What happened?" | `memory_history` — read it, do not paste it |

`query=` needs a Gemini key to search by meaning. Without one it
matches words and says so. Put a real token (path, symbol, error)
in every query so both modes work.

`"Nothing found."` with `ids=` means the id is wrong, not that the
work was never saved. A hit on a `Summary:` also returns the steps
it folded.

Then check. If a note says `auth.py:88`, read `auth.py` before you
edit it.

## Summarizing

`memory_summarize` hides everything since the last summary behind one
line. Call it when a piece of work is actually done, not mid-hunt.

Say what is now true, where it lives, what was decided, what is
still open. Folded steps are still findable and show up under the
summary in `memory_history`.

Summarize when a task completes, before switching tasks, or when state
says Recent is over 30 steps.

## Deleting

`memory_delete(ids=[...])` hides notes from state, find, history,
and the live page. Git still has the snapshot.

Use it for mistakes and "opened file X". Finished work you still
want → `memory_summarize`. A line of work that changed files →
`memory_branch`. Do not delete the current user request.

## The two tools that move files

`memory_branch` and `memory_inspect` **rewrite the working files**.
Save first, or you get `save current changes before …`.

**Look, then come back**

```
memory_save(text="Checkpoint before comparing the old tree")
memory_inspect(id="a1b2c3d4")
… read only …
memory_inspect_done()
```

While inspecting, saves are blocked and hooks stop. Close it in the
same turn. Edits are thrown away on the way back.

**Leave a dead end**

`memory_branch` folds the current line, moves to that note, and
**puts every file back** to that snapshot. Ask first, in plain
words: the files go back; the notes stay. Then save why you moved.

The project's real git is never touched. Old lines stay findable
with `all_branches=True`.

## Subagents

One notebook. Each note is tagged with who wrote it.

- Before fan-out: save the plan and each agent's question.
- As a subagent: save findings. Pass `agent=`.
- As the parent: save the synthesis, not a copy of their notes.

## Talking to the user

Do not paste MEMORY STATE, `memory_history`, or ids. Say what you
recalled in one sentence. To browse: `.agent_memory/live/index.html`.

## If something breaks

| Message | Do |
|---|---|
| `cannot save while inspecting` | `memory_inspect_done()`, then save |
| `save current changes before …` | `memory_save` first, then retry |
| `unknown memory id` | Use only ids you can see |
| `Nothing found.` | Widen: ids → text → query → `all_branches=True` |
| Tool missing | Say so. Do the work. Do not pretend you saved. |

## Before every save

> If a fresh agent saw only this first line, would it know what
> happened and what to do next?

If no, rewrite line 1.

## More

- Worked sequences: [playbooks.md](playbooks.md)
- Mechanics: [internals.md](internals.md)

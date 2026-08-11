---
name: agent-memory
description: Expert operation of the agent-memory MCP tools (memory_save, memory_find, memory_summarize, memory_history, memory_state, memory_branch, memory_inspect, memory_inspect_done). Use whenever MEMORY STATE appears in context, at the start of any multi-step or long-running task, when the user refers to earlier work ("continue", "why did we", "like last time", "the thing we decided"), before risky or irreversible edits, when recalling past decisions or dead ends, when folding finished work, when coordinating subagents, or when recovering a lost session.
---

# Agent Memory

Every entry in this store is **a note plus a full snapshot of the project files
at that moment**, committed to a private git directory (`.agent_memory/`, which
is separate from and never touches the project's own `.git/`). So memory here is
two things at once: a searchable record of what was decided, and a set of
restore points you can return to.

Hooks already save the user's prompt and your finished answer at turn
boundaries. Everything else — what you learned in the middle, why you chose
this over that, the state of the files when it worked — only exists if you
save it.

## Operating contract

If you read nothing else, follow these six.

1. **MEMORY STATE is already in your context.** It is injected every turn by an
   always-applied rule. Do not call `memory_state` to fetch what you can
   already see.
2. **Retrieve before you re-derive.** One targeted `memory_find`, not a fishing
   expedition — and not a silent guess.
3. **Verify before you trust.** Memory records what was true when it was saved.
   Confirm against the current code before acting on it.
4. **The first line of an entry is the whole memory.** Only line 1, cut at 100
   characters, ever appears in MEMORY STATE. Write it like a commit subject.
5. **Checkpoint at milestones and before risky edits.** The save is also the
   snapshot, so a save taken *before* a dangerous change is your undo.
6. **Fold finished work with `memory_summarize`.** And never end a turn while
   `memory_inspect` is open.

## The loop

| Phase | Ask | Move |
|---|---|---|
| Orient | Does this task depend on something decided or found earlier? | Read the injected state. No dependency → skip retrieval entirely. |
| Retrieve | Is the headline enough? | Enough → use it. Not enough → `memory_find(ids=…)` with that headline's id. |
| Verify | Is it still true? | Read the file, run the test. Memory is evidence, not truth. |
| Work | — | Normal tools. |
| Checkpoint | Did something become true that a fresh agent would need? | `memory_save` |
| Fold | Is a chunk of work finished? | `memory_summarize` |

## Reading the injected state

```
## Branches                 ← only when more than one line of work exists
* main (you are here)
[a1b2c3d4] retry-loop: Summary: abandoned the polling approach

## History                  ← folded work, one line per summary, oldest first
[39d43eec] @agent Summary: switched auth to short-lived tokens

## Recent                   ← everything since the last summary
[597bea7a] user: why is login slow
[6ca98198] @agent Root cause: session lookup does a full scan
```

- `[8-char id]` is real and usable in `memory_find(ids=[...])`. **Never invent
  one.** Ids not in your context or in a tool result do not exist to you.
- `@name` is who wrote it — `@agent`, `@user`, `@explore·a1b2c3` for subagents.
- `Summary:` marks a fold. The steps behind it are still stored; ask for that
  id to unfold them.
- Lines are truncated at 100 characters. A headline ending in `…` means the
  entry has more; the rest is one `memory_find` away.
- `INSPECTING [id]` at the top means the working files are **not** current. Call
  `memory_inspect_done` before doing anything else.

`memory_state` is only worth calling when the injection is missing (an SDK run
without hooks) or right after `memory_branch` changed which branch you are on.

## Writing entries worth keeping

Only the first line survives into state, so front-load it. Structure:

```
<specific headline, imperative or declarative, under ~90 chars>

Why: <the constraint or reason that will not be re-derivable later>
Evidence: <file:line, command, test name, exact error text>
Next: <the single next action, or "done">
```

Good:

```
Duplicate subagent saves come from an empty subagent_id at subagentStop

Why: the fallback to agent_id is blank for explore agents, so the session
lookup misses and a second entry is written under a fresh label.
Evidence: agent_memory/cursor/hook.py:245-262; reproduced with
  pytest agent_memory/tests/test_cursor.py -k subagent
Next: resolve the label from the registered session before falling back.
```

Bad, and why:

- `"Investigated the hook file"` — states an activity, not an outcome. A future
  agent learns nothing and must redo the work.
- `"Fixed it"` — fixed what, where, how do I verify?
- `"I looked into the subagent issue and found several things. First…"` —
  the real content is three lines down; state shows only this preamble.
- Anything containing an API key, token, or password. Entry text is stored in
  plain git. (`.env` itself is excluded from snapshots, but your prose is not.)

### When to save

Save on **state changes in your understanding**, not on activity:

- A finding: root cause, a real constraint, the actual shape of an API.
- A decision: chose X over Y, and the reason Y loses.
- A working checkpoint: tests green, build passing, feature demonstrably works.
- **Before** a risky or wide edit — that snapshot is the rollback.
- A durable user preference or constraint ("always use pytest, never unittest").
- The plan, immediately before spawning subagents, so they can find it.
- Any time roughly 15 tool calls have passed with real progress and no save.

Do not save: narration ("opening the file", "now I will check"), a restatement
of what is already in the injected state, a partial thought you will overwrite
in the next minute, or your final answer — the hook saves that for you.

Pass `agent=` with your own name (it is in `AGENT_MEMORY_AGENT`) so the store
can tell voices apart. This matters most when subagents are running.

## Retrieving without drowning

`memory_find` returns up to **50 entries in full body text**, plus every step
folded behind any summary it matched. Hook-saved answers can be hundreds of
words each, so a lazy broad query is a several-thousand-token mistake. Start
narrow and widen only on an empty result.

| You have | Call | Notes |
|---|---|---|
| An id from state | `memory_find(ids=["a1b2c3d4"])` | Most precise. Finds the entry even if it lives on another branch. |
| A distinctive literal (symbol, path, error string) | `memory_find(text="EigenModeSource")` | Case-insensitive substring. Cheapest reliable hit. |
| Only a concept | `memory_find(query="why we dropped the polling retry")` | Semantic when embeddings are configured; silently falls back to substring when they are not. |
| Work abandoned on another branch | `memory_find(query=..., all_branches=True)` | Default scope is the current branch only. |
| A user asking "what happened?" | `memory_history` | Whole branch, folded steps nested. Read it; do not paste it into chat. |

Three things that are easy to get wrong:

- **`query=` may not be semantic.** Without a Gemini key it degrades to a
  substring match, quietly. Symptom: paraphrases return nothing while exact
  wording works. Defense: put at least one literal token that would actually
  appear in the text — a filename, a symbol, an error message — into every
  query, so it works under both modes.
- **`"Nothing found."` with `ids=` means the id is wrong**, not that the work
  was never saved. Unknown ids match silently instead of erroring.
- **Matching a summary unfolds it.** A hit on a `Summary:` line also returns the
  individual steps it covered, so one well-aimed query can reconstruct a whole
  finished chunk.

Then verify. If memory says a function lives at `auth.py:88`, read `auth.py`
before you edit it. Reporting a stale memory as current fact is worse than
having no memory at all.

## Folding finished work

`memory_summarize(text=...)` folds **everything since the last summary** into
one line. There is no partial fold, so call it at a clean boundary — when a
piece of work is actually finished, not mid-investigation.

The text becomes the only visible trace of that whole stretch, forever. Make it
answer: what is now true, where it lives, what was decided, what is still open.

```
memory_summarize(
  text="Auth moved to short-lived tokens; login p95 1.9s → 240ms\n\n"
       "Changed: src/auth/session.py, src/auth/tokens.py, migration 0042.\n"
       "Decided: refresh in a cookie, not localStorage (XSS).\n"
       "Open: rotation for service accounts is still unimplemented.",
  agent="agent")
```

Fold when a task completes, before switching to an unrelated task, and when
state warns that Recent is over 30 steps. Nothing is lost: folded steps stay
retrievable through `memory_find` and appear nested in `memory_history`.

## Snapshots: the two tools that move files

`memory_branch` and `memory_inspect` **rewrite the user's working files**.
Everything else in this system only appends.

Both require a clean memory work tree, so `memory_save` immediately before
either one — otherwise you get `save current changes before …`.

**`memory_inspect(id)` → look at the past, then come back.**

```
memory_save(text="Checkpoint before comparing against the pre-refactor tree")
memory_inspect(id="a1b2c3d4")     # files are now exactly as they were
… read only …
memory_inspect_done()             # files restored
```

While inspecting: `memory_save` raises, and the hooks stop auto-saving — so if
the turn ends here, your answer and the user's next prompt are both silently
lost. Any edit you make is discarded on `inspect_done`. Treat it as read-only
and always close it in the same turn you opened it.

**`memory_branch(id, name, label)` → abandon a dead end and rewind.**

It caps the current line of work with `label` as its summary, moves to `id`,
creates branch `name` there (or resumes an existing branch if `id` is its tip),
and **restores every project file to that snapshot**. Files that memory tracks
and that were created after the snapshot are removed.

This throws away the user's current working tree. **Ask first, always**, in
plain terms: "this rewinds the files to how they were before the retry work —
that work stays in memory and stays findable, but the files go back." Then save
why you moved, immediately.

The project's real git history is untouched by all of this, and nothing in the
memory store is ever deleted — the abandoned branch is still reachable with
`memory_find(..., all_branches=True)`.

## Working with subagents

Subagents share one store and each entry is tagged with who wrote it. Hooks
already record the spawn (`Spawned @explore·a1b2c3: …`) and the completion
(`[completed] …`), so:

- **Before fan-out**, save the plan and the question each subagent owns. That is
  what they will find when they orient.
- **As a subagent**, pass `agent=` and save findings, not narration. Your final
  message is captured by the hook — save the intermediate discovery that would
  otherwise die with you.
- **As the parent**, do not re-save what the subagent already wrote. Save the
  synthesis: what you now believe, and what you are doing about it.

## Talking to the user about memory

Do not paste MEMORY STATE, `memory_history`, or entry ids into chat — ids are
meaningless to a human and the state is already on their screen if they want it.
Say what you recalled in one sentence ("we dropped the polling approach because
it broke under concurrent writes") and move on. If they want to browse, point
at the live view: `.agent_memory/live/index.html`.

## When something goes wrong

| Message | Meaning | Move |
|---|---|---|
| `cannot save while inspecting; call inspect_done first` | Inspect mode is open | `memory_inspect_done()`, then save |
| `save current changes before inspecting` / `before branching` | Work tree is dirty vs. the store | `memory_save(...)` first, then retry |
| `Error: unknown memory id 'x'` | Id does not resolve | Re-read state; use only ids you can see |
| `Nothing found.` | No match, not an error | Widen: `ids` → `text` (distinctive token) → `query` → `all_branches=True` |
| `not inspecting; nothing to return from` | Already closed | Continue |
| Tool is missing entirely | MCP server not loaded | Say so plainly; do the work and report that memory is unavailable, rather than pretending it saved |

## Self-check before every save

> If a fresh agent saw only this first line in MEMORY STATE, would it know what
> happened and what to do next?

If no, rewrite line 1. That single habit is most of the difference between a
memory store that compounds and one that just accumulates.

## More detail

- Worked end-to-end scenarios — resuming a dead session, archaeology, safe
  refactors, dead ends, parallel subagents: [playbooks.md](playbooks.md)
- Exact mechanics — storage model, search pipeline, snapshot excludes, hook
  events, tool-by-tool reference: [internals.md](internals.md)

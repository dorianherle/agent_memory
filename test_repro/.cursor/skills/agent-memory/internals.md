# Internals

Exact mechanics, for when behaviour needs to be predicted rather than guessed.

## Storage model

The store is a git repository whose git directory is `.agent_memory/` and whose
work tree is the project root. Every write runs `git add -A` and commits, so
**each entry is a note plus a snapshot of the whole project at that moment**.
The entry text is the commit message; metadata (`agent`, `covers`, `user`,
`auto`, …) rides along as a JSON payload line inside it.

The project's own `.git/` is never read or written by this. Rewinding memory
does not move the user's real branch, and committing to their repository does
not create memory entries.

Never snapshotted (from `EXCLUDE_ENTRIES` in `core.py`):

```
.agent_memory/   .git/   .svn/   .hg/   .env
__pycache__/     .pytest_cache/  <any nested git checkout>
```

So a snapshot restore leaves `.env`, virtualenv caches and nested repos exactly
as they are. Everything else in the project is fair game and *will* be
overwritten by `memory_branch` / `memory_inspect`.

A SQLite catalog inside `.agent_memory/` mirrors the commits for fast queries,
and an HNSW index holds embeddings when a provider is configured. Both are
derived state and rebuild themselves from the git history.

## Tool reference

| Tool | Signature | Effect |
|---|---|---|
| `memory_save` | `(text, agent=None, payload=None)` | Commit + snapshot. Returns `Saved [id8] @agent`. Warns if line 1 is over 100 chars or reads like narration. Raises while inspecting. |
| `memory_summarize` | `(text, agent=None)` | Same, plus `covers` = every entry since the last summary. Returns `Summarized [id8] @agent`. Same headline warning as save. |
| `memory_open_questions` | `(text="", depends_on=None, ids=None)` | Add a question (`text` + notes it stands on). Empty `text` closes all, or only `ids`. Several can be live. |
| `memory_delete` | `(ids)` | Tombstone. Hides those ids from state / find / history / live HTML. Returns `Deleted [id8], …`. Raises on unknown id. The delete commit is `kind=delete` and does not appear in Recent. |
| `memory_find` | `(query=None, text=None, ids=None, all_branches=False, full=False)` | Read. Search returns up to 8 headlines; `ids=` returns capped bodies, `full=True` removes that cap. A matched summary lists folded child headlines. `query=` without embeddings uses local full-text search. |
| `memory_history` | `(full=False)` | Current branch, oldest first, folded steps indented under their summary. Headlines by default; `full=True` for bodies. |
| `memory_state` | `()` | The same compact state that is already injected each turn. |
| `memory_branch` | `(id, name, label)` | Save label as a summary of the current line, then restore working files to the requested id. Old work stays searchable. |
| `memory_inspect` | `(id)` | **Restores files** to that entry, detached, and blocks saving until closed. |
| `memory_inspect_done` | `()` | Restores the branch and its files. |

There is no edit tool. `memory_delete` appends a tombstone; git history is
never rewritten. Default find / state / history / live HTML skip deleted
targets. `Memory.find(..., include_historical=True)` can still retrieve them.

## How state is rendered

`render_packet()` is the injected view, capped at 1,200 characters by default.
It includes Goal, latest Steering, Handoff, up to eight Open questions and three
Related excerpts as space permits. Omitted entries are signposted and remain
searchable. `render_state()` is the older Goal/Steering/Recent display, retained
for compatibility and dashboard use; it is not the injected packet.

An explicit `payload={"handoff": true}` checkpoint takes precedence over incidental
notes. Otherwise an actionable auto-saved answer (`Next:`, `Blocked:`, `Remaining:`)
or a work note supplies the handoff. A summary provides the fallback. Excerpts
copy complete useful body lines, prioritizing next steps, decisions, rejected
approaches, rollback, constraints and evidence. They do not invoke a model.

Prompts and finished answers retain their full text on disk. The first line is
limited only for display, not storage. `[stale]` compares files changed by the
note's snapshot, plus declared `payload.files`, against the current working tree.
It includes unsaved tracked edits. Notes without changed or declared files have
no automatic evidence invalidation; missing stale markers do not prove validity.

The packet is written to `.cursor/rules/agent-memory-state.mdc` and mirrored to
`.cursor/MEMORY_STATE.md`. Both are gitignored. A character cap is deterministic;
it is not an exact model-token cap. Benchmarks measure tokens separately.

## The search pipeline

`memory_find` resolves in this order:

1. **Scope.** Default is entries reachable from the current branch. `all_branches=True`
   drops the scope. An explicit `ids=` entry that is *not* reachable on the
   current branch automatically widens the search to every branch — an id you
   can see is always findable.
2. **Ids.** Short ids are resolved through `git rev-parse`. An unresolvable id
   is passed through unchanged and simply matches nothing; there is no error.
3. **Ranking.** With an embedding provider, `query=` runs hybrid semantic
   search. Without a provider, or when the provider fails, it uses local SQLite
   full-text search. This does not require a key or a model call.
4. **Substring.** `text=` is a case-insensitive `instr` match on the message.
5. **Unfolding.** Any matched summary lists the entries in its `covers`
   as indented headlines, so hitting a summary shows the work behind it
   without dumping those bodies.
6. **Output.** Search uses `k=8` headlines and skips auto-saved answers.
   Explicit `ids=` return bodies capped at 800 characters; `full=True` opts
   into the complete body when essential evidence is beyond that cap.

Practical consequence: `memory_find(query="…")` returns a short headline
list of work notes. Prefer `ids=` when you need the body.

## Folding

`memory_summarize` stores `covers = [ids of everything since the last summary]`.
State then hides those ids and shows the summary line instead. The fold is
all-or-nothing over that window; there is no way to fold a subset. Folded
entries remain in `memory_find` results and appear indented under their summary
in `memory_history`.

## Delete

`memory_delete` writes a new commit with `kind=delete` and `target_id` set to
the hidden entry. `catalog.state()` and default `select()` exclude both the
tombstone and its target. `graph_data` skips those nodes, so the live HTML
timeline hides them too. Each id is one tombstone; unknown ids raise.

## Snapshot movement

Both movement tools refuse to run against a dirty work tree, where "dirty"
means the project files differ from the last memory commit:

```
save current changes before inspecting
save current changes before branching
```

`memory_save` immediately beforehand always clears this.

`memory_inspect(id)` does a forced detached checkout and writes an `INSPECTING`
marker. While that marker exists:

- `memory_save` raises `cannot save while inspecting; call inspect_done first`
- the `beforeSubmitPrompt` and `afterAgentResponse` hooks skip saving entirely,
  so the user's next prompt and your answer are dropped
- edits to files are discarded when `inspect_done` force-checks the branch back
  out

Inspection persists across client creation and process restarts. Readers never
restore working files. After interruption, explicitly call `inspect_done` to
return; automatic recovery cannot distinguish a crashed owner from another
active agent. Saves remain blocked until inspection ends.

`memory_branch(id, name, label)` saves the current line's summary, then restores
files to the requested snapshot. If that snapshot is another branch's tip, it
resumes that branch; otherwise it creates `name`. The summary stays searchable
in the old history. No test outcome forces a branch change.

## Hook events

| Event | What it does |
|---|---|
| `sessionStart` | Injects instructions and the agent name; sets `AGENT_MEMORY_AGENT`; refreshes the state rule and live HTML |
| `beforeSubmitPrompt` | Saves the user's prompt as `@user`; rewrites the alwaysApply state rule; `additional_context` is only a pointer to that rule (the packet is not pasted twice) |
| `afterAgentResponse` | Saves the finished answer under the acting agent |
| `subagentStart` | Registers the subagent's name and saves `Spawned @name: <task>` under the parent |
| `subagentStop` | Saves `[status] <summary>` under the subagent's name |

Duplicate suppression: an identical consecutive auto-save is skipped, and a user
prompt identical to the newest user entry is skipped. Hooks fail open — a memory
error never blocks the agent loop, it just means nothing was saved.

## Environment

| Variable | Role |
|---|---|
| `AGENT_MEMORY_ROOT` | Project root the store belongs to (the MCP server is also pinned with `--root`) |
| `AGENT_MEMORY_AGENT` | Your name for `agent=` on saves |
| `AGENT_MEMORY_PROVIDER` | Embedding provider import path; absent means substring-only search |
| `GEMINI_API_KEY` | Read from `.agent_memory/credentials.env` or the project `.env` |

## Files on disk

| Path | Role |
|---|---|
| `.agent_memory/` | The store: git dir, SQLite catalog, HNSW index, credentials |
| `.agent_memory/live/index.html` | Self-refreshing browser view of the timeline and prompt packets |
| `.cursor/rules/agent-memory.mdc` | Always-applied bootstrap rule |
| `.cursor/rules/agent-memory-state.mdc` | Always-applied live MEMORY STATE (generated, gitignored) |
| `.cursor/MEMORY_STATE.md` | Markdown mirror of the same state (generated, gitignored) |
| `.cursor/skills/agent-memory/` | This skill |
| `.cursor/mcp.json`, `.cursor/hooks.json` | Server registration and hook wiring |

## If the tools are not there

The MCP server may be unregistered, unreloaded after install, or unavailable to
a subagent. The injected MEMORY STATE can still be present while the tools are
not — reading state does not prove you can write it.

When a `memory_*` call is unavailable: do the actual work, and tell the user
that memory is unavailable so nothing was checkpointed. Never claim a save you
did not make. The fix is usually reloading MCP in Cursor's Settings → Tools &
MCP, or re-running `agent-memory cursor install`.

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
| `memory_save` | `(text, agent=None)` | Commit + snapshot. Returns `Saved [id8] @agent`. Warns if line 1 is over 100 chars or reads like narration. Raises while inspecting. |
| `memory_summarize` | `(text, agent=None)` | Same, plus `covers` = every entry since the last summary. Returns `Summarized [id8] @agent`. Same headline warning as save. |
| `memory_open_questions` | `(text="", depends_on=None, ids=None)` | Add a question (`text` + notes it stands on). Empty `text` closes all, or only `ids`. Several can be live. |
| `memory_delete` | `(ids)` | Tombstone. Hides those ids from state / find / history / live HTML. Returns `Deleted [id8], …`. Raises on unknown id. The delete commit is `kind=delete` and does not appear in Recent. |
| `memory_find` | `(query=None, text=None, ids=None, all_branches=False)` | Read. Search returns up to 12 headlines; `ids=` returns those full bodies. A matched summary lists folded child headlines. `query=` without embeddings appends a substring-fallback notice. |
| `memory_history` | `(full=False)` | Current branch, oldest first, folded steps indented under their summary. Headlines by default; `full=True` for bodies. |
| `memory_state` | `()` | The same compact state that is already injected each turn. |
| `memory_branch` | `(id, name, label)` | Save label as a summary of the current line, then restore working files to the requested id. Old work stays searchable. |
| `memory_inspect` | `(id)` | **Restores files** to that entry, detached, and blocks saving until closed. |
| `memory_inspect_done` | `()` | Restores the branch and its files. |

There is no edit tool. `memory_delete` appends a tombstone; git history is
never rewritten. Default find / state / history / live HTML skip deleted
targets. `Memory.find(..., include_historical=True)` can still retrieve them.

## How state is rendered

`render(compact=True)` produces what you see injected:

- **Goal** is the original job (`goal=True` on the first user save, or
  the oldest user entry if none is marked). It is not folded.
- **Steering** is the latest user note that is not the goal. It is not folded.
- **Open questions** are all `open_question=True` entries. Each line is
  `[id] text [dep] [dep]`. They are not folded. The packet keeps the
  first of any same-text pair (case and spacing ignored). Adding that
  text again returns the existing id. Closing one closes its twins.
- **Current branch** is a single `* name (you are here)` line when more than
  one branch exists. Other branch listings stay in `memory_history`.
- **Recent** is the newest 10 work subjects since the last summary
  (user prompts and auto-saved answers are omitted; Goal and Steering
  already hold the ones that matter). Older Recent lines stay findable;
  a `(+N earlier)` marker says they exist.
- `[stale]` on a Recent subject means that entry's own files changed
  between its commit and HEAD. Goal, Steering, and open questions are
  never marked stale. Note-only (empty) commits are never stale.
- `last @agent checkpoint N steps ago` appears when 8+ Recent items have
  passed since the last non-auto, non-user save.
- Each line is `[8-char id] @agent <prefix><first line of the message>`, where
  prefix is `Summary: ` for folds and `user: ` for the user's own prompts.
- **Only the first line is shown, truncated to 100 characters** with a trailing
  `…`. Multi-paragraph entries are treated as commit bodies.
- Over 30 entries in Recent (full count, not the visible 10) adds a nudge
  to summarize.
- While inspecting, an `INSPECTING [id]` banner is prepended.

The same text is written to `.cursor/rules/agent-memory-state.mdc` (an
always-applied rule, rewritten on every durable write) and mirrored to
`.cursor/MEMORY_STATE.md`. Both are gitignored.

## The search pipeline

`memory_find` resolves in this order:

1. **Scope.** Default is entries reachable from the current branch. `all_branches=True`
   drops the scope. An explicit `ids=` entry that is *not* reachable on the
   current branch automatically widens the search to every branch — an id you
   can see is always findable.
2. **Ids.** Short ids are resolved through `git rev-parse`. An unresolvable id
   is passed through unchanged and simply matches nothing; there is no error.
3. **Ranking.** With an embedding provider (`AGENT_MEMORY_PROVIDER` +
   `GEMINI_API_KEY`), `query=` runs a hybrid semantic search with a small
   recency weight (0.05) and de-duplication. Without a provider — or if
   embedding raises for any reason, including a missing key — `query` is
   treated as a substring and the result ends with
   `(substring fallback — set GEMINI_API_KEY for semantic)`.
4. **Substring.** `text=` is a case-insensitive `instr` match on the message.
   Under fallback, `query` is appended to `text`.
5. **Unfolding.** Any matched summary lists the entries in its `covers`
   as indented headlines, so hitting a summary shows the work behind it
   without dumping those bodies.
6. **Output.** Search uses `k=12` headlines. Explicit `ids=` return full
   bodies of those notes only.

Practical consequence: `memory_find(query="…")` on a vague concept returns
a short headline list. Prefer `ids=` when you need the body, then `text=`
with a symbol or path, then `query=` with at least one literal token.

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

A session that dies while inspecting is repaired on the next load: the store
detects the stale marker and returns to the branch automatically.

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

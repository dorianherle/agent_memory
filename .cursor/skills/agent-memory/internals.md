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
| `memory_save` | `(text, agent=None)` | Commit + snapshot. Returns `Saved [id8] @agent`. Raises while inspecting. |
| `memory_summarize` | `(text, agent=None)` | Same, plus `covers` = every entry since the last summary. Returns `Summarized [id8] @agent`. |
| `memory_find` | `(query=None, text=None, ids=None, all_branches=False)` | Read. Up to 50 full entries, newest first, plus steps covered by any matched summary. |
| `memory_history` | `()` | Whole current branch, oldest first, folded steps indented under their summary. |
| `memory_state` | `()` | The same compact state that is already injected each turn. |
| `memory_branch` | `(id, name, label=None)` | Caps the current branch with `label`, moves to `id`, **restores files**. |
| `memory_inspect` | `(id)` | **Restores files** to that entry, detached, and blocks saving until closed. |
| `memory_inspect_done` | `()` | Restores the branch and its files. |

There is no delete or edit tool. Memory only ever grows.

## How state is rendered

`render(compact=True)` produces what you see injected:

- **Branches** block appears only when more than one branch exists; the current
  one is marked `(you are here)`.
- **History** is every summary, oldest first. **Recent** is everything after the
  last summary.
- Each line is `[8-char id] @agent <prefix><first line of the message>`, where
  prefix is `Summary: ` for folds and `user: ` for the user's own prompts.
- **Only the first line is shown, truncated to 100 characters** with a trailing
  `…`. Multi-paragraph entries are treated as commit bodies.
- Over 30 entries in Recent adds a nudge to summarize.
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
   embedding raises for any reason, including a missing key — it silently falls
   back to treating `query` as a substring.
4. **Substring.** `text=` is a case-insensitive `instr` match on the message.
   Under fallback, `query` is appended to `text`.
5. **Unfolding.** Any matched summary pulls in the entries listed in its
   `covers`, so hitting a summary returns the work behind it.
6. **Output.** `k=50`, rendered as full message bodies, not subjects.

Practical consequence: `memory_find(query="…")` on a vague concept can return
tens of full agent answers. Prefer `ids=`, then `text=` with a symbol or path,
then `query=` with at least one literal token in it.

## Folding

`memory_summarize` stores `covers = [ids of everything since the last summary]`.
State then hides those ids and shows the summary line instead. The fold is
all-or-nothing over that window; there is no way to fold a subset. Folded
entries remain in `memory_find` results and appear indented under their summary
in `memory_history`.

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

`memory_branch(id, name, label)`:

- with `label`, first commits a summary capping the current branch — that line
  becomes the branch's label in the Branches block
- if `id` is the tip of an existing branch, that branch is resumed and `name` is
  ignored; otherwise a new branch `name` is created at `id`
- working files are restored to the target snapshot
- the result string reports where you landed and asks you to save why

## Hook events

| Event | What it does |
|---|---|
| `sessionStart` | Injects instructions and the agent name; sets `AGENT_MEMORY_AGENT`; refreshes the state rule and live HTML |
| `beforeSubmitPrompt` | Saves the user's prompt as `@user`; rewrites the injected MEMORY STATE; records the full prompt packet for the live view |
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

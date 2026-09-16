# Agent Memory: fresh-context review and implementation plan

Reviewed 2026-09-09 against source commit `5476e3e`.

## Recommendation

Preserve the central purpose: very long-horizon, multi-turn work on stable goals.
Build a project memory service with multiple isolated goal spaces, each able to
prepare a small, evidence-backed context packet for the next question. Preserve
project knowledge, user decisions, unfinished work, and their evidence. Give
each goal its own lifecycle, independent of chat sessions and source branches.
Share applicable lessons through an explicit project knowledge layer.
Each goal graph must have its own independent Git repository, with its own
HEAD, refs, objects, catalog, and recovery state. Concurrent code execution also
needs a separate workspace per goal.

The repository has a useful storage foundation. Its current product contract is
one persistent job with an original Goal, subsequent Steering, and snapshots.
That contract is useful **within each goal space**. Its current project-wide
application cannot distinguish independent goals in the same project. More
embeddings or shorter summaries alone will not solve this mismatch.

The companion [goal spaces and deterministic learning design](../design/goal-spaces-and-learning.md)
specifies how to retain the long-horizon loop, control cross-learning, and steer
search using verified outcomes.

Fresh contexts can reduce carryover from earlier reasoning. They cannot guarantee
unbiased or correct answers: a wrong saved conclusion can bias every new agent.
Measure recovery accuracy, relevance, contradiction handling, and evidence
quality instead of promising an uncontaminated model.

This review adds a reproducible audit and a design proposal. It does not change
runtime behavior or migrate the existing notebook.

## What was verified

- Read the storage, catalog, search, prompt assembly, wrappers, MCP tools, hooks,
  installer, skill, and tests.
- Ran the existing suite in a temporary copy through `Memory.check`:
  **187 passed, 1 skipped**, 56.5 seconds, Python 3.14.3 on macOS. The skipped test
  requires a live Gemini key. No live provider evaluation was performed.
- Ran fifteen synthetic observations in disposable repositories, also through
  an isolated `Memory.check`. They use no user files or model calls.
- Read this project's existing memory. Its returned Goal and Steering belonged
  to earlier conversations, illustrating the task-boundary problem in practice.
- No fresh-agent task-success benchmark was run. Passing storage tests is not
  evidence that an agent will reliably resume work.

Reproduce the observations from the repository root:

```bash
.venv/bin/python scripts/audit_clean_context.py
```

The script reports current behavior; it is a characterization probe, not a test
suite that declares the observed defects desirable. Recorded results:
[2026-09-09-observations.json](2026-09-09-observations.json).

## Findings

### 1. Goal identity is project-wide, so unrelated work becomes Steering

`split_goals` picks the first marked goal or oldest user entry.
`save_user` only creates a goal when none exists. `_steering` selects the latest
user entry across the memory branch. A chat session identifier does not scope
these records.

Probe: finish “Fix authentication timeouts,” reopen memory, then ask “Build
invoice export.” The Goal remains authentication; invoice export becomes
Steering. Two simultaneous tasks also share this selection policy.

Source: `agent_memory/core.py:101`, `agent_memory/core.py:598`,
`agent_memory/prompt.py:149`, `agent_memory/cursor/hook.py:122`.

**Change:** introduce `project_id`, `task_id`, `session_id`, and `turn_id`, and
route each goal to its independent Git-backed memory store.
Task creation, continuation, and completion must be explicit operations. Hooks
bind a host session to a task; “new chat” can resume an existing task without
reusing the chat history. A new independent request gets a new task.

### 2. Completed knowledge disappears from the startup packet

Summaries remain stored and searchable, but compact rendering omits historical
summaries. `prompt_sections` currently supplies only failed-path autopsies.
There is no curated project knowledge section or retrieval based on the current
question when building the packet.

Probe: save and summarize the project's build command, reopen memory. The
command and summary are absent from the packet, although the summary is still
retrievable by ID. A new agent has to guess that this information exists.

Source: `agent_memory/core.py:54`, `agent_memory/core.py:740`,
`agent_memory/prompt.py:177`, `agent_memory/prompt.py:300`.

**Change:** promote selected durable facts and user decisions into project
knowledge, preserving source links. Build each packet using the new question,
task, and current repository state. Summarization should compact episodes
without removing the discovery path to reusable knowledge.

### 3. Freshness checks do not establish whether a claim is current

`_stale_ids` compares files changed by a note's own snapshot with later memory
commits. It does not record which files were read as evidence. Notes saved
without file changes are never marked stale; unsaved working-tree edits are
also missed. Goal, Steering, and questions deliberately suppress stale labels.

Probe: a read-only note says `timeout=30`. The file changes to `timeout=60`,
and a new snapshot is saved. The old note still appears without a stale mark.
A separate probe shows the same omission for an unsaved file edit.

Source: `agent_memory/core.py:708`, `agent_memory/core.py:762`,
`agent_memory/prompt.py:335`.

**Change:** store explicit evidence references: relative path, content hash,
optional symbol, captured revision, and observation time. Compare dependencies
with the live worktree before injection. Use `current`, `needs_recheck`,
`superseded`, and `unknown` states. An unchanged file does not prove external
behavior remains true; runtime observations need their own validity conditions.

### 4. Retrieval and prompt assembly lack an overall size budget

The Python `find` API has an approximate message budget, but the MCP-facing
action does not expose it. It retrieves up to 50 full notes and automatically
adds up to 50 covered notes. Bodies have no size limit. The packet caps Recent
but includes every open question and its dependency IDs.

Probe: opening one summary returned **35,243 characters**, including all twelve
synthetic folded notes. The displayed estimate of 8,811 tokens is only
`ceil(characters / 4)`, not a tokenizer measurement. Sixty open questions added
6,739 characters to a startup packet; the count can continue growing.

Source: `agent_memory/tools.py:209`, `agent_memory/core.py:496`,
`agent_memory/cursor/mcp.py:47`, `agent_memory/prompt.py:343`.

**Change:** separate ranked search previews from explicit note expansion. Add
limits, pagination, and a total response budget including metadata and evidence.
Expanding a summary should return the summary and child references by default.
Allocate one packet budget across knowledge, task state, questions, and lessons.
Never silently truncate the current user request or an essential constraint.

### 5. Offline search misses ordinary multiword queries

Without an embedding provider, `query` becomes one literal substring. The
catalog already has FTS5 search, but this path does not use it.

Probe: “authentication timeout” misses “Authentication now uses a timeout of
30 seconds.” The existing catalog's FTS finds the same note. This is a basic
lexical retrieval issue, independent of semantic similarity.

Source: `agent_memory/core.py:447`, `agent_memory/catalog.py` (`fts`),
`agent_memory/search.py:186`.

**Change:** make FTS5/BM25 the default local search, preserving `text=` for exact
substring lookup. Use optional embeddings to add candidates. Apply project,
task, branch applicability, supersession, and evidence filters in both paths.
Report provider errors accurately and keep local retrieval functional.

### 6. The wrapper neither enforces a fresh context nor captures final answers

The OpenAI-compatible wrapper forwards all supplied messages, then appends
memory tool calls and responses inside its loop. It returns a finished answer
without calling `auto_save`; the streaming path likewise has no final-answer
capture. The Cursor wrapper sends to the same underlying agent instance.

Probe: an earlier assistant message reaches the wrapped client unchanged; the
finished answer is absent after reopening memory. Cursor hooks do save answers,
so durability differs by integration.

Source: `agent_memory/wrappers.py:83`, `agent_memory/wrappers.py:116`,
`agent_memory/wrappers.py:158`, `agent_memory/cursor/hook.py:208`.

**Change:** define a host integration contract for startup, prompt capture,
tool evidence, completion, interrupted runs, and idempotent retries. Add a
separate fresh-run mode; preserve the existing wrapper's pass-through behavior
for callers that rely on it. Test final-answer capture for streaming,
nonstreaming, cancellation, and external-tool handoffs.

### 7. A passing check can certify the wrong snapshot

`check` runs against current working files, then records the existing memory
HEAD as green. It neither requires a clean snapshot nor records the exact tree
that was tested. Further, any failing command enforces a global red latch that
blocks normal memory writes and another check until a branch rewind.

Probe: save “old snapshot,” edit to “new verified content,” run a passing check
that reads the new content. The green checkpoint still contains “old snapshot.”
This makes “last green” an unreliable description of verified files.

Source: `agent_memory/core.py:960`, `agent_memory/core.py:983`,
`agent_memory/core.py:1033`.

**Change:** associate a check with a captured tree identity and command result,
and detect edits during execution. Label an unsnapshotted result accordingly.
Record a failure as evidence and allow fixing/rechecking. Make forced rewind
an optional experiment policy, scoped to a task/worktree. A failing regression
test is often useful progress and is not sufficient evidence to reject an
entire approach forever.

### 8. Starting another client can change the working files

`Memory._init` treats any inspection marker as an abandoned inspection and calls
`inspect_done`. It cannot distinguish an active inspector from a crashed one.
MCP calls construct a new `Memory` instance through `get_memory`.

Probe: inspect an old version, then construct another memory client. The working
file immediately returns to the current version and inspection is cleared.

Source: `agent_memory/core.py:143`, `agent_memory/core.py:1090`,
`agent_memory/core.py:1110`, `agent_memory/cursor/common.py` (`get_memory`).

**Change:** make inspection read-only via `git show` or an isolated snapshot
directory. Separate memory retrieval from operations that restore the user's
worktree. If legacy live inspection remains, add an owner/lease and recover only
demonstrably abandoned operations.

### 9. Default snapshots can ingest sensitive files in a new project

Every save snapshots the project. The built-in exclusions protect `.env`, but
not `.env.local`; protection then depends on the project's ignore rules.

Probe in a synthetic project without a `.gitignore`: `.env` is excluded and
`.env.local` is tracked. Only dummy content was used.

Source: `agent_memory/core.py:33`, `agent_memory/core.py:389`.

**Change:** make whole-worktree snapshots optional, add conservative secret and
generated-file exclusions, and show snapshot scope during setup. Keep raw
transcripts separate from knowledge. Filtering must happen before persistence
and before sending material to an embedding provider; hiding a note in search
does not remove it from Git history or derived storage.

### 10. Failed approaches become unconditional advice

`prompt_sections` picks recent autopsies across branches without matching the
current question or checking whether the original conditions still apply.
General saves have no enforced evidence schema; the MCP interface has no
correction/supersession operation even though the Python core has `supersede`.
The wrapper injects stored text inside a system message.

An additional probe confirms that a login/Redis autopsy is injected for an
unrelated invoice-export request. The prompt trust-boundary concern remains a
source-level finding, not a demonstrated injection exploit or model failure.

Source: `agent_memory/core.py:740`, `agent_memory/core.py:910`,
`agent_memory/cursor/mcp.py:36`, `agent_memory/wrappers.py:123`.

**Change:** retrieve failed approaches only with their conditions, evidence,
and reconsideration triggers. Separate user requirements, observations,
decisions, hypotheses, and historical transcripts. Return stored content as
explicitly untrusted evidence; keep behavioral instructions in a separate
trusted adapter prompt. Corrections should supersede claims while retaining
their audit trail. Neither repeated retrieval nor agreement between agents
should count as independent verification.

### 11. Concurrent initialization can create two original goals

Goal detection and user-note deduplication run before `save` obtains the Git
lock. A controlled interleaving of two clients completing their pre-save reads
before either write produced two entries marked `goal=True`. The renderer then
picks one. The lower-level commit lock protects Git writes, not the domain
invariant. Open-question deduplication uses a similar read-before-lock pattern.

Source: `agent_memory/core.py:598`, `agent_memory/core.py:653`,
`agent_memory/core.py:344`.

**Change:** check identity, deduplication, goal binding, and expected state version
inside the same serialized domain operation. Use idempotency keys for host
events and a unique goal identity. A new client should not initialize or repair
shared state without coordination.

### 12. Save can commit successfully and then report failure

Embedding queue setup happens after the Git commit. A provider configuration
error propagates from `_queue_embedding` even though the note is already durable.
The probe forces a local provider-construction failure and confirms that the
reported failed save is retrievable afterward.

Source: `agent_memory/core.py:334`, `agent_memory/core.py:403`.

**Change:** acknowledge the durable event independently of optional indexing.
Report search/index degradation separately, and return the original event ID
when a caller retries the same idempotency key after a lost acknowledgement.

## Subsystem review

These are architectural assessments grounded in source and the existing suite.
The explicit probes above establish specific defects; other risks below are
identified from source and still need targeted failure/load tests.

| Piece | What holds | What must improve |
|---|---|---|
| `core.py`: Git event history | Durable IDs, append-style changes, independent host Git metadata | Domain operations currently mix memory, worktree snapshots, branching, rendering, and checks in one class; isolate responsibilities |
| `catalog.py`: SQLite projection | WAL, branch scoping, tombstones, replay, persistent embedding cache | Coordinate initialization/rebuild; make event projection and watermarks consistent; avoid rebuilding all branch reachability on each append |
| `search.py`: candidate selection | Hybrid candidates, scoped filtering, persistent query cache | Search waits for all missing document embeddings; local search should answer immediately and optional enrichment should have bounded deadlines |
| `ann.py`: vector index | File/thread locks, incremental vectors, cache rebuild | Keep optional; freeze index/version for reproducible evaluation; enforce exact final scope filters |
| `providers/*` | Clean provider protocol; model/query fingerprints | Explicit network timeouts, retry bounds, finite/consistent vector validation; OpenAI-compatible `urlopen` supplies no explicit timeout |
| `prompt.py` | Concise headlines, useful work/question distinction | Goal scope, evidence-aware selection, whole-packet budget, conflict handling; summaries need a discovery path |
| Summarize/delete/supersede | Recoverable folds and correction primitive in core | Subset/per-goal folding; expose corrections to MCP; hiding must remain distinct from physical erasure |
| Open questions | Dependencies and duplicate detection exist | Typed required versus optional questions, dependency status, per-goal atomic transitions; closing is not proof of resolution |
| Check/latch/autopsy | Explicit failures are visible across restarts | Exact tested tree, inconclusive outcomes, measured goals, scoped conditional lessons, positive successful workflows |
| Branch/inspect | Snapshots can support experimental recovery | Separate goal/attempt identity from physical checkout; reads must not change another client's files |
| `tools.py` and MCP | Usable tool boundary; most domain behavior reusable | Goal IDs, structured records/results, budgets, pagination, correction, health; host-neutral entrypoint |
| `wrappers.py` | Existing SDK requests and external tools pass through | Completion durability, interruption capture, explicit fresh-run mode, bounded internal loops, consistent streaming semantics |
| Cursor hooks/common/install | Captures prompts/finals, root pinning, merged install, credentials outside committed config | Session-scoped packets, atomic delivery IDs, degradation status, platform/integration diagnostics; shared rule is unsuitable for parallel goal contexts |
| `identity.py` | Agent attribution and session registration | Attribution is not isolation; JSON read/modify/write needs transactional storage or locking; bind session to goal and attempt |
| `turn_prompts.py` | Valuable record of memory-related injected context | Atomic/versioned event snapshots; include policy and evidence revision; current sidecar excludes tool JSON schemas and host's full prompt |
| `live_html.py`, `viz.py`, `canvas.py` | Helpful visibility into history and injected material | Make visualization an asynchronous reader; incrementally export; add goal lanes, rule reasons, and evidence status instead of expanding UI before core semantics |
| `live_reveal.py` | Optional loopback helper and current-worktree display | Starts from live export; wildcard CORS and no request token/project restriction on reveal warrant hardening before broad install; actual browser exploitability was not tested |
| Tests and demo | Broad behavior coverage, offline provider doubles, replay tests | Cross-goal recovery, concurrency, fault injection, goal verification, negative transfer, long-horizon model evaluation; demo assertions currently test autopsy display |

The catalog/ANN foundations merit reuse. The missing piece is an explicit domain
model connecting goal, observed state, attempt, measured outcome, and reusable
policy. Avoid adding more prompt rules to compensate for that missing model.

## Growth measurement

Synthetic local microbenchmark; one unchanged text file, no embeddings, no
summaries. Read operations are medians of three samples. Save time is the median
of the most recent ten saves. This is one run, not an SLA or a production-scale
benchmark.

| Notes | Save ms | Reopen ms | State ms | Literal search ms | Live export ms | Live data bytes |
|---|---:|---:|---:|---:|---:|---:|
| 10 | 100.868 | 10.537 | 130.045 | 14.690 | 174.818 | 18,209 |
| 100 | 69.160 | 17.935 | 132.349 | 18.960 | 170.354 | 112,988 |
| 500 | 60.504 | 14.818 | 214.332 | 94.015 | 272.633 | 535,843 |

Recent's headline cap works for this case: packet text grew from 689 to 824
characters. Live data grew with history. At 500 notes, building state and live
export already costs hundreds of milliseconds independently of model or network
latency. The current hook/MCP paths can regenerate them more than once per event.
Source also shows full history traversal in graph generation, full ref-membership
rebuilds after appends, and synchronous embedding completion before semantic
search. These deserve targeted optimization and larger-scale measurements.

The changing save times show why this small sample must not be extrapolated into
a complexity or throughput claim. Large worktrees, 10,000+ notes, parallel
writers, and remote providers remain unmeasured.

Reproduce with `scripts/benchmark_memory_growth.py`; raw results are in
[2026-09-09-growth.json](2026-09-09-growth.json).

## Target behavior

```mermaid
flowchart LR
    A[New user question] --> B[Resolve project and task]
    B --> C[Select relevant knowledge and handoff]
    C --> D[Check evidence and fit context budget]
    D --> E[Fresh agent run]
    E --> F[Save evidence and next step]
    F --> C
```

A fresh context begins for each user question. Temporary tool history remains
available within that run so the agent can execute coherently. A task can span
many such runs. Recovery after a crash uses the last acknowledged checkpoint
and inspects the current worktree for changes after it.

MCP supplies memory tools/resources; the host owns conversation history and
context aggregation. Consequently, automatic context reset needs a cooperating
host adapter or a runner that owns model requests. This division follows the
[MCP architecture specification](https://modelcontextprotocol.io/specification/2025-06-18/architecture/index).

Offer three explicit integration levels:

| Integration | Guarantee |
|---|---|
| Generic MCP | Memory is available when the host/agent retrieves and saves it |
| Tested host adapter | Bootstrap and durable capture at supported lifecycle events |
| Fresh-run runner | Every new user question begins a new model context with a selected packet |

The package currently ships a Cursor installer and generic Python wrappers.
The presence of local configuration for another host does not establish a
packaged, tested adapter. Add capability diagnostics that say which guarantees
are active. A successful MCP connection alone is insufficient.

### Minimum record model

| Record | Required content |
|---|---|
| Project knowledge | One fact/decision/constraint, scope, source, evidence status, validity conditions, supersession links |
| Task | Objective, status, acceptance criteria, constraints, latest handoff, open questions |
| Handoff | Verified progress, relevant files, checks, pending action, next step, worktree identity |
| Evidence | File hash/revision or command result, timestamp, source event, originating session |
| Episode | Raw user/assistant/tool events, task/session/turn IDs, completion or interruption status |

Keep these records small and typed. Store conclusions and concise reasons,
not private chain-of-thought. Large transcripts and tool logs remain behind
references and are excluded from default retrieval. Mark inferred evidence
links as unverified until checked.

Use the existing append-only history and rebuildable SQLite catalog within each
independent goal repository initially.
Split optional source snapshots from memory semantics incrementally. No remote
vector database or wholesale storage rewrite is needed for the first version.

### Proposed MCP surface

Names below are proposed, not currently implemented.

| Operation | Purpose |
|---|---|
| `memory_context` | Given question and optional task, return a bounded startup packet and selection reasons |
| `memory_task` | Start, bind/resume, list, or complete a task |
| `memory_record` | Save a typed observation, decision, constraint, or checkpoint with provenance |
| `memory_search` | Return ranked previews with filters, budget, and pagination |
| `memory_get` | Expand selected records/evidence under a budget |
| `memory_correct` | Supersede or retract a claim and retain its history |

Task completion should atomically save the handoff, resolve applicable questions,
and close the task. Keep existing tools as compatibility adapters while clients
migrate. Raw event capture belongs to the host integration; asking the agent to
remember every tool output is not a durability guarantee.

### Packet selection and trust

1. Resolve the project and worktree from explicit registration. Scope tasks by
   ID; never use the latest prompt in a shared file as another chat's task.
2. Begin with the current request and relevant explicit user constraints.
   List resumable tasks if continuation is ambiguous; do not silently choose a
   completed task. Ordinary new questions should not require a setup interview.
3. Retrieve task handoff, relevant project facts/decisions, and applicable
   lessons using FTS first, with optional semantic candidates.
4. Apply supersession, evidence validity, branch/worktree applicability, and
   relevance filters before ranking. Include material contradictory evidence.
5. Deduplicate and fit one total budget. A starting target is 1,500–2,500 memory
   tokens, to be tuned by evaluation, separate from the user's full request.
   Use the target model's tokenizer when available; otherwise enforce an
   explicit conservative byte budget and report that tokens are estimated.
6. Return IDs, compact evidence references, uncertainty, omitted-item counts,
   and why each record was included. The agent can request more detail.

An old “Redis was slower” note should include the measured workload and the
conditions under which retrying is reasonable. A historical hypothesis must
not become a user constraint just because it appeared in a summary.

## Implementation sequence

### First: correct boundaries and retrieval

- Add a goal registry and independent Git memory stores/workspaces per goal.
  Verify that reads, writes, failed checks, and restores in A cannot affect B.
- Add task/session IDs and lifecycle operations; derive per-task Goal and
  Steering. Keep project knowledge separate.
- Add `memory_context(question, task_id, budget)` with local FTS retrieval and
  useful project facts after summarization.
- Expose limits and summary expansion controls; budget every section.
- Add acceptance tests for independent tasks, continuation, completed-task
  knowledge discovery, and bounded responses.

This is the first useful release: a clean agent can distinguish what the user
is asking now from what happened before.

### Next: reliability and evidence

- Capture evidence dependencies and supersession; invalidate against the live
  worktree, including dirty files.
- Fix check-to-snapshot identity and remove mandatory global rollback from the
  default memory workflow.
- Make inspection read-only and optional snapshots safe for concurrent clients.
- Add transactional task/handoff writes and idempotency keys. Test two clients,
  dropped connections, process crashes, and repeated hook deliveries.

### Then: repeatable installation and fresh-run integration

- Extract the host-neutral MCP server from the Cursor namespace while retaining
  a compatibility entrypoint.
- Add `init`, `serve`, and `doctor`; make embedding dependencies optional for
  local search. Document platform support (`fcntl` currently assumes POSIX).
- Implement and test each host adapter's actual lifecycle events and root
  binding. Scope generated state per session instead of one shared live rule.
- Implement a runner for strict fresh context per new question. Capture the
  completed answer and structured handoff in both stream modes; mark interrupted
  runs as interrupted. Retry using event IDs rather than text deduplication.

### Finally: prove quality before adding complexity

Build deterministic recovery fixtures first, then a repeated agent benchmark.
Compare no memory, the existing memory, and the proposed packet under matched
models and budgets. Include wrong notes and irrelevant history deliberately.

| Scenario | Required outcome |
|---|---|
| Completed task followed by unrelated task | No inherited active goal or unrelated constraints |
| New context continues interrupted task | Correct next step, with unresolved work visible |
| Summary contains a required build command | Command discoverable without guessing an old ID |
| Evidence file changed without a memory save | Old claim marked for recheck |
| New evidence contradicts a saved claim | Correction surfaced; old claim not asserted as current |
| Prior failure's conditions no longer hold | Approach reconsidered using current evidence |
| Two clients work on different tasks | No task state crossover or source-file movement on read |
| Hook delivery is repeated or interrupted | No duplicate event; interruption not labeled completion |
| Notebook has 10,000 records | Packet remains within budget; measured latency remains acceptable |
| Stored text contains adversarial instructions | Treated as data; no unauthorized tool action |

Measure completion rate, correct next-step selection, unsupported-claim rate,
irrelevant memory exposure, extra questions needed from the user, tokens, and
latency. Zero task crossover and strict packet-size compliance are deterministic
requirements. Model success and bias reduction need empirical results, not
unit-test counts or self-ratings.

## Migration

Preserve the old store and provide a dry-run import report. Import old records
as historical episodes with their existing IDs, timestamps, and branch
provenance. Do not automatically promote old assistant answers to verified
project facts or treat the oldest prompt as an active task.

Create a legacy task grouping for unscoped history and link explicitly selected
unfinished work to new task records. Derive project facts only with evidence
or clear user attribution. Version the record schema and packet policy; retain
the ability to read the original history while the new catalog is rebuilt.

The first implementation should focus on task isolation, local retrieval, and
bounded context assembly. Those changes directly support the desired experience:
open a new chat, ask the next question, and recover the relevant project context
without repeating the conversation.

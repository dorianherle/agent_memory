# Agent Memory

Long-running agents that remember — without dragging the whole chat around.

The prompt stays small. Finished work folds into one line. Detail reopens on demand. Dead ends branch away. Nothing is deleted.

Memory owns durability. The agent owns judgment — including mid-turn
`memory_save` checkpoints when a run outlasts the next hook.

## Cursor

```bash
pip install "agent-memory[cursor] @ git+https://github.com/dorianherle/agent_memory.git@main"
cd your-project
agent-memory cursor install
```

The installer **always requires a Gemini choice**:
- interactive: prompts `1` configure / `2` skip
- non-interactive: needs `--gemini-api-key`, `GEMINI_API_KEY`, project `.env`,
  existing credentials, or explicit `--skip-gemini` (no silent skip)

It writes:

- `.cursor/mcp.json` — MCP tools + non-secret `AGENT_MEMORY_PROVIDER`
- `.cursor/hooks.json` — automatic saves
- `.cursor/rules/agent-memory.mdc` — how the agent should use it
- `.cursor/agent-memory-sdk.md` — Cursor SDK recipe (`setting_sources`)
- `.cursor/MEMORY_STATE.md` — live compact state
- `.agent_memory/credentials.env` — `GEMINI_API_KEY` (gitignored; loaded at runtime)

Install also prints IDE + SDK next steps. Re-run anytime; merges safely.

Non-interactive:

```bash
agent-memory cursor install --gemini-api-key "$GEMINI_API_KEY"
# or skip embeddings:
agent-memory cursor install --skip-gemini --no-interactive
```

### Cursor IDE

Reload MCP in **Settings → Tools & MCP**. Open Agent chat.

| When | What happens |
|------|----------------|
| Session starts | MEMORY STATE is injected |
| You send a prompt | saved automatically |
| Long mid-turn work | agent calls `memory_save` to checkpoint |
| Agent needs detail | `memory_find` / `memory_history` |
| A chunk of work is done | `memory_summarize` |
| Agent finishes an answer | saved automatically |

Everything else lives in `.agent_memory/` beside the project — a separate Git store. Your repo is untouched (`.agent_memory/` is added to `.gitignore`).

Working in this repo?

```bash
pip install -e ".[cursor]"
agent-memory cursor install
```

### Cursor SDK (no wrap)

Install already created `.cursor/mcp.json` and `.cursor/hooks.json`. Local SDK agents **still ignore project MCP** unless you opt in with `setting_sources` / `settingSources`.

```bash
pip install cursor-sdk python-dotenv
# CURSOR_API_KEY in .env or the environment
```

```python
from dotenv import load_dotenv
from cursor_sdk import Agent, LocalAgentOptions

load_dotenv()

with Agent.create(
    model="composer-2.5",
    local=LocalAgentOptions(
        cwd=".",  # project where you ran `agent-memory cursor install`
        setting_sources=["project"],  # required: load agent-memory from mcp.json
    ),
) as agent:
    print(agent.send("What do we already know?").wait().result)
```

TypeScript:

```ts
import { Agent } from "@cursor/sdk";

await using agent = await Agent.create({
  model: { id: "composer-2.5" },
  local: {
    cwd: process.cwd(),
    settingSources: ["project"], // required: load agent-memory from mcp.json
  },
});
```

| Piece | Local SDK |
|-------|-----------|
| MCP `memory_*` | needs `setting_sources=["project"]` (or inline `mcp_servers`) |
| Hooks auto-save | automatic when `cwd` is the installed project |

Same store as the IDE: point `cwd` at that project. Full copy lives at `.cursor/agent-memory-sdk.md` after install.

## Tools

Hooks auto-save at **turn boundaries** (your prompt, the finished answer).
That is not enough for a long mid-turn run — the agent should call
`memory_save` for durable checkpoints (milestones, findings, decisions).
Do not save every trivial step. Other tools are for judgment:

| Tool | Use it to |
|------|-----------|
| `memory_save` | Mid-turn checkpoint before the turn ends |
| `memory_summarize` | Fold finished work behind one line |
| `memory_find` | Reopen detail by meaning (Gemini), text, or id |
| `memory_write_canvas` | **Show** memory — Cursor canvas dashboard |
| `memory_branch` | Leave a dead end; restore that snapshot |
| `memory_inspect` | Look at an old snapshot (then `memory_inspect_done`) |
| `memory_state` | Agent context only (not for displaying to you) |

The live canvas (`agent-memory-live.canvas.tsx`) is refreshed by hooks on save
and when the agent calls `memory_save` / `memory_summarize`.
Click a summary to open **only** its subgraph; breadcrumbs return to Live.

A self-updating **HTML** monitor is also kept at `.agent_memory/live/index.html`.
Session start creates it if missing; hooks refresh the data on every save. Open
that file in a browser and leave the tab open — it polls for updates, so you can
watch memory without asking the agent for a canvas. Timestamps include seconds.
Paths to plots/files mentioned in an entry are clickable and **Reveal in Finder**
(a tiny local helper starts automatically when the live page is written).

## Python (OpenAI-compatible)

```python
from openai import OpenAI
from agent_memory import Memory, wrap

client = wrap(OpenAI(), Memory("."))
response = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "Why does checkout double-charge?"}],
)
```

Low-level: `memory.tools`, `memory.call(...)`, `memory.messages(...)`.

## Semantic search (Gemini)

Configured by `cursor install`. Under the hood:

```text
AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
GEMINI_API_KEY=…
```

Without a provider, `memory_find(query=...)` falls back to substring search.

## Nested git repos

Host and nested `.git/` directories are excluded automatically so `memory_summarize` /
saves are not broken by another repo inside the tree.

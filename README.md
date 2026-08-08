# Agent Memory

Long-running agents that remember — without dragging the whole chat around.

The prompt stays small. Finished work folds into one line. Detail reopens on demand. Dead ends branch away. Nothing is deleted.

Memory owns durability. The agent owns judgment.

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
- `.cursor/MEMORY_STATE.md` — live compact state
- `.agent_memory/credentials.env` — `GEMINI_API_KEY` (gitignored; loaded at runtime)

Non-interactive:

```bash
agent-memory cursor install --gemini-api-key "$GEMINI_API_KEY"
# or skip embeddings:
agent-memory cursor install --skip-gemini --no-interactive
```

Reload MCP in **Settings → Tools & MCP**. Open Agent chat.

| When | What happens |
|------|----------------|
| Session starts | MEMORY STATE is injected |
| You send a prompt | saved automatically |
| Agent needs detail | `memory_find` / `memory_history` |
| A chunk of work is done | `memory_summarize` |
| Agent finishes an answer | saved automatically |

Everything else lives in `.agent_memory/` beside the project — a separate Git store. Your repo is untouched (`.agent_memory/` is added to `.gitignore`).

Working in this repo?

```bash
pip install -e ".[cursor]"
agent-memory cursor install
```

## Tools

The agent never has to “remember to save.” Hooks handle that. Tools are for judgment:

| Tool | Use it to |
|------|-----------|
| `memory_summarize` | Fold finished work behind one line |
| `memory_find` | Reopen detail by meaning (Gemini), text, or id |
| `memory_write_canvas` | **Show** memory — live dashboard (only UI) |
| `memory_branch` | Leave a dead end; restore that snapshot |
| `memory_inspect` | Look at an old snapshot (then `memory_inspect_done`) |
| `memory_state` | Agent context only (not for displaying to you) |

The live canvas (`agent-memory-live.canvas.tsx`) is refreshed by hooks on save.
Click a summary to open **only** its subgraph; breadcrumbs return to Live.

## Python

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

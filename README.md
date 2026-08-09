# Agent Memory

Long-running agents that remember — without dragging the whole chat around.

The **prompt stays small**: only top-level commit subjects (History summaries +
Recent headlines). Full bodies stay in the store and reopen on demand.
Finished work folds behind one summary line. Dead ends branch away. Nothing
is deleted.

---

## Mental model

| Layer | What it is | Who sees it |
|-------|------------|-------------|
| **Injected MEMORY STATE** | Compact outline — commit subjects only | Model, every turn |
| **MCP tools** (8) | Judgment + mid-turn checkpoints | Model, on demand |
| **Hooks** | Auto-save prompts/answers; refresh state + HTML | Invisible durability |
| **Live HTML** | Browser monitor with full prompt packets | You |

**MCP does not paste a separate “system prompt” file.** The model learns
tools exist in **two** ways:

1. **Prompt text (we control)** — alwaysApply `.cursor/rules/agent-memory.mdc`
   + sessionStart hook instructions. This *is* in the system/rules prompt.
2. **Tool schemas (Cursor controls)** — Cursor reads `.cursor/mcp.json`,
   starts the MCP server, `list_tools`, and attaches each tool’s
   name/description/args as callables on the request.

Live HTML shows both layers when you click a user entry.

### What the model sees each turn (roughly)

```
1. alwaysApply rules
   • agent-memory.mdc          → how to use tools
   • agent-memory-state.mdc    → live MEMORY STATE (commit subjects)

2. Hook context
   • sessionStart (once)       → instructions + @agent name
   • beforeSubmitPrompt        → MEMORY STATE again (compact)

3. Your goal                  → <user_query>

4. MCP tool schemas           → available to call (not pasted as text)
```

Injected state looks like:

```text
MEMORY STATE
## History
[39d43eec] @agent Summary: clear + inject smoke test

## Recent
[597bea7a] user: Show me compact injection only

Commit subjects only. Use memory_find / memory_history for full bodies…
```

Detail → `memory_find` / `memory_history`. Do **not** dump graphs into chat.

---

## Cursor install

```bash
pip install "agent-memory[cursor] @ git+https://github.com/dorianherle/agent_memory.git@main"
cd your-project
agent-memory cursor install
```

Gemini choice is always required:

- interactive → `1` configure / `2` skip
- non-interactive → `--gemini-api-key`, `GEMINI_API_KEY`, project `.env`,
  existing credentials, or explicit `--skip-gemini`

```bash
agent-memory cursor install --gemini-api-key "$GEMINI_API_KEY"
agent-memory cursor install --skip-gemini --no-interactive
```

### Files written

| Path | Role |
|------|------|
| `.cursor/mcp.json` | MCP server + non-secret `AGENT_MEMORY_PROVIDER` |
| `.cursor/hooks.json` | Auto-save + state injection |
| `.cursor/rules/agent-memory.mdc` | Tool guide (alwaysApply) |
| `.cursor/rules/agent-memory-state.mdc` | Live MEMORY STATE (alwaysApply, **gitignored**) |
| `.cursor/MEMORY_STATE.md` | Markdown mirror of compact state (**gitignored**) |
| `.cursor/agent-memory-sdk.md` | SDK recipe (`setting_sources`) |
| `.agent_memory/` | Separate Git memory store (**gitignored**) |
| `.agent_memory/live/index.html` | Browser live monitor |
| `.agent_memory/credentials.env` | `GEMINI_API_KEY` (**gitignored**) |

Re-run install anytime; it merges safely.

This repo:

```bash
pip install -e ".[cursor]"
agent-memory cursor install
```

---

## Cursor IDE

1. Reload MCP in **Settings → Tools & MCP**
2. Open Agent chat
3. Optional: open `.agent_memory/live/index.html` in a browser (auto-refreshes)

| When | What happens |
|------|----------------|
| Session starts | Instructions + agent id; state rule + live HTML refreshed |
| You send a prompt | Saved as `@user`; compact MEMORY STATE rewritten into the prompt; HTML records the **full prompt packet** |
| Long mid-turn work | Agent should `memory_save` at milestones (hooks only fire at turn boundaries) |
| Need detail | `memory_find` / `memory_history` |
| Chunk finished | `memory_summarize` (folds Recent behind one line) |
| Agent finishes | Answer auto-saved |

---

## MCP tools (8)

Hooks cover **turn boundaries**. For long mid-turn runs, the agent should call
`memory_save` at milestones so progress survives if the session dies. Prefer a few solid checkpoints over silence; skip pure noise.

| Tool | Purpose |
|------|---------|
| `memory_save` | Mid-turn checkpoint |
| `memory_summarize` | Fold Recent behind one summary line |
| `memory_find` | Reopen detail (Gemini `query=`, substring `text=`, or `ids=`) |
| `memory_history` | Full timeline text (folded steps under summaries) |
| `memory_state` | Compact state for the agent (same as injection) |
| `memory_branch` | Leave a dead end; restore files to that snapshot |
| `memory_inspect` | Temporarily view an old snapshot |
| `memory_inspect_done` | Leave inspect mode |

There is **no** `memory_write_canvas`. The live UI is HTML only.

---

## Live HTML monitor

Path: `.agent_memory/live/index.html`

- Created on session start; refreshed on every save
- Polls for updates — leave the tab open
- Timeline shows **short titles** (no clutter)
- Click a **user** entry → aside shows:
  - **Memory instructions** (sessionStart / hook system text)
  - **Always-apply rule** (`agent-memory.mdc`)
  - **Injected MEMORY STATE** (compact subjects)
  - **User goal**
  - **Full memory packet** (all of the above, Copy)
  - Note: Cursor's base system prompt + MCP tool JSON schemas are *not*
    plain text here — tools are registered as tool defs, not pasted prompt
- Click other entries → full reply / summary body
- File paths in bodies are clickable (**Reveal in Finder**)

---

## Cursor SDK

Local SDK agents **ignore** project MCP unless you opt in:

```python
from dotenv import load_dotenv
from cursor_sdk import Agent, LocalAgentOptions

load_dotenv()

with Agent.create(
    model="composer-2.5",
    local=LocalAgentOptions(
        cwd=".",  # project with agent-memory cursor install
        setting_sources=["project"],  # required
    ),
) as agent:
    print(agent.send("What do we already know?").wait().result)
```

```ts
import { Agent } from "@cursor/sdk";

await using agent = await Agent.create({
  model: { id: "composer-2.5" },
  local: {
    cwd: process.cwd(),
    settingSources: ["project"], // required
  },
});
```

| Piece | Local SDK |
|-------|-----------|
| MCP `memory_*` | needs `setting_sources=["project"]` |
| Hooks auto-save | automatic when `cwd` is the installed project |

Same store as the IDE. Full recipe: `.cursor/agent-memory-sdk.md` after install.

---

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

`wrap` / `Memory.messages()` inject **GOAL then compact MEMORY STATE** each turn.
Low-level: `memory.tools`, `memory.call(...)`, `memory.messages(...)`.

---

## Semantic search (Gemini)

Configured by `cursor install`:

```text
AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
GEMINI_API_KEY=…
```

Without a provider, `memory_find(query=...)` falls back to substring search.

---

## Nested git repos

Host and nested `.git/` directories are excluded automatically so saves /
`memory_summarize` are not broken by another repo inside the tree.

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
| **Skill** | How to use those tools well | Model, when it matters |
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

### Or paste this to your agent

Copy the whole block into a Cursor Agent chat in the project you want memory in.

```text
Install the agent-memory package in this project so I get persistent memory
in Cursor: MCP tools, auto-save hooks, and the agent-memory skill.

1. Choose the Python that will still exist tomorrow — the project's .venv if
   there is one, otherwise create one and use it. Its path gets baked into
   .cursor/mcp.json and .cursor/hooks.json, so it has to be stable. Call it
   PY below, and prefer the unversioned path (.venv/bin/python, not
   .venv/bin/python3.13).

2. Install the package:
   PY -m pip install "agent-memory[cursor] @ git+https://github.com/dorianherle/agent_memory.git@main"

3. Ask me for a Gemini API key before you install — it turns on semantic
   memory_find. Do not skip it silently and do not decide for me.
   With a key (or if GEMINI_API_KEY is already in my env or project .env):
     PY -m agent_memory.cli cursor install --python PY --no-interactive
   Add --gemini-api-key "KEY" if you got the key from me directly.
   If I decline, use --skip-gemini instead.

4. Verify, and show me what you checked:
   - these exist: .cursor/mcp.json, .cursor/hooks.json,
     .cursor/rules/agent-memory.mdc, .cursor/skills/agent-memory/SKILL.md,
     .agent_memory/
   - the interpreter recorded in .cursor/mcp.json is PY, and it imports:
     PY -c "import agent_memory, mcp; print('ok')"
   - the hook answers instead of erroring:
     echo '{"hook_event_name":"sessionStart"}' | PY -m agent_memory.cursor.hook
   - .gitignore contains .agent_memory/

5. Never commit .agent_memory/ or anything holding the API key.

6. Then tell me the two steps only I can do: reload MCP in Cursor Settings →
   Tools & MCP, and start a fresh Agent chat so the hooks attach.

Finally, read .cursor/skills/agent-memory/SKILL.md and tell me in three lines
how you will use memory from now on.
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
| `.cursor/skills/agent-memory/` | Skill: how to use the tools well |
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

## Skill

Tool schemas say *what* each tool takes; the rule is too small to say *when*.
`cursor install` writes a skill at `.cursor/skills/agent-memory/` so the agent
does not have to be an expert — and the user does not have to instruct it.

| File | Contents |
|------|----------|
| `SKILL.md` | Operating contract, entry-writing, retrieval budget, snapshot safety |
| `playbooks.md` | Resume a dead session, archaeology, safe refactor, dead end, subagents |
| `internals.md` | Storage model, search pipeline, hook events, error strings |

It is auto-invoked: the description triggers on multi-step work, "continue" /
"why did we", risky edits, folding, subagents, and recovery. The alwaysApply
rule and the sessionStart hook both point at it, so it still bootstraps where
skills are unsupported.

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

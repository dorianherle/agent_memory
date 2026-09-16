# Agent Memory

A notebook for agents that run a long time on **one job**.

Think Ralph loops: you give a goal, the agent keeps going for hours,
and you only jump in when something is off. The chat will forget.
This notebook does not.

Every note is also a snapshot of the project files at that moment.
The agent can search what was decided, and it can go back.

---

## What the agent sees every turn

A short header. That is the whole memory prompt.

```text
## Goal
[a1b2c3d4] Why is login so slow?

## Steering
[e5f6g7h8] wait, also look at the cookie path

## Open questions
[11111111] Why is it still slow after caching? [6ca98198]
[22222222] Do we still need the extra database index?

## Recent
[6ca98198] @agent Login loads the whole users table every time
```

Every `[8 characters]` is a note id. Same shape everywhere.

The extra id after a question (`[6ca98198]` above) is not a second
question. It points at an older note the question stands on — here,
the finding in Recent. The agent opens that note with `memory_find`.

**Goal** is the original job — the thing you started the loop for.
It does not change when you type mid-run.

**Steering** is your latest course-correct (“wait, also check X”).
Optional. It does **not** replace open questions. Goal is the
mission; steering is the latest nudge; open questions are what
the agent still has to resolve.

**Open questions** are the unsolved things right now. You can have
several. Adding the same question twice does nothing. Clear one
or all with `memory_open_questions`.

**Recent** is the last 10 work headlines — findings, not every
user prompt or auto-saved answer. Finished work leaves this view.
The agent opens it with `memory_find` or `memory_history`.

Watch it live: `.agent_memory/live/index.html`.

---

## The tools

Hooks already save your first prompt and each finished answer.
The agent writes the rest.

| Tool | In one line |
|------|-------------|
| `memory_save` | Write a finding, a decision, or a checkpoint |
| `memory_open_questions` | Add or close unsolved questions |
| `memory_summarize` | Hide a finished chunk behind one line |
| `memory_delete` | Hide a bad or tiny note |
| `memory_find` | Open a note (by id, words, or meaning) |
| `memory_history` | The whole timeline of this line of work |
| `memory_state` | The same compact view already in the prompt |
| `memory_branch` | Summarize current work and restore a requested snapshot |
| `memory_inspect` | Peek at an old snapshot — **rewinds the files** |
| `memory_inspect_done` | Put the files back |

`memory_branch` and `memory_inspect` change the working files.
Everything else only writes to the notebook.

---

## Working with memory

Run tests with normal tools. Save the useful result, relevant files, and next
step with `memory_save`. Memory stores context; the agent decides how to use it.

Use `memory_branch(id, name, label)` when you want to restore a specific snapshot.
The label summarizes the work being left behind. That history stays searchable.

Generated memory-state files are excluded from snapshots. The SDK wrapper replaces
its state packet before each model request, including requests inside tool loops.

---

## Cursor install

```bash
pip install "agent-memory[cursor] @ git+https://github.com/dorianherle/agent_memory.git@main"
cd your-project
agent-memory cursor install
```

### Or paste this to your agent

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

3. Ask me for a Gemini API key before you install — it turns on
   meaning-based memory_find. Notes and literal lookup work without it.
   Do not skip it silently and do not decide for me.
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

Gemini is always a choice: interactive `1` / `2`, or
`--gemini-api-key`, `GEMINI_API_KEY`, project `.env`, or `--skip-gemini`.

```bash
agent-memory cursor install --gemini-api-key "$GEMINI_API_KEY"
agent-memory cursor install --skip-gemini --no-interactive
```

This repo:

```bash
pip install -e ".[cursor]"
agent-memory cursor install
```

Then: **Settings → Tools & MCP → reload**, start a new Agent chat.
Optional: open `.agent_memory/live/index.html` (it refreshes itself).

Re-run install anytime; it merges safely.

### What install writes

| Path | What it is |
|------|------------|
| `.cursor/mcp.json` | The memory tools |
| `.cursor/hooks.json` | Auto-save + refresh |
| `.cursor/rules/agent-memory.mdc` | Short always-on guide |
| `.cursor/skills/agent-memory/` | How the agent should use memory |
| `.cursor/rules/agent-memory-state.mdc` | Live notebook view (**gitignored**) |
| `.agent_memory/` | The notebook itself (**gitignored**) |
| `.agent_memory/live/index.html` | Browser view |
| `.agent_memory/credentials.env` | Gemini key, if you set one (**gitignored**) |

---

## Live view

`.agent_memory/live/index.html` — created on session start, updated on
every save. Leave the tab open.

Click a user prompt to see what the model was given. Click any other
note for the full text. File paths open in Finder.

---

## Skill

Install writes `.cursor/skills/agent-memory/`. That is the agent's
manual: when to save, how to write a headline, when to set open questions, when not to rewind files.

- [SKILL.md](.cursor/skills/agent-memory/SKILL.md) — the manual
- [playbooks.md](.cursor/skills/agent-memory/playbooks.md) — worked examples
- [internals.md](.cursor/skills/agent-memory/internals.md) — mechanics

---

## Cursor SDK

Local SDK agents ignore project MCP unless you opt in:

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

Same notebook as the IDE. Full recipe: `.cursor/agent-memory-sdk.md`
after install.

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

`wrap` injects the compact notebook view each turn.

---

## Semantic search (Gemini)

One optional key, one job: `memory_find(query=…)` finds notes by
meaning instead of exact words. Notes and literal lookup work without it.

Without a key, `memory_find` falls back to literal substring search
(and says so). Everything else works unchanged.

---

## Nested git repos

Host and nested `.git/` folders are skipped so a repo-inside-a-repo
does not break saves.

## Tests

```sh
.venv/bin/python -m pytest agent_memory/tests -q
```

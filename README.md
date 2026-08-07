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

Reload MCP in **Settings → Tools & MCP**. Open Agent chat.

| When | What happens |
|------|----------------|
| Session starts | MEMORY STATE is injected |
| You send a prompt | saved automatically |
| Agent needs detail | `memory_find` / `memory_history` |
| A chunk of work is done | `memory_summarize` |
| Agent finishes an answer | saved automatically |

`cursor install` merges into your project (safe if `.cursor/` already exists):

- `.cursor/mcp.json` — judgment tools
- `.cursor/hooks.json` — automatic saves
- `.cursor/rules/agent-memory.mdc` — how the agent should use it
- `.cursor/MEMORY_STATE.md` — live compact state

Everything is stored in `.agent_memory/` beside the project — a separate Git store. Your repo is untouched.

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
| `memory_find` | Reopen detail by meaning, text, or id |
| `memory_history` | Full timeline of the current branch |
| `memory_graph` | Branch tree (`html=true` writes a page) |
| `memory_graph_data` | Same graph as JSON `{nodes, edges, kinds, branches}` |
| `memory_write_canvas` | Write a live `.canvas.tsx` graph instantly (no model rewrite) |
| `memory_branch` | Leave a dead end; restore that snapshot |
| `memory_inspect` | Look at an old snapshot (then `memory_inspect_done`) |
| `memory_state` | Refresh MEMORY STATE |

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

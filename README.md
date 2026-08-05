# Agent Memory

Git-backed memory for agents. The model does not keep a chat transcript.
It only sees the current memory state, then calls tools to change it.

```text
work  →  save  →  fold finished steps into a summary  →  find detail later
```

## Install

```bash
pip install "agent-memory @ git+https://github.com/dorianherle/agent_memory.git@main"
```

```python
from agent_memory import Memory

memory = Memory(".")
```

This creates `.agent_memory/` next to your project. It is a separate Git
repo for agent memory and does not touch the project's own Git history.

## How it works

| Piece | Role |
|---|---|
| `Memory` | Stores immutable entries with `save`, `find`, `branch`, `state`. |
| `MEMORY_INSTRUCTIONS` | System prompt: when to call each tool. |
| `TOOLS` | OpenAI function-calling schemas for `save`, `find`, `branch`. |
| `call_tool(...)` | Runs one tool call and returns `(memory, observation)`. |
| `render(state)` | Turns `memory.state()` into the MEMORY STATE block. |

`state` is not a tool. Every prompt already includes it.

```python
from agent_memory.prompt import MEMORY_INSTRUCTIONS, render
from agent_memory.tools import TOOLS, call_tool
```

### Folding detail away

Each save shows up in Recent:

```text
## Recent
[a1b2c3d4] Read checkout/api.py
[e5f6a7b8] Read payment retry docs
```

When that piece of work is done, save one sentence with `summary=True`:

```python
memory.save("Checkout retries payments, so it needs an idempotency key.", summary=True)
```

Everything in Recent is folded behind it. No ids, no tags, no lists:

```text
## History
[9c8d7e6f] Checkout retries payments, so it needs an idempotency key.

## Recent
```

The folded steps are still stored. `find(ids="9c8d7e6f")` returns the summary
and the steps behind it.

## Use with the OpenAI API

Pass `TOOLS` to Chat Completions. Rebuild the user prompt every turn from
GOAL, CONSTRAINTS, MEMORY STATE, and the last tool observation. When the
model returns plain text instead of a tool call, it is done.

```python
import json
from openai import OpenAI
from agent_memory import Memory
from agent_memory.prompt import MEMORY_INSTRUCTIONS, render
from agent_memory.tools import TOOLS, call_tool

client = OpenAI()
memory = Memory(".")

goal = "Find why checkout orders are occasionally charged twice."
constraints = "- Do not change production code."
observation = "Nothing yet."

while True:
    prompt = f"""
GOAL
{goal}

CONSTRAINTS
{constraints}

MEMORY STATE
{render(memory.state())}

OBSERVATION
{observation}
""".strip()

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {"role": "system", "content": MEMORY_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        tools=TOOLS,
    )
    message = response.choices[0].message

    if not message.tool_calls:
        print(message.content)
        break

    call = message.tool_calls[0]
    memory, observation = call_tool(
        memory,
        call.function.name,
        json.loads(call.function.arguments),
    )
```

Works with any OpenAI-compatible endpoint. Add your own task tools next to
`TOOLS` (read a file, run a command) and save each result.

## Use with the OpenAI Agents SDK

Install the SDK, then wrap the same memory methods as `@tool`s.
Put `Memory` on the run context and rebuild MEMORY STATE in dynamic
instructions so every model turn sees the latest compact state.

```bash
pip install openai-agents
```

```python
from dataclasses import dataclass
from agents import Agent, Runner, RunContextWrapper
from agents.decorators import tool
from agent_memory import Memory
from agent_memory.prompt import MEMORY_INSTRUCTIONS, render
from agent_memory.tools import call_tool


@dataclass
class Ctx:
    memory: Memory


def instructions(ctx: RunContextWrapper[Ctx], agent: Agent[Ctx]) -> str:
    state = render(ctx.context.memory.state())
    return f"{MEMORY_INSTRUCTIONS}\n\nMEMORY STATE\n{state}"


@tool
def save(ctx: RunContextWrapper[Ctx], text: str, summary: bool = False) -> str:
    """Store a step. Set summary to true to fold Recent behind this text."""
    memory, result = call_tool(
        ctx.context.memory,
        "save",
        {"text": text, "summary": summary},
    )
    ctx.context.memory = memory
    return result


@tool
def find(
    ctx: RunContextWrapper[Ctx],
    ids: list[str] | None = None,
    text: str | None = None,
    query: str | None = None,
) -> str:
    """Read stored entries, including the steps folded behind a summary."""
    memory, result = call_tool(
        ctx.context.memory,
        "find",
        {"ids": ids, "text": text, "query": query},
    )
    ctx.context.memory = memory
    return result


@tool
def branch(ctx: RunContextWrapper[Ctx], id: str, name: str) -> str:
    """Go back to an entry on a new branch when earlier work proved wrong."""
    memory, result = call_tool(
        ctx.context.memory,
        "branch",
        {"id": id, "name": name},
    )
    ctx.context.memory = memory
    return result


agent = Agent[Ctx](
    name="Memory agent",
    instructions=instructions,
    tools=[save, find, branch],
)

result = Runner.run_sync(
    agent,
    """
GOAL
Find why checkout orders are occasionally charged twice.

CONSTRAINTS
- Do not change production code.
- Quote exact source lines for the root cause.
""".strip(),
    context=Ctx(memory=Memory(".")),
)
print(result.final_output)
```

The Agents SDK owns the tool loop. You own the memory: keep it on the
context, refresh MEMORY STATE in instructions, and let `call_tool` update
it when `branch` returns a new `Memory`.

## Mini API

| Method | What it does |
|---|---|
| `Memory(path)` | Open or create memory in `path/.agent_memory/`. |
| `save(message, payload=None, allow_empty=False, summary=False)` | Save an entry. `summary=True` folds Recent behind it. `allow_empty=True` saves with no file changes. |
| `state()` | Current compact state. |
| `find(query=None, ids=None, text=None, payload=None, k=10)` | Find by meaning, id, text, or payload. Short ids work. |
| `branch(base_id, name)` | Start a new branch from an earlier entry. |
| `revisit(id)` | Temporarily restore project files at that entry. |
| `supersede(old_id, message)` | Replace a conclusion, keep history. |
| `delete(item_id)` | Hide from normal search, keep history. |
| `clear(archive=True)` | Start fresh; archive the old store by default. |

Host helpers:

| Helper | What it does |
|---|---|
| `MEMORY_INSTRUCTIONS` | System prompt for when to call tools. |
| `render(state)` | Format state for the prompt. |
| `TOOLS` | OpenAI tool schemas. |
| `call_tool(memory, name, arguments)` | Execute one tool call. |

## Semantic search

```bash
pip install "agent-memory[gemini]"
export GEMINI_API_KEY="..."
export AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
```

```python
memory.find("Why could a customer be charged twice?", k=6)
```

Any provider works if it exposes `fingerprint`, `embed_documents`, and
`embed_query`. Point `AGENT_MEMORY_PROVIDER` at `module:Class`.

## Test

```bash
python -m unittest discover -s agent_memory/tests
```

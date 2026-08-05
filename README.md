# Agent Memory

Your agent remembers without carrying its whole chat history.

It saves what it learns, folds finished work into one line, reopens the
detail when it is needed again, and branches back past dead ends. The
prompt stays small. Nothing is deleted.

```text
do something            →  save
finish a meaningful bit →  summarize
need a detail again     →  find
dead end, go back       →  branch
```

## Install

```bash
pip install "agent-memory @ git+https://github.com/dorianherle/agent_memory.git@main"
```

## Quick start

One `wrap(...)` call. Everything else is normal SDK code.

### OpenAI

```python
from openai import OpenAI
from agent_memory import Memory, wrap

memory = Memory(".")
client = wrap(OpenAI(), memory)

response = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "Why does checkout double-charge?"}],
)
print(response.choices[0].message.content)
```

The wrapper only acts at the two boundaries of a request. Before it
goes out: inject the memory instructions, the current MEMORY STATE and
the `save` / `find` / `branch` tools. When the model answers: a memory
tool call is executed against the store and the next request fires
silently; text or one of your own tool calls is returned to you
untouched. One `create()` can therefore make several API requests —
that is the agent working.

Streaming passes through untouched. If the stream ends in a memory
call, the wrapper executes it and the next request streams through the
same iterator:

```python
stream = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "I moved to Neuchatel. Remember this."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

### Cursor SDK

Cursor owns the tool loop, so the wrapper acts at the two places you
talk to Cursor instead: `Agent.create` registers the memory tools, and
every `agent.send` gets MEMORY STATE prepended before Cursor sees the
prompt.

```python
from cursor_sdk import Agent
from agent_memory import Memory, wrap

memory = Memory(".")

with wrap(Agent, memory, model="composer-2.5", cwd=".") as agent:
    agent.send("Why does checkout double-charge?").wait()
```

Everything else (`model=`, `cwd=`, `api_key=`, your own
`custom_tools=`, ...) passes through to `Agent.create` unchanged.

Already have an agent, for example from `Agent.resume`? Wrapping it
adds the send-side injection; register the tools yourself with
`agent_memory.wrappers.memory_custom_tools(memory)`:

```python
agent = wrap(existing_agent, memory)
agent.send("...")  # MEMORY STATE is prepended here
```

- `Memory(".")` creates `.agent_memory/` next to your project. It is a
  separate Git store; your repo stays untouched.
- Next session, open `Memory(".")` again — everything is still there.
  You never reload an old transcript.
- Your own `tools=` / `custom_tools` still work. Memory calls are
  handled silently.
- OpenAI: sync client only for now — `AsyncOpenAI` is not wrapped yet.

## How memory works

The model never sees a transcript. Every request carries a compact
MEMORY STATE, and the model manages it with three tools.

**save** — every meaningful step becomes an entry in Recent:

```text
## Recent
[a1b2c3d4] Read checkout/api.py
[e5f6a7b8] Read payment retry docs
```

**save with summary** — a finished piece of work folds into one line.
History grows by one, Recent empties:

```text
## History
[9c8d7e6f] Checkout retries payments, so it needs an idempotency key.

## Recent
```

**find** — the folded steps are not gone. Anything ever saved can be
looked up again by id, text match, saved metadata, or meaning (with an
embedding provider).

**branch** — a past conclusion proved wrong. Instead of rewriting
history, the model goes back to an earlier entry on a new branch. The
dead end stays on the old branch:

```text
main
  [a1b2c3d4] Read checkout/api.py
  [e5f6a7b8] Read payment retry docs
  [9c8d7e6f] Conclusion: retries need an idempotency key.   ← wrong
  [11223344] Tried a fix based on that.                     ← dead end

branch from a1b2c3d4 → retry-without-key
  [a1b2c3d4] Read checkout/api.py
  Recent continues from here
```

Every entry is a Git commit in `.agent_memory/`. That is where the ids
come from, why nothing is ever lost, and why branching is free. A save
also snapshots your working files, so `memory.revisit(id)` can restore
how everything looked at that moment.

## Bring your own loop

The wrapper is nothing but this loop. If you already own an agent loop,
use the three pieces directly:

| Piece | Role |
|---|---|
| `memory` | the store on disk |
| `MEMORY_TOOLS` | OpenAI tool schemas for `save` / `find` / `branch` |
| `call_tool(memory, name, args)` | runs one tool call, returns the result string |

```python
import json
from openai import OpenAI
from agent_memory import Memory, MEMORY_TOOLS, call_tool

client = OpenAI()
memory = Memory(".")

# a plain [system, user] list: memory instructions + prompt + MEMORY STATE
messages = memory.messages("Why does checkout double-charge?")

while True:
    response = client.chat.completions.create(
        model="gpt-5",
        messages=messages,
        tools=MEMORY_TOOLS,  # + your own tools
    )
    message = response.choices[0].message

    if not message.tool_calls:
        print(message.content)
        break

    messages.append(message)
    for call in message.tool_calls:
        result = call_tool(memory, call.function.name, json.loads(call.function.arguments))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
```

`memory.messages(prompt)` is only a helper that builds the first two
chat dicts. It returns a normal Python list; from there `append` is
plain `list.append`, and the store is only touched when the model calls
a tool.

### Compact mode

The loop above grows `messages` within the session, like any chatbot.
For the smallest possible request, do not append — rebuild the two
messages from the store every turn:

```python
prompt = "Why does checkout double-charge?"

while True:
    response = client.chat.completions.create(
        model="gpt-5",
        messages=memory.messages(prompt),  # fresh two messages every turn
        tools=MEMORY_TOOLS,
    )
    message = response.choices[0].message
    if not message.tool_calls:
        break
    call = message.tool_calls[0]
    call_tool(memory, call.function.name, json.loads(call.function.arguments))
```

Each request then holds only the instructions, your prompt, MEMORY STATE
and the last tool result. No transcript at all. Folding Recent into a
summary visibly shrinks the next request.

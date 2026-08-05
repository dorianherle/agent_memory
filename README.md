# Agent Memory

Your agent remembers without carrying its whole chat history.

It saves what it learns, folds finished work into one line, reopens the
detail when it is needed again, and branches back past dead ends. The
prompt stays small. Nothing is deleted.

```text
do something            →  save
finish a meaningful bit →  summarize
need a detail again     →  find
look at an old snapshot →  inspect
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
goes out: record your prompt as a `user:` entry, then inject the memory
instructions, the current MEMORY STATE and the memory tools. When the
model answers: a memory tool call is executed against the store and the
next request fires silently; text or one of your own tool calls is
returned to you untouched. One `create()` can therefore make several
API requests — that is the agent working.

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
every `agent.send` records your prompt as a `user:` entry and gets
MEMORY STATE prepended before Cursor sees the prompt.

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
MEMORY STATE, and the model manages it with five tools. Your own words
are part of it too: every prompt is recorded automatically as a
`user:` entry, so intent is never lost.

**save** — every meaningful step becomes an entry in Recent:

```text
## Recent
[3f2e1d0c] user: why does checkout double-charge?
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
embedding provider). An explicit id is found even if it lives on
another branch, and `all_branches` widens any search to everything.

**inspect** — look at the past without moving. Every working file is
set to exactly how it was at an entry; `inspect_done` checks the
branch and its files back out. The window survives crashes: a marker
on disk records the way home, and the next `Memory(...)` restores it.

**branch** — move to any entry, back past a dead end or forward to
another branch. Leaving a branch first caps it with one summary
commit, and that node is what the Branches section shows. Working
files are restored to the target entry's snapshot:

```text
main
  [a1b2c3d4] Read checkout/api.py
  [e5f6a7b8] Read payment retry docs
  [9c8d7e6f] Conclusion: retries need an idempotency key.   ← wrong
  [11223344] Summary: tried the key fix, dead end.          ← the cap

branch from a1b2c3d4 → retry-without-key
  [a1b2c3d4] Read checkout/api.py
  Recent continues from here

## Branches
[11223344] main: tried the key fix, dead end.
* retry-without-key (you are here)
```

Branching to `[11223344]` later resumes main, files included. Nothing
is rewritten and nothing is deleted.

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
| `MEMORY_TOOLS` | OpenAI tool schemas for the five memory tools |
| `call_tool(memory, name, args)` | runs one tool call, returns the result string (errors included, never raised) |

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

`memory.messages(prompt)` builds the first two chat dicts and records
the prompt as a `user:` entry (an unchanged, resent prompt is skipped).
It returns a normal Python list; from there `append` is plain
`list.append`.

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

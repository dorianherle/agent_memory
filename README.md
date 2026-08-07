# Agent Memory

Your agent remembers without carrying its whole chat history.

It saves what it learns, folds finished work into one line, reopens
detail when needed, and branches past dead ends. The prompt stays small.
Nothing is deleted.

## Install

```bash
pip install "agent-memory @ git+https://github.com/dorianherle/agent_memory.git@main"
```

## Quick start

Import `Memory`. Pass its tool to your agent. You own the loop.

```python
import json
from openai import OpenAI
from agent_memory import Memory

client = OpenAI()
memory = Memory(".")

messages = memory.messages("Why does checkout double-charge?")

while True:
    response = client.chat.completions.create(
        model="gpt-5",
        messages=messages,
        tools=memory.tools,  # + your own tools
    )
    message = response.choices[0].message

    if not message.tool_calls:
        print(message.content)
        break

    messages.append(message)
    for call in message.tool_calls:
        result = memory.call(
            call.function.name, json.loads(call.function.arguments)
        )
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
```

`Memory(".")` stores everything in `.agent_memory/` next to your project
(a separate Git store; your repo stays untouched). Open it again next
session — it's still there.

## Actions

The model never sees a transcript. Every request carries MEMORY STATE.
One tool, one `action`:

```text
do something            →  memory(action=save)
finish a meaningful bit →  memory(action=save, summary=true)
need a detail again     →  memory(action=find)
look at an old snapshot →  memory(action=inspect)
dead end, go back       →  memory(action=branch)
```

Also: `history` (full timeline), `graph` (branch tree; `html=true` writes
an explorable page), `inspect_done` (leave inspect).

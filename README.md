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

Wrap your OpenAI client. Everything else is normal OpenAI code.

```python
from openai import OpenAI
from agent_memory import Memory, wrap_client

memory = Memory(".")
client = wrap_client(OpenAI(), memory)

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

- `Memory(".")` creates `.agent_memory/` next to your project. It is a
  separate Git store; your repo stays untouched.
- Next session, open `Memory(".")` again — everything is still there.
  You never reload an old transcript.
- Your own `tools=` still work. Memory calls are handled silently; a
  response that calls your tools is returned to you as usual.
- Sync client only for now — `AsyncOpenAI` is not wrapped yet.

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
looked up again by id, text match, saved metadata, or meaning (see
Semantic search).

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

## Other SDKs

### Cursor SDK

Cursor runs the loop. Register the three tools as custom tools — the
schemas come from `MEMORY_TOOLS`, each `execute` writes to the store:

```python
from cursor_sdk import Agent, LocalAgentOptions, CustomTool
from agent_memory import Memory, MEMORY_TOOLS, call_tool

memory = Memory(".")

def run(name):
    def execute(**args):
        return call_tool(memory, name, args)
    return execute

custom_tools = {}
for tool in MEMORY_TOOLS:
    spec = tool["function"]
    custom_tools[spec["name"]] = CustomTool(
        description=spec["description"],
        input_schema=spec["parameters"],
        execute=run(spec["name"]),
    )

with Agent.create(
    model="composer-2.5",
    local=LocalAgentOptions(cwd=".", custom_tools=custom_tools),
) as agent:
    system, user = memory.messages("Investigate why checkout double-charges.")
    agent.send(f"{system['content']}\n\n{user['content']}")
```

### OpenAI Agents SDK

Wrap `call_tool` as function tools:

```python
from agents import Agent, function_tool
from agent_memory import Memory, call_tool

memory = Memory(".")

@function_tool
def save(text: str, payload: dict | None = None, summary: bool = False) -> str:
    """Store a step. summary=True folds Recent."""
    return call_tool(memory, "save", {"text": text, "payload": payload, "summary": summary})

@function_tool
def find(ids: list[str] | None = None, text: str | None = None,
         payload: dict | None = None, query: str | None = None) -> str:
    """Look up stored entries."""
    return call_tool(memory, "find", {"ids": ids, "text": text, "payload": payload, "query": query})

@function_tool
def branch(id: str, name: str) -> str:
    """Go back to an entry on a new branch."""
    return call_tool(memory, "branch", {"id": id, "name": name})

system, user = memory.messages("You investigate checkout bugs.")
agent = Agent(
    name="Researcher",
    instructions=f"{system['content']}\n\n{user['content']}",
    tools=[save, find, branch],
)
```

## API

| Call | Does |
|---|---|
| `Memory(path, provider=None)` | open or create the store |
| `memory.save(text, payload=None, summary=False)` | remember a step; `summary=True` folds Recent |
| `memory.find(query=None, ids=..., text=..., payload=...)` | look something up |
| `memory.branch(id, name)` | start a new branch from an earlier entry |
| `memory.state()` | current entries (History + Recent) |
| `memory.messages(prompt)` | plain `[system, user]` list for one turn |
| `memory.revisit(id)` | temporarily restore files as they were at that entry |
| `memory.clear()` | archive the store and start fresh |
| `MEMORY_TOOLS` | OpenAI tool schemas for `save` / `find` / `branch` |
| `call_tool(memory, name, args)` | run one tool call, return the result string |
| `wrap_client(client, memory)` | drop-in client with the memory loop built in |

`payload` is optional metadata on a save — a plain dict you can filter
on later: `memory.find(payload={"file": "docs/retries.md"})`.

## Semantic search

Give `Memory` an embedding provider and `find` can search by meaning:

```python
memory.find("why could a customer be charged twice?", k=6)
```

Without a provider, `find` still works by id / text / payload.

Any OpenAI-compatible `/v1/embeddings` endpoint (OpenAI, vLLM, Ollama, ...):

```python
from agent_memory.providers.openai_compatible import OpenAICompatibleProvider

memory = Memory(".", provider=OpenAICompatibleProvider(
    base_url="https://api.openai.com/v1",
    model="text-embedding-3-small",
    api_key="...",
))
```

Gemini:

```bash
pip install "agent-memory[gemini]"
export GEMINI_API_KEY="..."
```

```python
from agent_memory.providers.gemini import GeminiProvider

memory = Memory(".", provider=GeminiProvider())
```

Your own — any object with these three members:

```python
class MyProvider:
    fingerprint = "my-model:1536"  # change when the model or vector size changes

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
```

Or set it through the environment, without touching code:

```bash
export AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
```

## Test

```bash
python -m unittest discover -s agent_memory/tests
```

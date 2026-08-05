# Agent Memory

Minimal Git-backed memory for AI agents.

```bash
pip install agent-memory
```

```python
from agent_memory import Memory

memory = Memory(".")

item_id = memory.save(
    "Updated matrix_math.py",
    payload={"type": "action", "files_changed": 1},
)

items = memory.find(ids=item_id)
items = memory.find(text="matrix")
items = memory.find(payload={"type": "action"})
items = memory.find(since=items[0].datetime, k=10)

replacement_id = memory.supersede(item_id, "Corrected matrix implementation")
memory.delete(replacement_id)

with memory.revisit(id=item_id):
    print(open("matrix_math.py").read())

feature = memory.branch(base_id=item_id, name="fix-bug")

memory.clear()
memory.clear(archive=False)
```

`find()` searches the current branch and returns at most 10 items by default.
Each `MemoryItem` has `id`, `message`, `payload`, a timezone-aware `datetime`,
and an optional semantic `score`. Pass `branch=None` to search every branch or
`include_historical=True` to include deleted and superseded memories.

## Execution state

When an agent finishes a subtask, save a one-line summary that covers the
raw steps. `state()` returns the whole current branch oldest to newest, with
covered items folded behind their summary. Nothing is deleted; hiding happens
only at read time, and covered steps stay reachable via `find(ids=...)` and
`revisit()`.

```python
a = memory.save("opened login page")
b = memory.save("created project")
summary = memory.save("Set up account and project", covers=[a, b])

memory.state()  # [summary]

deeper = memory.save("Finished setup phase", covers=[summary])
```

Summaries covering summaries build higher abstraction levels with the same
verb. `agent_memory.prompt` renders the state for an agent and ships the
matching system prompt:

```python
from agent_memory.prompt import SYSTEM_PROMPT, render

context = render(memory.state())
```

The agent-facing tools compose from existing verbs:

| agent tool | implementation |
|---|---|
| summary | `save(text, covers=[ids])` |
| investigate(id) | `find(ids=item.payload["covers"])` |
| revise(id, reason) | `branch(id, name)` then `save(reason, {"tag": "hint"})` |

## Semantic search

Install or write an embedding provider, then set:

```bash
export AGENT_MEMORY_PROVIDER=my_package.embeddings:MyProvider
```

The provider contract is:

```python
class MyProvider:
    fingerprint = "model-name:dimensions"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...
```

Then use:

```python
memory.find(
    "attempts to optimize matrix multiplication",
    k=6,
    max_tokens=4000,
)
```

Gemini is optional:

```bash
pip install "agent-memory[gemini]"
export GEMINI_API_KEY="..."
export AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
```

Git stores the immutable memory history. SQLite stores its incremental metadata
projection, FTS index, document embeddings, and query embeddings. HNSW performs
bounded semantic search. A document or query is embedded only once for each
provider fingerprint. A missing HNSW file is rebuilt from cached vectors without
calling the embedding provider.

## Tests

```bash
python -m unittest discover -s agent_memory/tests
```

# Agent Memory

Your agent can remember without carrying its entire chat history.

Agent Memory is a tiny, Git-backed notebook for agents. Save what happened,
fold old details into short summaries, and retrieve the original facts when
they matter again.

```text
Read 12 files  →  save one summary  →  keep the prompt small
                                      →  recover any original detail later
```

## Install

Install directly from this repository:

```bash
pip install "agent-memory @ git+https://github.com/dorianherle/agent_memory.git@main"
```

Then create memory in the project your agent works in:

```python
from agent_memory import Memory

memory = Memory(".")
```

This creates `.agent_memory/` in that project. It is a separate Git repository
that records the agent's memory without changing the project's Git history.

## The idea

Every `save()` creates an immutable memory item. An item can be a file read, a
decision, a tool result, or a short summary of earlier items.

```python
read_api = memory.save("Read checkout/api.py")
read_docs = memory.save("Read the payment retry documentation")

summary = memory.save(
    "The checkout endpoint retries requests, so payment needs an idempotency key.",
    covers=[read_api, read_docs],
)
```

`memory.state()` now shows the summary instead of the two raw steps. The
original steps still exist and can always be found by ID.

```python
for item in memory.state():
    print(item.message)

# The checkout endpoint retries requests, so payment needs an idempotency key.
```

This is useful when your agent's context is getting large: it keeps the
important conclusion visible and hides the supporting detail until it is needed.

## Everyday use

```python
# Save something the agent learned.
item_id = memory.save(
    "Found a duplicate payment caused by retries",
    payload={"area": "checkout", "severity": "high"},
)

# Look up an exact memory or filter the current branch.
memory.find(ids=item_id)
memory.find(text="payment")
memory.find(payload={"area": "checkout"})

# Replace a wrong conclusion without losing the old one.
memory.supersede(item_id, "The duplicate payment is caused by a missing idempotency key")

# Hide a memory from normal searches without deleting its history.
memory.delete(item_id)
```

`find()` searches the current branch and returns up to 10 items by default.
Each returned `MemoryItem` has an `id`, `message`, `payload`, `datetime`, and
an optional semantic-search `score`.

Pass `include_historical=True` to include deleted and superseded memories, or
`branch=None` to search every memory branch.

## Give the agent a compact state

`agent_memory.prompt` turns the current state into a prompt-ready block and
includes a system prompt that tells the agent how to summarize and investigate.

```python
from agent_memory.prompt import SYSTEM_PROMPT, render

context = render(memory.state())
prompt = f"{SYSTEM_PROMPT}\n\n{context}"
```

## Copy-paste agent prompt

Use this as the prompt you send on every agent turn. Replace `goal` and
`constraints` with the user's request, and rebuild it after every memory action.

```python
from agent_memory import Memory
from agent_memory.prompt import SYSTEM_PROMPT, render

memory = Memory(".")

goal = "Find why checkout orders are occasionally charged twice."
constraints = """
- Do not change production code.
- Keep the final answer under 200 words.
- Quote exact source lines for the root cause.
""".strip()

prompt = f"""
GOAL
{goal}

CONSTRAINTS
{constraints}

MEMORY STATE
{render(memory.state())}

MEMORY INSTRUCTIONS
{SYSTEM_PROMPT}
""".strip()
```

Give the model tools that call `memory.save()`, `memory.find()`, and
`memory.branch()`. After it reads a file or reaches a conclusion, save that
step. When Recent gets long, save a one-sentence summary with `covers=[ids]`.
On the next turn, build the prompt again. The model sees a compact state rather
than a growing chat transcript.

When the agent needs detail behind a summary, inspect the IDs in its `covers`
payload:

```python
summary = memory.state()[0]
source_items = memory.find(ids=summary.payload["covers"])
```

## Change your mind safely

Use branches when new evidence proves an earlier direction wrong:

```python
feature_memory = memory.branch(
    base_id=summary.id,
    name="missing-idempotency-key",
)
feature_memory.save("Confirmed the payment provider accepts idempotency keys")
```

The original branch remains intact. To inspect exactly what the project looked
like at a memory item, use `revisit()`:

```python
with memory.revisit(item_id):
    print(open("checkout/api.py").read())
```

## Semantic search

For meaning-based search, configure an embedding provider:

```bash
pip install "agent-memory[gemini]"
export GEMINI_API_KEY="..."
export AGENT_MEMORY_PROVIDER=agent_memory.providers.gemini:GeminiProvider
```

Then search naturally:

```python
results = memory.find(
    "Why could a customer be charged twice?",
    k=6,
    max_tokens=4000,
)
```

You can bring your own provider too:

```python
class MyProvider:
    fingerprint = "model-name:dimensions"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...
```

Set `AGENT_MEMORY_PROVIDER` to its import path, for example:

```bash
export AGENT_MEMORY_PROVIDER=my_package.embeddings:MyProvider
```

## See it work

`examples/debug_agent.py` gives a live agent a checkout incident to solve.
It has no chat history; its memory is rebuilt from `memory.state()` every turn.

```bash
export OPENROUTER_API_KEY="..."
python examples/debug_agent.py
```

The agent reads files, summarizes them to shrink its prompt, investigates old
summaries for exact source text, and branches if its first hypothesis is wrong.

## Test

```bash
python -m unittest discover -s agent_memory/tests
```

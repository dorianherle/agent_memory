# Agent Memory

**Deterministic Bounded State Packets, Git-Snapshot Lineage, and Consecutive Compression for Long-Horizon AI Agents**

**Author:** **Dorian Herle** (`herle.dorian@gmail.com`)  
**Paper:** [📄 Read the Paper (PDF)](paper/agent_memory_paper.pdf) · [📖 Read Online (Markdown)](paper/PAPER.md) · [🧾 LaTeX Source](paper/agent_memory_paper.tex)  
**Benchmark Artifacts:** [`benchmarks/memoryarena/`](benchmarks/memoryarena/) · [`examples/memoryarena_online_benchmark.py`](examples/memoryarena_online_benchmark.py)

---

A local, zero-LLM-overhead memory system for agents that run a long time on **one job**.

Think Ralph loops or multi-session scientific derivations: you give a goal, the agent works across dozens of fresh sessions, and you only jump in when something is off. Long-context chat buffers rot and explode in token cost; 1D fact extractors (Mem0) paraphrase away exact constants; fixed-window RAG (BM25/vector chunks) slices multi-line equations and code definitions in half.

**Agent Memory** does not. Every note is committed to a private Git DAG alongside a SHA-256 manifest of the project files at that moment. Before every turn, a deterministic compiler (`render_packet`) injects a bounded **$\le 1,200$-character ($\sim 200$-token) `MEMORY STATE` packet** with zero LLM calls, while single-call symbol-aware retrieval (`memory_find(query=..., full=True)`) and consecutive compression (`memory_summarize` and executable `memory_model` rules) preserve **100.0% symbolic recall** across deep multi-session chains.

---

## MemoryArena (ICML 2026) Benchmark Results

Evaluated on the official [**ICML 2026 MemoryArena (`formal_reasoning_phys`)**](https://huggingface.co/datasets/ZexueHe/memoryarena) benchmark (**all 20 multi-session theoretical physics papers, 86 interdependent subtasks, chain depths up to $k=12$**). Full per-paper `result.jsonl` logs and official `eval.py` `all_results.json` summaries are in [`benchmarks/memoryarena/official_results/phys/`](benchmarks/memoryarena/official_results/phys/).

### 1. Official Backbone Evaluation (`gpt-5-mini` Solver + `MathEnvironment.judge()`)

Using the exact official [`ZexueHe/MemoryArena`](https://github.com/ZexueHe/MemoryArena) configuration (`openai/gpt-5-mini` solver at `temperature=0.0` + `MathEnvironment.judge()` LLM equivalence judge across all 20 papers):

| System | Category | Success Rate (SR) | Progress Score (PS) | Avg Memory Tokens |
| :--- | :--- | :---: | :---: | :---: |
| **Agent Memory (`memory_find(query=q, full=True)`)** | **Consecutive + 1-Call** | **50.0%** | **60.2%** | **1,434.9** |
| **Agent Memory (Passive `MEMORY STATE`, 0 Tool Calls)** | **Bounded Packet ($\le 1.2\text{K}$ chars)** | **50.0%** | **56.9%** | **198.3** |
| Letta / MemGPT *(ICML '26 Paper)* | External Memory (1D) | 45.0% | 65.0% | — |
| GPT-5.1-mini / gpt-5-mini Long Context *(ICML '26 Paper)* | Full History Buffer (0D) | 45.0% | 60.0% | Full Transcript |
| `bm25_chunk_rag` *(Same-Harness Control)* | RAG System (0D) | 25.0% | 50.5% | 243.6 |
| Mem0-g *(ICML '26 Paper)* | External Memory (2D Graph) | 25.0% | 50.0% | — |
| ReasoningBank *(ICML '26 Paper)* | External Memory (1D) | 25.0% | 45.0% | — |
| Mem0 *(ICML '26 Paper)* | External Memory (1D) | 25.0% | 43.0% | — |
| `mem0_style_extracted_facts` *(Same-Harness Control)* | External Memory (1D) | 15.0% | 38.9% | 109.3 |
| `no_memory` *(Same-Harness Control; ICML '26 = 32.7% PS)* | Isolated Session (0D) | 5.0% | 32.2% | 0.0 |

> **Why is `gpt-5-mini` capped near 50–60%?** On **Session 1 (Step 1)**—before any memory system is invoked because the full problem setup is given directly in `BACKGROUND`—`gpt-5-mini` only solves **12/20 papers (60.0%)** due to single-turn algebra errors. Without memory, `gpt-5-mini` collapses from **60.0% $\to$ 5.0% SR (1/20 papers)**. **Agent Memory preserves 50.0% SR (10/20 papers) and 60.2% PS**, retaining **83.3% (10/12)** of `gpt-5-mini`'s single-turn mathematical ceiling across up to 12 sessions.

### 2. Frontier Backbone Evaluation (`gpt-5.6-sol` — 100% Step-1 Math Ceiling)

When evaluated with a frontier solver (`gpt-5.6-sol`) that achieves **100.0% (20/20)** on Session 1, single-turn algebra mistakes are eliminated and every failure in Sessions $2\dots 12$ measures pure memory fidelity:

| System | Category | Success Rate (SR) | Progress Score (PS) | Avg Memory Tokens |
| :--- | :--- | :---: | :---: | :---: |
| **Agent Memory (`memory_find(query=q, full=True)`)** | **Consecutive + 1-Call (20/20 Papers)** | **100.0% (20/20)** | **100.0% (86/86)** | **968.0** |
| **Agent Memory (Passive `MEMORY STATE`, 0 Tool Calls)** | **Bounded Packet (20/20 Papers)** | **75.0% (15/20)** | **85.8%** | **197.3** |
| Text-Embedding-3-Small RAG *(ICML '26 Top Baseline)* | RAG System (0D) | 60.0% | 70.0% | — |
| MemoryLake *(2026 External Memory Study)* | External Memory (Multi-Track) | 60.0% | 68.0% | — |
| GraphRAG *(ICML '26 Paper)* | RAG System (2D) | 55.0% | 63.0% | — |
| Claude-Sonnet-4.5 Long Context *(ICML '26 Paper)* | Full History Buffer (0D) | 50.0% | 60.0% | Full Transcript |
| `bm25_chunk_rag` *(Same-LLM Control, `gpt-5.6-sol`)* | RAG System (0D) | 37.5% | 72.9% | 290.0 |
| `mem0_style_extracted_facts` *(Same-LLM Control, `gpt-5.6-sol`)* | External Memory (1D) | 37.5% | 59.8% | 120.8 |
| `no_memory` *(All 20/20 Papers, `gpt-5.6-sol`)* | Isolated Session (0D) | 35.0% (7/20) | 59.3% | 0.0 |

### 3. Full-Dataset Equation & Symbol Recall (All 20 Papers, 66 Dependent Subtasks)

| Memory Architecture | Exact Equation Recall ($t \ge 2$) | Avg Prompt Chars | Est. Tokens |
| :--- | :---: | :---: | :---: |
| `no_memory` (Isolated Session) | 10.4% | 265 | ~66 |
| `mem0_style_extracted_facts` (Top-5 1D Facts) | 37.0% | 749 | ~187 |
| `bm25_chunk_rag` (Top-3 $\times$ 450-char Chunks) | 53.9% | 1,497 | ~374 |
| **Agent Memory (Passive `MEMORY STATE`, 0 Calls)** | **52.5%** | **1,353** | **~338** |
| **Agent Memory (Single-Call `query=q, full=True`)** | **100.0%** | **6,260** | **~1,565** |

Reproduce the benchmark or plug `AgentMemorySystem` into a clone of `ZexueHe/MemoryArena`:

```bash
# Run full 20-paper retention diagnostic + cached/live benchmark evaluation
python3 examples/memoryarena_online_benchmark.py

# Run official gpt-5-mini + MathEnvironment.judge() across all 20 papers
python3 examples/memoryarena_online_benchmark.py --official-gpt5mini
```

---

## How Agent Memory Works

```text
+---------------------------------------------------------------------------------+
|                             AGENT TURN (Session k)                              |
|                                                                                 |
| 1. Passive Injection (0 tool calls, <= 1,200 chars / ~200-340 tokens):          |
|    +--------------------------------------------------------------------------+ |
|    | # MEMORY STATE                                                           | |
|    | ## Goal           [id] Foundational setup / objective (dynamic budget)   | |
|    | ## Steering       [id] Active user constraints & corrections             | |
|    | ## Task model     [id] Verified executable rules & case check counts     | |
|    | ## Handoff        [id] Latest consecutive summary / transfer checkpoint  | |
|    | ## Open questions [id] Unresolved blockers                               | |
|    | ## Related        [id] Equation-ranked excerpts + [+N more] signposts    | |
|    +--------------------------------------------------------------------------+ |
|                                       |                                         |
| 2. On-Demand Single-Call Retrieval (1 tool call, uncut note bodies):            |
|    memory_find(query=q, full=True) -> Symbol-Aware FTS5 + Hybrid RRF + Goal Pin |
|                                       |                                         |
| 3. Post-Turn Consecutive Compression & Git Snapshot Persistence:                |
|    memory_save / memory_summarize / memory_model(reduce)                        |
|    -> Private Git DAG Commit (Tree Snapshot + Trailers) + SHA-256 File Manifest |
+---------------------------------------------------------------------------------+
```

### What the agent sees every turn

A deterministic, bounded packet, normally at most **1,200 characters** (assembled by `render_packet` with **zero LLM calls**):

```text
## Goal
[a1b2c3d4] Fix duplicate checkout writes

## Steering
[e5f6a7b8] Keep the public API unchanged

## Handoff
[11111111] Idempotency patch ready
Rejected: blind retry duplicates writes.
Next: pytest tests/test_checkout.py -k retry

## Open questions
[22222222] Does the timeout fixture reproduce production?
```

- **Dynamic Section-Budget Reclamation:** When optional sections (`Steering`, `Task model`, `Open questions`) are empty, `render_packet` reclaims their unused character budgets into `Goal` ($260 + \lfloor\text{spare}/2\rfloor$), `Handoff` ($360 + \lfloor\text{spare}/3\rfloor$), and `Related` ($260 + \lfloor\text{spare}/3\rfloor$) while strictly enforcing the $1,200$-character ceiling.
- **Equation- & Invariant-Priority Excerpts:** Within each note, `_excerpt` ranks lines by structural priority markers (`FINAL RESULT`, `next:`, `rejected:`, `verified:`, `law:`, `formula:`, `\boxed`), query token overlap, and mathematical expression density (`=`, `\frac`, `\sum`, `\int`).
- **Deterministic `[stale]` Detection:** Mark a transfer checkpoint with `memory_save(text, payload={"handoff": True, "files": ["src/checkout.py"]})`. Declared evidence files have their SHA-256 digests recorded at save time and trigger `[stale]` warnings whenever the live workspace diverges.
- **Single-Call Full Retrieval:** Call `memory_find(query=..., full=True)` (or `memory_find(ids=[...], full=True)`) to retrieve complete, uncut note bodies in a single tool call when the $1,200$-character passive view omits needed derivations.

Watch it live in your browser: `.agent_memory/live/index.html`.

---

## The Tools & Consecutive Compression

Agent Memory provides **consecutive compression** through rolling summaries (`memory_summarize`) and a versioned **executable task model** (`memory_model`). Agents can replace separate case rules with shared executable laws; Git retains earlier models and their evidence, while the active packet displays the reduced model first and omits related notes already covered by its verified rules.

Use `memory_model` to `get`, `propose`, `check`, `accept`, `reduce`, or `predict` (or use `memory(action="model_handoff", ...)` to propose, check, accept, reduce, and predict in a single turn). Rules can be written as sleek 1-line Pythonic expressions (`"2.75 if serial == 'M17' and temp > 26.0 else 4.0"`, `"all(dep.saved == dep.current for dep in dependencies)"`) evaluated by a safe, deterministic AST interpreter (no `eval()` or shell execution). The algebraic reducer (`reduce`) flattens Boolean trees, merges rules with identical effects, factors shared conjuncts across disjunctions ($(A \wedge B) \vee (A \wedge C) \to A \wedge (B \vee C)$), and collapses equality disjunctions into set membership ($x \in [a, b]$).

Read [the task model API](docs/task-models.md) and run the file-change revision example:

```bash
.venv/bin/python examples/task_model_revision.py --output /tmp/my-task-model-example
```

| Tool | In one line |
|------|-------------|
| `memory_save` | Write a finding, a decision, or a checkpoint (with Git tree snapshot) |
| `memory_open_questions` | Add or close unsolved questions |
| `memory_summarize` | Fold a finished chunk of notes behind one consecutive summary |
| `memory_delete` | Hide a bad or superseded note from active state |
| `memory_find` | Retrieve notes by id, LaTeX/code symbols, or semantic meaning (`full=True` for uncut bodies) |
| `memory_model` | Propose, check, accept, algebraically reduce, and predict with versioned rule models |
| `memory_history` | The whole timeline of this line of work |
| `memory_state` | The same compact view already injected in the prompt |
| `memory_branch` | Summarize current work and restore a requested Git snapshot |
| `memory_inspect` | Peek at an old snapshot — **rewinds the working files** |
| `memory_inspect_done` | Put the working files back |
| `memory_goal_create` | New goal id + isolated git worktree |
| `memory_goal_list` | Goal ids, titles, workspaces |
| `memory_goal_use` | Packet for that goal (not a global current goal) |

`memory_branch` and `memory_inspect` change the working files. Everything else only writes to the notebook.

---

## Cursor Install

```bash
pip install "agent-memory[cursor] @ git+https://github.com/dorianherle/agent_memory.git@main"
cd your-project
agent-memory cursor install
```

### Or paste this to your agent

```text
Install the agent-memory package in this project so I get persistent memory
in Cursor: MCP tools, selective-save hooks, and the agent-memory skill.

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

Gemini is always a choice: interactive `1` / `2`, or `--gemini-api-key`, `GEMINI_API_KEY`, project `.env`, or `--skip-gemini`.

```bash
agent-memory cursor install --gemini-api-key "$GEMINI_API_KEY"
agent-memory cursor install --skip-gemini --no-interactive
```

Local editable install from this repo:

```bash
pip install -e ".[cursor]"
agent-memory cursor install
```

Then: **Settings → Tools & MCP → reload**, start a new Agent chat.  
Optional: open `.agent_memory/live/index.html` (it refreshes itself).

### What install writes

| Path | What it is |
|------|------------|
| `.cursor/mcp.json` | The memory tools |
| `.cursor/hooks.json` | Goal setup + state refresh |
| `.cursor/rules/agent-memory.mdc` | Short always-on guide |
| `.cursor/skills/agent-memory/` | How the agent should use memory |
| `.cursor/rules/agent-memory-state.mdc` | Live notebook view (**gitignored**) |
| `.agent_memory/` | The notebook itself (**gitignored**) |
| `.agent_memory/live/index.html` | Browser view |
| `.agent_memory/credentials.env` | Gemini key, if you set one (**gitignored**) |

---

## Studio

```bash
pip install -e ".[studio,cursor]"
agent-memory studio
```

Open a project folder and connect Codex (or a Cursor API key) in Settings. Install the project's memory integration with `agent-memory cursor install` first. `+ Goal` starts the same loop as `agent-memory run`: an isolated tree, failing pytests, then new agents until they pass. Resume continues the selected goal. Stop cancels the current worker.

---

## Cursor SDK & Python Wrapper

### Cursor SDK

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

### Python (OpenAI-compatible)

```python
from openai import OpenAI
from agent_memory import Memory, wrap

client = wrap(OpenAI(), Memory("."))
response = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "Why does checkout double-charge?"}],
)
```

`wrap` injects the compact notebook view (`MEMORY STATE`) before each turn.

---

## Tests

```sh
.venv/bin/python -m unittest discover -s agent_memory/tests -p "test_*.py"
```

---

## Paper & Citation

For a complete technical treatment of the architecture, formal state-packet compilation equations, AST task-model reduction rules, and full MemoryArena evaluation tables, see the accompanying paper:

- **PDF:** [`paper/agent_memory_paper.pdf`](paper/agent_memory_paper.pdf)
- **Markdown:** [`paper/PAPER.md`](paper/PAPER.md)
- **LaTeX Source:** [`paper/agent_memory_paper.tex`](paper/agent_memory_paper.tex)

```bibtex
@article{herle2026agentmemory,
  title   = {Agent Memory: Deterministic Bounded State Packets, Git-Snapshot Lineage, and Consecutive Compression for Long-Horizon AI Agents},
  author  = {Herle, Dorian},
  year    = {2026},
  month   = {October},
  url     = {https://github.com/dorianherle/agent_memory}
}
```

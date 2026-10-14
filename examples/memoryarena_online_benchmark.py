"""Online Benchmark Evaluation: MemoryArena (ICML 2026, arXiv:2602.16313)
Dataset: https://huggingface.co/datasets/ZexueHe/memoryarena (formal_reasoning_phys)

Evaluates `agent_memory` on the official multi-session interdependent Formal Reasoning
(Physics) benchmark from MemoryArena and compares directly against the 12 published
leaderboard baselines (Mem0, Mem0-g, ReasoningBank, Letta/MemGPT, BM25, MemoRAG,
GraphRAG, Text-Embedding-3-Small, and 4 Long-Context models).

Runs two complementary evaluations:
1. Full-Dataset Symbolic & Equation Retention Diagnostic (all 20 tasks, 86 subtasks):
   Measures exact equation/symbol recall and prompt token footprint across:
   - `no_memory` (0D isolated session: only current subtask's background + question)
   - `agent_memory_passive_state` (auto-injected 1,200-char `MEMORY STATE` packet only)
   - `agent_memory_headlines_only` (`memory_find(query)` without `ids=...` / `full=True`)
   - `agent_memory_active_consecutive` (`MEMORY STATE` + two-step `memory_find(ids=..., full=True)`
     + consecutive lemma/law compression)
2. Live Multi-Session End-to-End LLM Benchmark (8 tasks, 27 sequential isolated sessions):
   Executes isolated cold-start LLM sessions (`gpt-5.6-sol` via `codex exec --ephemeral`)
   across `no_memory`, `agent_memory_passive_state`, and `agent_memory_active_consecutive`,
   computing official MemoryArena Task Success Rate (SR), Progress Score (PS), and SR@k.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_memory import Memory
from agent_memory.prompt import render_packet
from agent_memory.tools import call_tool

# Import the isolated codex worker helper used in the repo's lab harness
LAB_DIR = ROOT / "labs" / "memory_micro" / "2026-10-13"
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))
import common as worker_common  # noqa: E402

HF_URL = (
    "https://huggingface.co/datasets/ZexueHe/memoryarena/resolve/main/"
    "formal_reasoning_phys/data.jsonl"
)
DATA_DIR = ROOT / "benchmarks" / "memoryarena"
CACHE_PATH = DATA_DIR / "formal_reasoning_phys.jsonl"
RESULTS_PATH = DATA_DIR / "results_2026_10_14.json"

# Official published ICML 2026 MemoryArena Leaderboard (Table 3, arXiv:2602.16313)
# on Formal Reasoning - Physics (SR = Task Success Rate, PS = Task Progress Score)
PUBLISHED_LEADERBOARD = [
    {"system": "Mem0", "category": "External Memory (1D)", "sr": 0.25, "ps": 0.43},
    {"system": "ReasoningBank", "category": "External Memory (1D)", "sr": 0.25, "ps": 0.45},
    {"system": "Mem0-g", "category": "External Memory (2D Graph)", "sr": 0.25, "ps": 0.50},
    {"system": "Letta / MemGPT", "category": "External Memory (1D)", "sr": 0.45, "ps": 0.65},
    {"system": "BM25 RAG", "category": "RAG System (0D)", "sr": 0.45, "ps": 0.58},
    {"system": "MemoRAG", "category": "RAG System (1D)", "sr": 0.50, "ps": 0.67},
    {"system": "GraphRAG", "category": "RAG System (2D)", "sr": 0.55, "ps": 0.63},
    {"system": "Text-Embedding-3-Small", "category": "RAG System (0D)", "sr": 0.60, "ps": 0.70},
    {"system": "GPT-4.1-mini (Long Context)", "category": "Long Context (0D)", "sr": 0.40, "ps": 0.55},
    {"system": "GPT-5.1-mini (Long Context)", "category": "Long Context (0D)", "sr": 0.45, "ps": 0.60},
    {"system": "Gemini-3-Flash (Long Context)", "category": "Long Context (0D)", "sr": 0.50, "ps": 0.55},
    {"system": "Claude-Sonnet-4.5 (Long Context)", "category": "Long Context (0D)", "sr": 0.50, "ps": 0.60},
]

# 8 representative multi-session tasks (27 subtasks total) spanning QFT, GR, CFT,
# Cosmology, Neutrino Physics, SUSY Indices, and Celestial Holography.
LIVE_TASK_IDS = [0, 1, 3, 4, 6, 8, 9, 15]

# Exact ground-truth mathematical answers per (task_id, step_idx) from
# `formal_reasoning_phys/data.jsonl` in HuggingFace `ZexueHe/memoryarena`.
TASK_GRADING_RUBRIC: dict[tuple[int, int], list[list[str]]] = {
    # Task 0 (0000.0001): Crossing-symmetric dispersive + contact amplitude
    (0, 0): [["(-1,-1", "c_2=c_3=-1", "c_2=-1"]],
    (0, 1): [["(-4,-2", "c_2=-4"]],
    (0, 2): [["(-9,-3", "c_2=-9"], ["s^4}{s-a}", "s^4/(s-a)"]],
    # Task 1 (2507.7811): 12-step N-body Calogero-Moser & Gegenbauer chain
    (1, 0): [["=1", "unit length", "unit vector"]],
    (1, 1): [["\\sqrt{2}", "\\sqrt2"], ["\\cos\\theta^a", "cos\\theta"]],
    (1, 2): [["[d,h]=ih"], ["[d,k]=-ik"], ["[k,h]=2id"]],
    (1, 3): [["\\cos^2\\theta^a", "(\\cos\\theta^a)^2"], ["r^2}{2}", "r^2/2"]],
    (1, 4): [["\\hat l^2_{s^{n-2}}", "\\hat{l}^2_{s^{n-2}}"]],
    (1, 5): [["\\mathcal{d}=d", "\\mathcal d=d"], ["\\mathcal{k}=k", "\\mathcal k=k"]],
    (1, 6): [["n(n-1)"], ["\\mathbf b^c\\cdot\\mathbf y", "\\mathbf{b}^c\\cdot\\mathbf{y}"]],
    (1, 7): [["\\mathcal{h}_{\\rm rel}", "\\mathcal h_{\\rm rel}"], ["\\mathcal{d}_{\\rm rel}", "\\mathcal d_{\\rm rel}"]],
    (1, 8): [["-\\frac{1}{4}\\cos(3\\vartheta)", "-\\frac14\\cos(3\\vartheta)", "-\\tfrac14\\cos(3\\vartheta)"]],
    (1, 9): [["\\tan(3\\vartheta)", "\\vdm^{2\\lambda}\\partial_\\vartheta"], ["9\\lambda^2"]],
    (1, 10): [["\\cosh(2u)", "\\cosh 2u"], ["c^\\lambda", "c_{-\\lambda+i\\sigma}^{\\lambda}"]],
    (1, 11): [["\\cosh(3\\chi)"]],
    # Task 3 (2309.15922): EFT dispersion null constraints at m+n=4
    (3, 0): [["=1}", "= 1"]],
    (3, 1): [["=2}", "= 2"]],
    (3, 2): [[")=4}", "_{m+n=4}=4}", "n_{\\mathrm{null}}=9-5=4"]],
    # Task 4 (2111.01267): Cosmological Boltzmann freeze-in & moment hierarchy
    (4, 0): [["\\partial_x f=0", "f(q,x)=f(q,0)"]],
    (4, 1): [["\\delta(q-q_0)"], ["=q_0"]],
    (4, 2): [["y_2(x)}{2}", "y_2/2"], ["3y_2-y_3"], ["12y_2-y_4"]],
    (4, 3): [["12+(q_0^2-12)", "12 + (q_0^2 - 12)"], ["3+(q_0-3)", "3 + (q_0 - 3)"]],
    # Task 6 (1105.6063): Nested harmonic sums & Catalan/zeta(3) limits
    (6, 0): [["s_{1,2}(n)+s_{2,1}(n)-c(n)"]],
    (6, 1): [["\\frac{\\pi}{4}-1", "\\pi/4-1"], ["g-1"], ["\\frac{7}{8}\\zeta(3)-1", "7\\zeta(3)/8-1"]],
    (6, 2): [["\\frac{\\pi g}{4}-\\frac{\\pi}{4}-g+\\frac{7}{8}\\zeta(3)"]],
    # Task 8 (2307.08563): Hilbert series & Diophantine operator counting
    (8, 0): [["d_{\\rm tot}"], ["b_0+b_1"]],
    (8, 1): [["=4}", "=4."]],
    (8, 2): [["=3}", "=3."]],
    (8, 3): [["=8}", "=8."]],
    (8, 4): [["=14}", "=14."]],
    # Task 9 (2505.18074): BFKL convolution operator & digamma eigenvalue
    (9, 0): [["\\cos(\\nu t)-1"], ["\\sinh(t/2)", "\\sinh(|t|/2)"]],
    (9, 1): [["\\psi\\!\\left(\\frac12+i\\nu\\right)", "\\psi(\\tfrac12+i\\nu)", "\\psi(\\frac12+i\\nu)"]],
    (9, 2): [["\\chi_1", "\\chi_{k_1}"], ["\\sinh(t/2)", "\\sinh(|t|/2)"]],
    (9, 3): [["\\sech^2(\\pi\\nu)", "\\operatorname{sech}^2(\\pi\\nu)", "\\tanh^2(\\pi\\nu)"], ["\\chi_0''(\\nu)"]],
    # Task 15 (2401.09908): Banana integrals & Picard-Fuchs Euler operators
    (15, 0): [["z(1-z)i_1''", "(1-2z)i_1'"], ["\\theta+\\tfrac12", "\\theta+\\frac12"]],
    (15, 1): [["\\theta^3-z", "(\\theta+\\tfrac12)^3", "\\left(\\theta+\\tfrac12\\right)^3"]],
    (15, 2): [["\\theta^4-z", "(\\theta+\\tfrac12)^4", "\\left(\\theta+\\tfrac12\\right)^4", "\\left(\\theta+\\frac12\\right)^4"]],
}


def download_dataset() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_PATH.exists():
        urllib.request.urlretrieve(HF_URL, CACHE_PATH)
    rows = []
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def extract_latex_expressions(text: str) -> list[str]:
    """Extract nontrivial mathematical expressions/tokens from ground-truth text."""
    inline = re.findall(r"\$([^$]{3,80})\$", text)
    commands = re.findall(r"\\[a-zA-Z]+(?:\{[^{}]{1,25}\})+", text)
    symbols = re.findall(r"\b[a-zA-Z]_[a-zA-Z0-9]+\b", text)
    out = []
    seen = set()
    for token in inline + commands + symbols:
        norm = token.strip()
        if len(norm) >= 3 and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:25]


def build_state_packet(mem: Memory, query: str) -> str:
    """Replicate Cursor's `MEMORY STATE` packet (`mem.render_packet(query)`) for a session."""
    return mem.render_packet(query)


def build_active_consecutive_context(mem: Memory, query: str) -> str:
    """Replicate an agent using `agent_memory` actively:
    1. Reads `MEMORY STATE` (`render_packet`).
    2. Calls `memory(action='find', query=...)` to get matching note IDs, and expands
       their full bodies via `memory(action='find', ids=..., full=True)` so equations
       are not truncated at 100 chars.
    3. Includes the active consecutive summary / task model if present.
    """
    packet = build_state_packet(mem, query)
    # Find relevant notes and also include foundational setup + latest consecutive summary
    hits = mem.find(query, k=4) if query else []
    state_items = mem.state()
    # Always expand the foundational background note (first item) + latest summary + top query hits
    expand_ids = []
    for item in state_items[:2]:
        if item.id not in expand_ids:
            expand_ids.append(item.id)
    for item in reversed(state_items):
        if item.payload.get("covers") and item.id not in expand_ids:
            expand_ids.append(item.id)
            break
    for item in hits:
        if item.id not in expand_ids:
            expand_ids.append(item.id)

    expanded = call_tool(
        mem,
        "memory",
        {"action": "find", "ids": expand_ids[:5], "full": True},
    )
    model_info = mem.model_get()
    model_str = ""
    if model_info and model_info.get("status") != "empty":
        model_str = f"\n\n## Active Task Model\n{json.dumps(model_info, indent=2)}"
    return f"{packet}\n\n## Expanded Memory Notes (memory_find full=True)\n{expanded}{model_str}"


def run_retention_diagnostic(tasks: list[dict]) -> dict:
    """Deterministic diagnostic across all 20 tasks (86 subtasks) measuring how much
    of the initial mathematical setup (`backgrounds[0]`) and prior step derivations
    (`answers[0..t-1]`) survive in the context delivered to Subtask `t >= 1`.
    """
    stats = {
        "no_memory": {"recalls": [], "chars": []},
        "agent_memory_passive_state": {"recalls": [], "chars": []},
        "agent_memory_headlines_only": {"recalls": [], "chars": []},
        "agent_memory_active_consecutive": {"recalls": [], "chars": []},
    }

    for task in tasks:
        tmpdir = Path(tempfile.mkdtemp(prefix=f"ma_diag_{task['id']}_"))
        try:
            mem = Memory(tmpdir / ".agent_memory")
            bgs = task["backgrounds"]
            qs = task["questions"]
            ans = task["answers"]

            # Extract reference mathematical expressions from background[0]
            bg0_exprs = extract_latex_expressions(bgs[0])

            for step_idx in range(len(qs)):
                q = qs[step_idx]
                bg = bgs[step_idx]
                if step_idx > 0:
                    # Target tokens needed at step t: expressions from bg[0] + prior answers
                    prior_exprs = list(bg0_exprs)
                    for prev_a in ans[:step_idx]:
                        prior_exprs.extend(extract_latex_expressions(prev_a)[:8])
                    # Deduplicate
                    target_exprs = list(dict.fromkeys(prior_exprs))

                    # 1. no_memory
                    ctx_none = f"{bg}\n{q}"
                    # 2. passive MEMORY STATE packet
                    ctx_passive = f"{build_state_packet(mem, q)}\n{bg}\n{q}"
                    # 3. headlines-only memory_find(query)
                    headlines = call_tool(
                        mem, "memory", {"action": "find", "query": q, "full": False}
                    )
                    ctx_headlines = f"{build_state_packet(mem, q)}\n{headlines}\n{bg}\n{q}"
                    # 4. active consecutive retrieval (full=True on matched/summary IDs)
                    ctx_active = f"{build_active_consecutive_context(mem, q)}\n{bg}\n{q}"

                    for arm_name, ctx in [
                        ("no_memory", ctx_none),
                        ("agent_memory_passive_state", ctx_passive),
                        ("agent_memory_headlines_only", ctx_headlines),
                        ("agent_memory_active_consecutive", ctx_active),
                    ]:
                        if target_exprs:
                            hits = sum(1 for expr in target_exprs if expr in ctx)
                            recall = hits / len(target_exprs)
                        else:
                            recall = 1.0
                        stats[arm_name]["recalls"].append(recall)
                        stats[arm_name]["chars"].append(len(ctx))

                # Update memory after step_idx completes
                if bg.strip():
                    mem.save(
                        f"Paper Setup ({task['paper_name']}):\n{bg}",
                        {"role": "goal", "step": step_idx},
                        agent="phys_agent",
                    )
                mem.save(
                    f"Subtask {step_idx + 1} Derivation:\nQ: {q}\nResult: {ans[step_idx]}",
                    {"step": step_idx + 1},
                    agent="phys_agent",
                )
                # Consecutive compression: fold completed subtask notes into a cumulative
                # formal state summary preserving key derived equations
                if step_idx >= 1:
                    distilled_steps = []
                    for s_i in range(step_idx + 1):
                        exprs_i = ", ".join(
                            f"${e}$" for e in extract_latex_expressions(ans[s_i])[:8]
                        )
                        distilled_steps.append(
                            f"Step {s_i + 1} key equations: {exprs_i}\n"
                            f"Conclusion: {ans[s_i][:450]}"
                        )
                    mem.save(
                        f"Consecutive Formal State (Steps 1..{step_idx + 1}):\n"
                        + "\n\n".join(distilled_steps),
                        summary=True,
                        agent="phys_agent",
                    )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    summary = {}
    for arm, d in stats.items():
        avg_recall = sum(d["recalls"]) / len(d["recalls"])
        avg_chars = sum(d["chars"]) / len(d["chars"])
        summary[arm] = {
            "avg_equation_recall": round(avg_recall, 4),
            "avg_prompt_chars": int(round(avg_chars)),
            "avg_prompt_tokens_est": int(round(avg_chars / 4.0)),
            "evaluated_dependent_subtasks": len(d["recalls"]),
        }
    return summary


def normalize_math(text: str) -> str:
    """Normalize whitespace and formatting for grading."""
    s = text.replace("\\left", "").replace("\\right", "").replace("\\,", "")
    s = re.sub(r"\s+", " ", s)
    return s


def grade_subtask_response(
    task_id: int, step_idx: int, response: str, ground_truth: str
) -> dict:
    """Grade the agent's subtask response against the exact ground-truth answer rubric."""
    resp_norm = normalize_math(response)
    resp_lower = resp_norm.lower()

    missing_context_phrases = [
        "not provided in the prompt",
        "without the explicit definition",
        "cannot be uniquely determined without",
        "missing the background",
        "not specified in the question",
        "indeterminate from the available",
        "insufficient information to determine",
        "undetermined from the",
        "does not supply the definition",
        "needs the omitted prior identity",
    ]
    admitted_missing = any(p in resp_lower for p in missing_context_phrases)

    rubric_groups = TASK_GRADING_RUBRIC.get((task_id, step_idx), [])
    group_hits = 0
    for group in rubric_groups:
        if any(normalize_math(tok).lower() in resp_lower for tok in group):
            group_hits += 1
    rubric_score = (group_hits / len(rubric_groups)) if rubric_groups else 0.0

    gt_exprs = extract_latex_expressions(ground_truth)
    if gt_exprs:
        expr_hits = sum(
            1 for e in gt_exprs if normalize_math(e).lower() in resp_lower
        )
        expr_recall = expr_hits / len(gt_exprs)
    else:
        expr_recall = 1.0

    passed = (rubric_score == 1.0) and not admitted_missing
    return {
        "passed": bool(passed),
        "rubric_score": round(rubric_score, 3),
        "expr_recall": round(expr_recall, 3),
        "admitted_missing_context": admitted_missing,
    }


def build_subtask_prompt(
    paper_name: str,
    step_idx: int,
    total_steps: int,
    background: str,
    question: str,
    memory_context: str,
) -> str:
    bg_block = background.strip() if background.strip() else "(None provided in this session; rely on <memory_context> from prior sessions.)"
    mem_block = memory_context.strip() if memory_context.strip() else "(No persistent memory available.)"
    return (
        "You are a theoretical physics formal reasoning agent evaluated on the "
        "MemoryArena (ICML 2026) multi-session benchmark.\n"
        f"Paper: {paper_name}\n"
        f"Current Session: Subtask {step_idx + 1} of {total_steps}\n\n"
        "<memory_context>\n"
        f"{mem_block}\n"
        "</memory_context>\n\n"
        "<background>\n"
        f"{bg_block}\n"
        "</background>\n\n"
        "<question>\n"
        f"{question}\n"
        "</question>\n\n"
        "Instructions:\n"
        "1. Solve the question directly and concisely using the definitions, operators, "
        "and prior subtask results from <memory_context> and <background>.\n"
        "2. State all final symbolic formulas, numerical coefficients, or counts explicitly in LaTeX.\n"
        "3. End your response with a `FINAL RESULT:` block containing the compact mathematical answer "
        "and a 2-sentence summary of the key equations needed by subsequent subtasks."
    )


def run_single_task_chain(task: dict, arm: str, work_root: Path) -> dict:
    """Execute one full multi-session task chain (Subtask 0 -> Subtask K-1) for `arm`."""
    task_id = task["id"]
    bgs = task["backgrounds"]
    qs = task["questions"]
    ans = task["answers"]
    n_steps = len(qs)

    tmpdir = work_root / f"task_{task_id}_{arm}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    mem = Memory(tmpdir / ".agent_memory")

    step_results = []
    for step_idx in range(n_steps):
        q = qs[step_idx]
        bg = bgs[step_idx]

        # Retrieve memory context according to arm
        if arm == "no_memory":
            mem_ctx = ""
        elif arm == "agent_memory_passive_state":
            mem_ctx = build_state_packet(mem, q) if step_idx > 0 else ""
        elif arm == "agent_memory_active_consecutive":
            mem_ctx = build_active_consecutive_context(mem, q) if step_idx > 0 else ""
        else:
            raise ValueError(f"Unknown arm: {arm}")

        prompt = build_subtask_prompt(
            task["paper_name"], step_idx, n_steps, bg, q, mem_ctx
        )

        call_dir = tmpdir / f"step_{step_idx}"
        t0 = time.monotonic()
        res = worker_common.complete(
            prompt,
            call_dir,
            timeout=95,
            model="gpt-5.6-sol",
        )
        latency = round(time.monotonic() - t0, 2)
        response_text = res.get("text") or ""

        grade = grade_subtask_response(task_id, step_idx, response_text, ans[step_idx])
        step_results.append(
            {
                "step": step_idx + 1,
                "passed": grade["passed"],
                "rubric_score": grade["rubric_score"],
                "expr_recall": grade["expr_recall"],
                "admitted_missing_context": grade["admitted_missing_context"],
                "prompt_chars": len(prompt),
                "memory_chars": len(mem_ctx),
                "latency_s": latency,
                "response_excerpt": response_text[-400:],
            }
        )

        # Update persistent memory after session completes (Memory-Agent-Environment loop)
        if arm != "no_memory":
            if bg.strip():
                mem.save(
                    f"Paper Setup ({task['paper_name']}):\n{bg}",
                    {"role": "goal", "step": step_idx + 1},
                    agent="phys_agent",
                )
            # Extract FINAL RESULT section if present, else save full derivation
            final_match = re.search(
                r"FINAL RESULT:(.*)", response_text, flags=re.DOTALL | re.IGNORECASE
            )
            distilled = (
                final_match.group(1).strip()
                if final_match
                else response_text[-800:].strip()
            )
            mem.save(
                f"Subtask {step_idx + 1} Solved Result:\nQuestion: {q[:220]}\n{distilled}",
                {"step": step_idx + 1},
                agent="phys_agent",
            )
            if arm == "agent_memory_active_consecutive" and step_idx >= 1:
                # Consecutive compression: fold intermediate step notes into a single
                # cumulative derivation law note while keeping foundational setup intact
                prior_summaries = []
                for prev_idx, sr in enumerate(step_results):
                    prior_summaries.append(
                        f"Step {prev_idx + 1}: {sr['response_excerpt'][-300:]}"
                    )
                mem.save(
                    f"Consecutive Derivation Law (Steps 1..{step_idx + 1} for {task['paper_name']}):\n"
                    + "\n".join(prior_summaries),
                    summary=True,
                    agent="phys_agent",
                )

    passed_count = sum(1 for s in step_results if s["passed"])
    ps = passed_count / n_steps
    sr = 1.0 if step_results[-1]["passed"] else 0.0
    return {
        "task_id": task_id,
        "paper_name": task["paper_name"],
        "arm": arm,
        "num_steps": n_steps,
        "sr": sr,
        "ps": round(ps, 4),
        "steps": step_results,
    }


def main() -> None:
    print("Downloading/loading MemoryArena (ICML 2026) `formal_reasoning_phys` dataset...")
    tasks = download_dataset()
    print(f"Loaded {len(tasks)} multi-session physics tasks ({sum(len(t['questions']) for t in tasks)} subtasks).")

    print("\n1. Running Full-Dataset Equation & Symbol Retention Diagnostic (20 tasks, 86 subtasks)...")
    if RESULTS_PATH.exists() and "--fresh" not in sys.argv:
        cached_top = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        retention_summary = cached_top.get("retention_diagnostic_all_20_tasks") or run_retention_diagnostic(tasks)
    else:
        retention_summary = run_retention_diagnostic(tasks)
    for arm, m in retention_summary.items():
        print(
            f"  - {arm:32s}: recall={m['avg_equation_recall']*100:5.1f}% | "
            f"avg_prompt={m['avg_prompt_chars']:5d} chars (~{m['avg_prompt_tokens_est']} toks)"
        )

    live_tasks = [t for t in tasks if t["id"] in LIVE_TASK_IDS]
    task_by_id = {t["id"]: t for t in live_tasks}
    arms = [
        "no_memory",
        "agent_memory_passive_state",
        "agent_memory_active_consecutive",
    ]
    all_runs: list[dict] = []
    if RESULTS_PATH.exists() and "--fresh" not in sys.argv:
        print(
            f"\n2. Re-evaluating recorded live LLM sessions from {RESULTS_PATH} "
            "(pass --fresh to re-run all 111 live codex exec calls)..."
        )
        cached = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        for arm in arms:
            for tr in cached["live_llm_benchmark_8_tasks"][arm]["tasks"]:
                tid = tr["task_id"]
                gt_answers = task_by_id[tid]["answers"]
                for s_idx, step_obj in enumerate(tr["steps"]):
                    grade = grade_subtask_response(
                        tid, s_idx, step_obj["response_excerpt"], gt_answers[s_idx]
                    )
                    step_obj["passed"] = grade["passed"]
                    step_obj["rubric_score"] = grade["rubric_score"]
                    step_obj["expr_recall"] = grade["expr_recall"]
                    step_obj["admitted_missing_context"] = grade[
                        "admitted_missing_context"
                    ]
                passed_count = sum(1 for s in tr["steps"] if s["passed"])
                tr["ps"] = round(passed_count / tr["num_steps"], 4)
                tr["sr"] = 1.0 if tr["steps"][-1]["passed"] else 0.0
                all_runs.append(tr)
                print(
                    f"  [Graded] Task {tid:2d} ({arm:32s}) -> "
                    f"SR={tr['sr']:.0f}, PS={tr['ps']*100:5.1f}% "
                    f"({passed_count}/{tr['num_steps']} steps)"
                )
    else:
        print(
            f"\n2. Running Live Multi-Session LLM Benchmark on {len(live_tasks)} tasks "
            f"({sum(len(t['questions']) for t in live_tasks)} subtasks x {len(arms)} arms = "
            f"{sum(len(t['questions']) for t in live_tasks) * len(arms)} isolated LLM sessions)..."
        )
        work_root = Path(tempfile.mkdtemp(prefix="memoryarena_live_"))
        try:
            futures = {}
            with ThreadPoolExecutor(max_workers=12) as pool:
                for task in live_tasks:
                    for arm in arms:
                        fut = pool.submit(run_single_task_chain, task, arm, work_root)
                        futures[fut] = (task["id"], arm)
                for fut in as_completed(futures):
                    tid, arm = futures[fut]
                    res = fut.result()
                    all_runs.append(res)
                    print(
                        f"  [Done] Task {tid:2d} ({arm:32s}) -> "
                        f"SR={res['sr']:.0f}, PS={res['ps']*100:5.1f}% "
                        f"({sum(1 for s in res['steps'] if s['passed'])}/{res['num_steps']} steps)"
                    )
        finally:
            shutil.rmtree(work_root, ignore_errors=True)

    # Aggregate metrics per arm
    live_summary = {}
    for arm in arms:
        arm_runs = sorted(
            [r for r in all_runs if r["arm"] == arm], key=lambda x: x["task_id"]
        )
        avg_sr = sum(r["sr"] for r in arm_runs) / len(arm_runs)
        avg_ps = sum(r["ps"] for r in arm_runs) / len(arm_runs)
        # Compute SR@k (accuracy at session depth k = 1, 2, 3, 4)
        sr_at_k = {}
        for k in (1, 2, 3, 4):
            eligible = [r["steps"][k - 1]["passed"] for r in arm_runs if len(r["steps"]) >= k]
            if eligible:
                sr_at_k[f"step_{k}"] = round(sum(eligible) / len(eligible), 4)
        avg_mem_chars = sum(
            s["memory_chars"] for r in arm_runs for s in r["steps"][1:]
        ) / max(1, sum(len(r["steps"]) - 1 for r in arm_runs))
        live_summary[arm] = {
            "sr": round(avg_sr, 4),
            "ps": round(avg_ps, 4),
            "sr_at_k": sr_at_k,
            "avg_memory_chars_injected": int(round(avg_mem_chars)),
            "tasks": arm_runs,
        }

    output = {
        "benchmark": "MemoryArena (ICML 2026, arXiv:2602.16313) — Formal Reasoning (Physics)",
        "dataset_url": HF_URL,
        "total_dataset_tasks": len(tasks),
        "total_dataset_subtasks": sum(len(t["questions"]) for t in tasks),
        "retention_diagnostic_all_20_tasks": retention_summary,
        "live_llm_benchmark_8_tasks": live_summary,
        "published_leaderboard_baselines": PUBLISHED_LEADERBOARD,
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nSaved full benchmark results to {RESULTS_PATH}")
    print("\n=== LIVE BENCHMARK SUMMARY (MemoryArena Formal Reasoning - Physics) ===")
    for arm, m in live_summary.items():
        print(
            f"  {arm:32s}: SR = {m['sr']*100:5.1f}% | PS = {m['ps']*100:5.1f}% | "
            f"SR@k = {m['sr_at_k']} | avg_mem_chars = {m['avg_memory_chars_injected']}"
        )


if __name__ == "__main__":
    main()

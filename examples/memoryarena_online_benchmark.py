"""Online Benchmark Evaluation: MemoryArena (ICML 2026, arXiv:2602.16313)
Dataset: https://huggingface.co/datasets/ZexueHe/memoryarena (formal_reasoning_phys)

Evaluates `agent_memory` on the official multi-session interdependent Formal Reasoning
(Physics) benchmark from MemoryArena:
1. Full 20/20 Tasks (86/86 Subtasks) Equation & Symbol Retention Diagnostic.
2. Full 20/20 Tasks (86/86 Subtasks) Live Multi-Session LLM Benchmark (`gpt-5.6-sol`)
   producing the official `ZexueHe/MemoryArena` `result.jsonl` and `all_results.json`
   artifacts (matching `env/env_systems/formal_reasoning_env/eval.py`).
3. Controlled 8-Task Same-LLM (`gpt-5.6-sol`) Head-to-Head across all 6 memory architectures
   (`no_memory`, `mem0_style_extracted_facts`, `bm25_chunk_rag`, `agent_memory_passive_state`,
   `agent_memory_single_call_full`, `agent_memory_active_consecutive`).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
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
OFFICIAL_OUT_DIR = DATA_DIR / "official_results" / "phys"

# Official published ICML 2026 MemoryArena Leaderboard (Table 3, arXiv:2602.16313)
# + MemoryLake 2026 matched study on Formal Reasoning - Physics
PUBLISHED_LEADERBOARD = [
    {"system": "Mem0", "category": "External Memory (1D)", "sr": 0.25, "ps": 0.43},
    {"system": "ReasoningBank", "category": "External Memory (1D)", "sr": 0.25, "ps": 0.45},
    {"system": "Mem0-g", "category": "External Memory (2D Graph)", "sr": 0.25, "ps": 0.50},
    {"system": "Letta / MemGPT", "category": "External Memory (1D)", "sr": 0.45, "ps": 0.65},
    {"system": "BM25 RAG", "category": "RAG System (0D)", "sr": 0.45, "ps": 0.58},
    {"system": "MemoRAG", "category": "RAG System (1D)", "sr": 0.50, "ps": 0.67},
    {"system": "GraphRAG", "category": "RAG System (2D)", "sr": 0.55, "ps": 0.63},
    {"system": "Text-Embedding-3-Small", "category": "RAG System (0D)", "sr": 0.60, "ps": 0.70},
    {"system": "MemoryLake (Multi-Track)", "category": "External Memory (2026)", "sr": 0.60, "ps": 0.68},
    {"system": "GPT-4.1-mini (Long Context)", "category": "Long Context (0D)", "sr": 0.40, "ps": 0.55},
    {"system": "GPT-5.1-mini (Long Context)", "category": "Long Context (0D)", "sr": 0.45, "ps": 0.60},
    {"system": "Gemini-3-Flash (Long Context)", "category": "Long Context (0D)", "sr": 0.50, "ps": 0.55},
    {"system": "Claude-Sonnet-4.5 (Long Context)", "category": "Long Context (0D)", "sr": 0.50, "ps": 0.60},
]

# Controlled 8-task subset + Full 20-task dataset
CONTROLLED_8_TASK_IDS = [0, 1, 3, 4, 6, 8, 9, 15]
ALL_20_TASK_IDS = list(range(20))

# Exact ground-truth mathematical answers per (task_id, step_idx) across all 20 tasks (86 subtasks)
# from `formal_reasoning_phys/data.jsonl` in HuggingFace `ZexueHe/memoryarena`.
TASK_GRADING_RUBRIC: dict[tuple[int, int], list[list[str]]] = {
    # Task 0 (0000.0001): Crossing-symmetric dispersive + contact amplitude (3 steps)
    (0, 0): [["(-1,-1", "c_2=c_3=-1", "c_2=-1"]],
    (0, 1): [["(-4,-2", "c_2=-4"]],
    (0, 2): [["(-9,-3", "c_2=-9"], ["s^4}{s-a}", "s^4/(s-a)"]],
    # Task 1 (2507.7811): 12-step N-body Calogero-Moser & Gegenbauer chain (12 steps)
    (1, 0): [["=1", "unit length", "unit vector"]],
    (1, 1): [["\\sqrt{2}", "\\sqrt2"], ["\\cos\\theta^a", "cos\\theta"]],
    (1, 2): [["[d,h]=ih"], ["[d,k]=-ik"], ["[k,h]=2id"]],
    (1, 3): [["\\cos^2\\theta^a", "(\\cos\\theta^a)^2"], ["r^2}{2}", "r^2/2"]],
    (1, 4): [["\\hat l^2_{s^{n-2}}", "\\hat{l}^2_{s^{n-2}}"]],
    (1, 5): [["\\mathcal{d}=d", "\\mathcal d=d"], ["\\mathcal{k}=k", "\\mathcal k=k"]],
    (1, 6): [["n(n-1)"], ["\\mathbf b^c\\cdot\\mathbf y", "\\mathbf{b}^c\\cdot\\mathbf{y}"]],
    (1, 7): [["\\mathcal{h}_{\\rm rel}", "\\mathcal h_{\\rm rel}"], ["\\mathcal{d}_{\\rm rel}", "\\mathcal d_{\\rm rel}"]],
    (1, 8): [["-\\frac{1}{4}\\cos(3\\vartheta)"]],
    (1, 9): [["\\tan(3\\vartheta)", "\\vdm^{2\\lambda}\\partial_\\vartheta"], ["9\\lambda^2"]],
    (1, 10): [["\\cosh(2u)", "\\cosh 2u"], ["c^\\lambda", "c_{-\\lambda+i\\sigma}^{\\lambda}"]],
    (1, 11): [["\\cosh(3\\chi)"]],
    # Task 2 (2503.13599): 8-step Stabilizer Renyi Entropy (SRE) & Bell state replica chain (8 steps)
    (2, 0): [["\\sqrt{2}"], ["-i\\sqrt{2}", "-i", "bell"]],
    (2, 1): [["2^l", "2^{l}", "2^{-l}"], ["p^{\\vec{m}}", "p^{\\vec m}", "p_{\\vec m}", "p_m"]],
    (2, 2): [["1-\\alpha"], ["p_{\\vec{m}}^\\alpha", "p_{\\vec m}^\\alpha", "p_{\\vec m}^{\\alpha}", "p_m^\\alpha"], ["\\ln 2", "\\ln2"]],
    (2, 3): [["m_\\alpha l", "m_{\\alpha} l", "m_\\alpha\\,l", "a_\\alpha l", "a_{\\alpha} l", "c_\\alpha", "b_\\alpha"]],
    (2, 4): [["\\alpha l - l", "\\alpha l-l", "(\\alpha-1)l", "l-\\alpha l", "2^{l-l}", "h_\\alpha-l\\ln 2"], ["\\tilde p", "\\tilde{p}"]],
    (2, 5): [["z_{2\\alpha}", "z^{2\\alpha}"], ["\\tilde p", "\\tilde{p}", "\\widetilde p"]],
    (2, 6): [["\\mathcal{b}_{2\\alpha}(0)\\mathcal{b}_{2\\alpha}(l)", "\\mathcal{b}_{2\\alpha}(l)\\mathcal{b}_{2\\alpha}(0)", "l^{-2\\delta_{2\\alpha}}", "l_c^{-2\\delta_{2\\alpha}}"]],
    (2, 7): [["4\\delta_{2\\alpha}"], ["\\ln l_c", "\\log l_c", "\\ln(l_c)"]],
    # Task 3 (2309.15922): EFT dispersion null constraints at m+n=4 (3 steps)
    (3, 0): [["=1}", "= 1"]],
    (3, 1): [["=2}", "= 2"]],
    (3, 2): [[")=4}", "_{m+n=4}=4}", "n_{\\mathrm{null}}=9-5=4"]],
    # Task 4 (2111.01267): Cosmological Boltzmann freeze-in & moment hierarchy (4 steps)
    (4, 0): [["\\partial_x f=0", "f(q,x)=f(q,0)"]],
    (4, 1): [["\\delta(q-q_0)"], ["=q_0"]],
    (4, 2): [["y_2(x)}{2}", "y_2/2"], ["3y_2-y_3"], ["12y_2-y_4"]],
    (4, 3): [["12+(q_0^2-12)", "12 + (q_0^2 - 12)"], ["3+(q_0-3)", "3 + (q_0 - 3)"]],
    # Task 5 (2404.07704): 2x2 matrix exponential & Frechet derivative coefficients (3 steps)
    (5, 0): [["-d\\,q", "-dq", "-d q"], ["p(s)+t\\,q", "p(s)+tq", "p+tq", "p(s) + t q(s)"]],
    (5, 1): [["e^{s\\lambda_+}-e^{s\\lambda_-}", "e^{\\lambda_+ s}-e^{\\lambda_- s}", "e^{\\lambda_+s}-e^{\\lambda_-s}"], ["\\lambda_+-\\lambda_-", "\\lambda_+ - \\lambda_-"]],
    (5, 2): [["b(eu+ue)", "b=c", "c=b"], ["p,q", "p(s)", "a_s"]],
    # Task 6 (1105.6063): Nested harmonic sums & Catalan/zeta(3) limits (3 steps)
    (6, 0): [["s_{1,2}(n)+s_{2,1}(n)-c(n)"]],
    (6, 1): [["\\frac{\\pi}{4}-1", "\\pi/4-1"], ["g-1"], ["\\frac{7}{8}\\zeta(3)-1", "7\\zeta(3)/8-1"]],
    (6, 2): [["\\frac{\\pi g}{4}-\\frac{\\pi}{4}-g+\\frac{7}{8}\\zeta(3)"]],
    # Task 7 (0000.0002): Higgs & W-boson positivity bounds (2 steps)
    (7, 0): [["c_{s,0}"], ["c_{s,0}+c_{s,2}", "c_{s,0} + c_{s,2}"], ["c_{s,0}+c_{s,1}+c_{s,2}", "c_{s,0} + c_{s,1} + c_{s,2}"]],
    (7, 1): [["c_{t,2}"], ["4c_{t,1}+c_{t,2}", "4 c_{t,1} + c_{t,2}"], ["16c_{t,0}", "16 c_{t,0}"]],
    # Task 8 (2307.08563): Hilbert series & Diophantine operator counting (5 steps)
    (8, 0): [["d_{\\rm tot}"], ["b_0+b_1"]],
    (8, 1): [["=4}", "=4."]],
    (8, 2): [["=3}", "=3."]],
    (8, 3): [["=8}", "=8."]],
    (8, 4): [["=14}", "=14."]],
    # Task 9 (2505.18074): BFKL convolution operator & digamma eigenvalue (4 steps)
    (9, 0): [["\\cos(\\nu t)-1"], ["\\sinh(t/2)", "\\sinh(|t|/2)"]],
    (9, 1): [["\\psi\\!\\left(\\frac{1}{2}+i\\nu\\right)", "\\psi(\\frac{1}{2}+i\\nu)"]],
    (9, 2): [["\\chi_1", "\\chi_{k_1}"], ["\\sinh(t/2)", "\\sinh(|t|/2)"]],
    (9, 3): [["\\sech^2(\\pi\\nu)", "\\operatorname{sech}^2(\\pi\\nu)", "\\tanh^2(\\pi\\nu)"], ["\\chi_0''(\\nu)"]],
    # Task 10 (1801.04273): Cohomology of configuration spaces & Bockstein operator (4 steps)
    (10, 0): [["a_n^\\bullet", "a_{n-1}^\\bullet", "a_{n-1}"], ["b_n^\\bullet", "b_n"]],
    (10, 1): [["n_{k-1}, 1, n_k", "n_{k-1},1,n_k", "1, n_k", "1,n_k", "2(-1)^{n_{i-1}}"]],
    (10, 2): [["= 0", "=0", "annihilates"], ["n-m+1"]],
    (10, 3): [["b_p(n,r)", "b_p(n, r)"], ["b_p(n-1, r-2)", "b_p(n-1,r-2)"], ["b'_p", "b_p'"]],
    # Task 11 (0000.0004): Super-sample covariance (SSC) & Gaussian window (3 steps)
    (11, 0): [["\\sigma_b^2"], ["r(k_i)p(k_i)", "r(k_i) p(k_i)", "o_i'"]],
    (11, 1): [["8\\pi^{3/2}"], ["r^2+q_0^{-2}", "r^2 + q_0^{-2}", "1/q_0^2", "q_0^{-2}"]],
    (11, 2): [["\\gamma", "(n+3)/2", "\\frac{n+3}{2}"], ["1+k_i^2/k_*^2", "k_i^2/k_*^2"]],
    # Task 12 (0011047): Running-coupling BFKL diffusion & Airy quantization (4 steps)
    (12, 0): [["\\chi_0 y", "\\chi_0y"], ["(t-t_0)^2"], ["\\chi_2 y", "\\chi_2y"]],
    (12, 1): [["\\frac{\\chi_2}{2b}", "\\chi_2/(2b)", "\\chi_2/2b", "\\frac{\\chi_2}{2}"], ["\\frac{\\chi_0}{b}", "\\chi_0/(b\\omega)", "\\chi_0-b\\omega t"]],
    (12, 2): [["a_n"], ["\\chi_0^{3/2}", "b^{3/2}", "a_n|^3", "(-a_n)^{3/2}"], ["\\operatorname{ai}", "\\mathrm{ai}"]],
    (12, 3): [["\\mathrm{ai}", "\\operatorname{ai}"], ["\\omega^{-2/3}", "\\omega^{2/3}", "s_0"]],
    # Task 13 (2310.20045): 9-step Picard group of Hurwitz stack H_{r,g,n} (9 steps)
    (13, 0): [["(r-2)\\binom{n}{2}+n", "(r-2)\\tbinom{n}{2}+n"], ["\\mathcal{h}_{r,g,n}", "\\mathcal h_{r,g,n}"]],
    (13, 1): [["\\mathrm{pic}(\\mathcal{h}_{r,g,2})", "\\operatorname{pic}(\\mathcal h_{r,g,2})"], ["\\mathrm{far}", "\\text{far}"]],
    (13, 2): [["2r(rd-1)"], ["r(rd-1)"]],
    (13, 3): [["2r(rd-1)"], ["r(rd-1)"]],
    (13, 4): [["2r(rd-1)", "torsion group"]],
    (13, 5): [["2r(rd-1)"], ["\\operatorname{coker}=0", "\\mathrm{coker}=0"]],
    (13, 6): [["\\mathrm{pic}(\\mathcal{h}_{r,g,n})", "\\operatorname{pic}(\\mathcal h_{r,g,n})"], ["\\mathrm{far}", "\\text{far}"]],
    (13, 7): [["r(rd-1)"]],
    (13, 8): [["2r(rd-1)"], ["r(rd-1)"]],
    # Task 14 (0000.0003): Post-measurement qubit polarization & correlation matrix (3 steps)
    (14, 0): [["\\kappa_\\mathcal{a}", "\\kappa_{\\mathcal{a}}", "\\kappa_{\\mathcal a}"], ["9"], ["\\kappa_\\mathcal{d}", "\\kappa_{\\mathcal{d}}", "\\kappa_{\\mathcal d}"]],
    (14, 1): [["\\kappa_\\mathcal{a}", "\\kappa_{\\mathcal{a}}", "\\kappa_{\\mathcal a}", "\\kappa_{\\mathcal d}"], ["\\langle f"]],
    (14, 2): [["9"], ["\\langle f"]],
    # Task 15 (2401.09908): Banana integrals & Picard-Fuchs Euler operators (3 steps)
    (15, 0): [["z(1-z)i_1''", "(1-2z)i_1'"], ["\\theta+\\frac{1}{2}"]],
    (15, 1): [["\\theta^3-z", "(\\theta+\\frac{1}{2})^3"]],
    (15, 2): [["\\theta^4-z", "(\\theta+\\frac{1}{2})^4"]],
    # Task 16 (1304.1806): Canonical epsilon-form differential equations (2 steps)
    (16, 0): [["-1/x", "-1&0", "-1 & 0"], ["1/(1-x)", "1&1", "1 & 1"]],
    (16, 1): [["2/x", "x^2/(1-x)", "x^2 g_1", "x^2g_1", "m_0="]],
    # Task 17 (2004.01171): Quartic matrix model one-cut resolvent & perturbation (4 steps)
    (17, 0): [["g z^2", "gz^2"], ["p z+q", "pz+q", "p z + q"]],
    (17, 1): [["\\sqrt{4-x^2}"], ["2\\pi"]],
    (17, 2): [["gx^2+q_0", "g x^2 + q_0"], ["\\sqrt{a^2-x^2}"]],
    (17, 3): [["3\\sqrt{1+8g}-1"], ["h"]],
    # Task 18 (2010.06947): Harmonic polylogarithms & Picard iteration through O(eps^2) (4 steps)
    (18, 0): [["a_0^2=0", "a_0^2 = 0"], ["i_2+\\epsilon a_0", "i_2 + \\epsilon a_0", "exponential"]],
    (18, 1): [["h_0(z)[a_1,a_0]", "h_0(z)[a_1, a_0]", "1 & 0", "1&0"]],
    (18, 2): [["[a_1,a_0]", "[a_1, a_0]"], ["h_{1,0}(z)", "h_{10}(z)"]],
    (18, 3): [["{li}_2(z)", "li_2(z)"], ["-\\epsilon\\log(1-z)", "-\\epsilon \\log(1-z)"]],
    # Task 19 (0000.0000): Partial-wave unitarity bounds for multi-scalar scattering (3 steps)
    (19, 0): [["\\eta_{ii}=1/2", "\\eta_{ii}=\\frac{1}{2}", "\\le 2", "\\leq 2"], ["\\eta_{ij}=1", "1/4", "1/2"]],
    (19, 1): [["2", "\\le 2", "\\leq 2"]],
    (19, 2): [["upper bound \\(1\\)", "maximum is \\(1\\)", "1/2", "\\frac{1}{2}"]],
}

_STOP = frozenset(
    "the a an is are was were be been being have has had do does did will would "
    "shall should may might must can could of in to for with on at from by about "
    "as into like through after over between out against during without before "
    "under around among what which who whom whose how why where when find show "
    "determine compute calculate derive express evaluate obtain".split()
)


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


def extract_mem0_facts(step_idx: int, bg: str, answer: str) -> list[str]:
    facts = []
    if bg.strip():
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", bg.strip()) if s.strip()]
        for s in sentences[:2]:
            clean = re.sub(r"\s+", " ", s)[:130]
            facts.append(f"[Fact S{step_idx + 1}] {clean}")
    ans_lines = [l.strip() for l in answer.strip().splitlines() if l.strip()]
    for l in ans_lines[-2:]:
        clean = re.sub(r"\s+", " ", l)[:130]
        facts.append(f"[Fact S{step_idx + 1} Result] {clean}")
    return facts[:4]


def retrieve_mem0_facts(facts: list[str], query: str, k: int = 5) -> str:
    if not facts:
        return ""
    q_words = set(re.findall(r"\w+", query.lower())) - _STOP
    scored = []
    for idx, f in enumerate(facts):
        f_words = set(re.findall(r"\w+", f.lower())) - _STOP
        overlap = len(q_words & f_words)
        scored.append((overlap, idx, f))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    chosen = [f for _, _, f in scored[:k]]
    return "## Retrieved Atomic Memory Facts (Mem0 1D top-5)\n" + "\n".join(chosen)


def chunk_text(text: str, chunk_size: int = 450) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    return [clean[i : i + chunk_size] for i in range(0, len(clean), chunk_size)]


def retrieve_bm25_chunks(chunks: list[str], query: str, k: int = 3) -> str:
    if not chunks:
        return ""
    q_words = [w for w in re.findall(r"\w+", query.lower()) if w not in _STOP]
    if not q_words:
        return "\n---\n".join(chunks[:k])
    n_docs = len(chunks)
    doc_tokens = [re.findall(r"\w+", c.lower()) for c in chunks]
    avgdl = sum(len(dt) for dt in doc_tokens) / max(1, n_docs)
    df = {
        w: sum(1 for dt in doc_tokens if w in dt)
        for w in set(q_words)
    }
    k1, b = 1.5, 0.75
    scored = []
    for idx, dt in enumerate(doc_tokens):
        dl = len(dt)
        score = 0.0
        for w in set(q_words):
            tf = dt.count(w)
            if tf == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[w] + 0.5) / (df[w] + 0.5))
            score += idf * (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * dl / max(1.0, avgdl)))
        scored.append((score, idx, chunks[idx]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = [c for _, _, c in scored[:k]]
    return "## Retrieved BM25 Chunks (top-3 x 450 chars)\n" + "\n---\n".join(chosen)


def build_state_packet(mem: Memory, query: str) -> str:
    return mem.render_packet(query)


def build_single_call_full_context(
    mem: Memory, query: str, packet: str | None = None
) -> str:
    pkt = packet if packet is not None else build_state_packet(mem, query)
    found = call_tool(
        mem,
        "memory",
        {"action": "find", "query": query, "full": True},
    )
    return f"{pkt}\n\n## Retrieved Memory Notes (memory_find query full=True)\n{found}"


def build_active_consecutive_context(
    mem: Memory, query: str, packet: str | None = None
) -> str:
    pkt = packet if packet is not None else build_state_packet(mem, query)
    hits = mem.find(query, k=4) if query else []
    state_items = mem.state()
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
    return f"{pkt}\n\n## Expanded Memory Notes (memory_find ids full=True)\n{expanded}"


def run_retention_diagnostic(tasks: list[dict]) -> dict:
    stats = {
        "no_memory": {"recalls": [], "chars": []},
        "mem0_style_extracted_facts": {"recalls": [], "chars": []},
        "bm25_chunk_rag": {"recalls": [], "chars": []},
        "agent_memory_passive_state": {"recalls": [], "chars": []},
        "agent_memory_headlines_only": {"recalls": [], "chars": []},
        "agent_memory_single_call_full": {"recalls": [], "chars": []},
        "agent_memory_active_consecutive": {"recalls": [], "chars": []},
    }

    for task in tasks:
        tmpdir = Path(tempfile.mkdtemp(prefix=f"ma_diag_{task['id']}_"))
        try:
            mem = Memory(tmpdir / ".agent_memory")
            bgs = task["backgrounds"]
            qs = task["questions"]
            ans = task["answers"]
            bg0_exprs = extract_latex_expressions(bgs[0])
            mem0_facts: list[str] = []
            bm25_chunks: list[str] = []
            prev_summary_id: str | None = None

            for step_idx in range(len(qs)):
                q = qs[step_idx]
                bg = bgs[step_idx]
                if step_idx > 0:
                    prior_exprs = list(bg0_exprs)
                    for prev_a in ans[:step_idx]:
                        prior_exprs.extend(extract_latex_expressions(prev_a)[:8])
                    target_exprs = list(dict.fromkeys(prior_exprs))

                    packet = build_state_packet(mem, q)
                    ctx_none = f"{bg}\n{q}"
                    ctx_mem0 = f"{retrieve_mem0_facts(mem0_facts, q)}\n{bg}\n{q}"
                    ctx_bm25 = f"{retrieve_bm25_chunks(bm25_chunks, q)}\n{bg}\n{q}"
                    ctx_passive = f"{packet}\n{bg}\n{q}"
                    headlines = call_tool(
                        mem, "memory", {"action": "find", "query": q, "full": False}
                    )
                    ctx_headlines = f"{packet}\n{headlines}\n{bg}\n{q}"
                    ctx_single_full = (
                        f"{build_single_call_full_context(mem, q, packet=packet)}\n{bg}\n{q}"
                    )
                    ctx_active = (
                        f"{build_active_consecutive_context(mem, q, packet=packet)}\n{bg}\n{q}"
                    )

                    for arm_name, ctx in [
                        ("no_memory", ctx_none),
                        ("mem0_style_extracted_facts", ctx_mem0),
                        ("bm25_chunk_rag", ctx_bm25),
                        ("agent_memory_passive_state", ctx_passive),
                        ("agent_memory_headlines_only", ctx_headlines),
                        ("agent_memory_single_call_full", ctx_single_full),
                        ("agent_memory_active_consecutive", ctx_active),
                    ]:
                        if target_exprs:
                            hits = sum(1 for expr in target_exprs if expr in ctx)
                            recall = hits / len(target_exprs)
                        else:
                            recall = 1.0
                        stats[arm_name]["recalls"].append(recall)
                        stats[arm_name]["chars"].append(len(ctx))

                mem0_facts.extend(extract_mem0_facts(step_idx, bg, ans[step_idx]))
                if bg.strip():
                    bm25_chunks.extend(chunk_text(bg))
                bm25_chunks.extend(chunk_text(f"Q: {q}\nA: {ans[step_idx]}"))

                if bg.strip():
                    mem.save(
                        f"Paper Setup ({task['paper_name']}):\n{bg}",
                        {"goal": True, "step": step_idx},
                        agent="phys_agent",
                    )
                mem.save(
                    f"Subtask {step_idx + 1} Derivation:\nQ: {q}\nResult: {ans[step_idx]}",
                    {"step": step_idx + 1},
                    agent="phys_agent",
                )
                if step_idx >= 1:
                    if prev_summary_id:
                        mem.delete(prev_summary_id)
                    distilled_steps = []
                    for s_i in range(step_idx + 1):
                        exprs_i = ", ".join(
                            f"${e}$" for e in extract_latex_expressions(ans[s_i])[:8]
                        )
                        distilled_steps.append(
                            f"Step {s_i + 1} key equations: {exprs_i}\n"
                            f"Conclusion: {ans[s_i][:450]}"
                        )
                    prev_summary_id = mem.save(
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
    s = re.sub(r"\\[dt]?frac\s*([0-9a-zA-Z])\s*([0-9a-zA-Z])", r"\\frac{\1}{\2}", s)
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
    bg_block = (
        background.strip()
        if background.strip()
        else "(None provided in this session; rely on <memory_context> from prior sessions.)"
    )
    mem_block = (
        memory_context.strip()
        if memory_context.strip()
        else "(No persistent memory available.)"
    )
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


def run_single_task_chain(
    task: dict,
    arm: str,
    work_root: Path,
    seed_step0: dict | None = None,
) -> dict:
    """Execute one full multi-session task chain (Subtask 0 -> Subtask K-1) for `arm`."""
    task_id = task["id"]
    bgs = task["backgrounds"]
    qs = task["questions"]
    ans = task["answers"]
    n_steps = len(qs)

    tmpdir = work_root / f"task_{task_id}_{arm}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    mem = Memory(tmpdir / ".agent_memory")
    mem0_facts: list[str] = []
    bm25_chunks: list[str] = []
    prev_summary_id: str | None = None

    step_results = []
    for step_idx in range(n_steps):
        q = qs[step_idx]
        bg = bgs[step_idx]

        if step_idx == 0 and seed_step0 is not None:
            response_text = seed_step0["response_excerpt"]
            grade = grade_subtask_response(task_id, 0, response_text, ans[0])
            step_results.append(
                {
                    "step": 1,
                    "passed": grade["passed"],
                    "rubric_score": grade["rubric_score"],
                    "expr_recall": grade["expr_recall"],
                    "admitted_missing_context": grade["admitted_missing_context"],
                    "prompt_chars": seed_step0["prompt_chars"],
                    "memory_chars": 0,
                    "latency_s": seed_step0["latency_s"],
                    "response_excerpt": response_text,
                }
            )
        else:
            if step_idx == 0 or arm == "no_memory":
                mem_ctx = ""
            elif arm == "mem0_style_extracted_facts":
                mem_ctx = retrieve_mem0_facts(mem0_facts, q)
            elif arm == "bm25_chunk_rag":
                mem_ctx = retrieve_bm25_chunks(bm25_chunks, q)
            elif arm == "agent_memory_passive_state":
                mem_ctx = build_state_packet(mem, q)
            elif arm == "agent_memory_single_call_full":
                mem_ctx = build_single_call_full_context(mem, q)
            elif arm == "agent_memory_active_consecutive":
                mem_ctx = build_active_consecutive_context(mem, q)
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

        # Update persistent memory after session completes
        if arm == "mem0_style_extracted_facts":
            mem0_facts.extend(extract_mem0_facts(step_idx, bg, response_text))
        elif arm == "bm25_chunk_rag":
            if bg.strip():
                bm25_chunks.extend(chunk_text(bg))
            bm25_chunks.extend(chunk_text(f"Q: {q}\nA: {response_text[-800:]}"))
        elif arm.startswith("agent_memory_"):
            if bg.strip():
                mem.save(
                    f"Paper Setup ({task['paper_name']}):\n{bg}",
                    {"goal": True, "step": step_idx + 1},
                    agent="phys_agent",
                )
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
            if (
                arm in ("agent_memory_single_call_full", "agent_memory_active_consecutive")
                and step_idx >= 1
            ):
                if prev_summary_id:
                    mem.delete(prev_summary_id)
                prior_summaries = []
                for prev_idx, sr in enumerate(step_results):
                    prior_summaries.append(
                        f"Step {prev_idx + 1}: {sr['response_excerpt'][-300:]}"
                    )
                prev_summary_id = mem.save(
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


def export_official_memoryarena_format(
    arm_name: str,
    arm_runs: list[dict],
    tasks_by_id: dict[int, dict],
) -> dict:
    """Write `result.jsonl` per paper and compute `all_results.json` using the exact
    formulas from `ZexueHe/MemoryArena/env/env_systems/formal_reasoning_env/eval.py`.
    """
    method_dir = OFFICIAL_OUT_DIR / arm_name
    method_dir.mkdir(parents=True, exist_ok=True)

    paper_passrate = {}
    paper_passrate_at_k = [0] * 20
    paper_count_at_k = [0] * 20
    min_length = 100000

    for tr in arm_runs:
        tid = tr["task_id"]
        task = tasks_by_id[tid]
        paper_key = task["paper_name"]
        paper_dir = method_dir / paper_key
        paper_dir.mkdir(parents=True, exist_ok=True)

        steps = tr["steps"]
        length = len(steps)
        min_length = min(min_length, length)
        correct_count = 0
        tottime = []
        memorytokens = []

        jsonl_lines = []
        for s_idx, s_obj in enumerate(steps):
            is_correct = bool(s_obj["passed"])
            if is_correct:
                correct_count += 1
                paper_passrate_at_k[s_idx] += 1
            paper_count_at_k[s_idx] += 1
            tottime.append(float(s_obj.get("latency_s", 0.0)))
            # Estimate gpt-4o-mini tokens (~4 chars/token)
            mem_toks = int(round(s_obj.get("memory_chars", 0) / 4.0))
            memorytokens.append(mem_toks)

            log_entry = {
                "query_id": s_idx,
                "query": task["questions"][s_idx],
                "memory_context": f"<{s_obj.get('memory_chars', 0)} chars>",
                "response": s_obj.get("response_excerpt", ""),
                "is_correct": is_correct,
                "time": float(s_obj.get("latency_s", 0.0)),
                "ground_truth": task["answers"][s_idx],
                "judge_result": {
                    "feedback": "yes" if is_correct else "no",
                    "is_correct": is_correct,
                },
            }
            jsonl_lines.append(json.dumps(log_entry, ensure_ascii=False))

        (paper_dir / "result.jsonl").write_text(
            "\n".join(jsonl_lines) + "\n", encoding="utf-8"
        )

        is_paper_correct = bool(steps[-1]["passed"])
        paper_passrate[paper_key] = {
            "progress_score": correct_count / length,
            "avg_length": sum(memorytokens) / len(memorytokens),
            "max_length": max(memorytokens),
            "avg_time": sum(tottime) / len(tottime),
            "data_time": sum(tottime),
            "is_paper_correct": is_paper_correct,
        }

    ps_list = [v["progress_score"] for v in paper_passrate.values()]
    passrate_list = [1.0 if v["is_paper_correct"] else 0.0 for v in paper_passrate.values()]
    avgl_list = [v["avg_length"] for v in paper_passrate.values()]
    avgt_list = [v["avg_time"] for v in paper_passrate.values()]
    maxll_list = [v["max_length"] for v in paper_passrate.values()]
    datatime_list = [v["data_time"] for v in paper_passrate.values()]

    prate = []
    cummulative_prate = []
    for k in range(len(paper_passrate_at_k)):
        if paper_count_at_k[k] > 0:
            prate.append(paper_passrate_at_k[k] / paper_count_at_k[k])
            pr_sofar = sum(paper_passrate_at_k[: k + 1])
            cnt_sofar = sum(paper_count_at_k[: k + 1])
            cummulative_prate.append(pr_sofar / cnt_sofar)

    all_results = {
        "overall_average_passrate": round(sum(passrate_list) / len(passrate_list), 4),
        "avg_progress_score": round(sum(ps_list) / len(ps_list), 4),
        "average_session_time": round(sum(avgt_list) / len(avgt_list), 2),
        "average_memory_length": round(sum(avgl_list) / len(avgl_list), 1),
        "average_task_time": round(sum(datatime_list) / len(datatime_list), 2),
        "memory_length": float(max(maxll_list)),
        "min_k": min_length,
        "num_papers_evaluated": len(paper_passrate),
        "total_subtasks_evaluated": sum(paper_count_at_k),
        "passrate_at_k": [round(float(p), 4) for p in prate],
        "cummulative_passrate_at_k": [round(float(p), 4) for p in cummulative_prate],
        "passrate_at_min_k": [round(float(p), 4) for p in prate[:min_length]],
        "cummulative_passrate_at_min_k": [
            round(float(p), 4) for p in cummulative_prate[:min_length]
        ],
    }
    (method_dir / "all_results.json").write_text(
        json.dumps(all_results, indent=4), encoding="utf-8"
    )
    return all_results


def summarize_runs(arm_runs: list[dict]) -> dict:
    arm_runs = sorted(arm_runs, key=lambda x: x["task_id"])
    avg_sr = sum(r["sr"] for r in arm_runs) / len(arm_runs)
    avg_ps = sum(r["ps"] for r in arm_runs) / len(arm_runs)
    sr_at_k = {}
    for k in (1, 2, 3, 4):
        eligible = [r["steps"][k - 1]["passed"] for r in arm_runs if len(r["steps"]) >= k]
        if eligible:
            sr_at_k[f"step_{k}"] = round(sum(eligible) / len(eligible), 4)
    avg_mem_chars = sum(
        s["memory_chars"] for r in arm_runs for s in r["steps"][1:]
    ) / max(1, sum(len(r["steps"]) - 1 for r in arm_runs))
    return {
        "sr": round(avg_sr, 4),
        "ps": round(avg_ps, 4),
        "sr_at_k": sr_at_k,
        "avg_memory_chars_injected": int(round(avg_mem_chars)),
        "tasks": arm_runs,
    }


def main() -> None:
    print("Downloading/loading MemoryArena (ICML 2026) `formal_reasoning_phys` dataset...")
    tasks = download_dataset()
    tasks_by_id = {t["id"]: t for t in tasks}
    print(
        f"Loaded {len(tasks)} multi-session physics tasks "
        f"({sum(len(t['questions']) for t in tasks)} subtasks)."
    )

    cached_data = {}
    if RESULTS_PATH.exists():
        cached_data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    print("\n1. Running Full-Dataset Equation & Symbol Retention Diagnostic (20 tasks, 86 subtasks)...")
    if cached_data.get("retention_diagnostic_all_20_tasks") and "--recalc-diag" not in sys.argv:
        retention_summary = cached_data["retention_diagnostic_all_20_tasks"]
    else:
        retention_summary = run_retention_diagnostic(tasks)
    for arm, m in retention_summary.items():
        print(
            f"  - {arm:32s}: recall={m['avg_equation_recall']*100:5.1f}% | "
            f"avg_prompt={m['avg_prompt_chars']:5d} chars (~{m['avg_prompt_tokens_est']} toks)"
        )

    # Load existing task runs by (arm, task_id)
    cached_by_arm_task: dict[tuple[str, int], dict] = {}
    for section_key in ("live_llm_benchmark_8_tasks", "full_20_tasks_benchmark"):
        sec = cached_data.get(section_key, {})
        for arm_name, arm_obj in sec.items():
            for tr in arm_obj.get("tasks", []):
                tid = tr["task_id"]
                gt_answers = tasks_by_id[tid]["answers"]
                for s_idx, step_obj in enumerate(tr["steps"]):
                    grade = grade_subtask_response(
                        tid, s_idx, step_obj["response_excerpt"], gt_answers[s_idx]
                    )
                    step_obj["passed"] = grade["passed"]
                    step_obj["rubric_score"] = grade["rubric_score"]
                    step_obj["expr_recall"] = grade["expr_recall"]
                    step_obj["admitted_missing_context"] = grade["admitted_missing_context"]
                passed_count = sum(1 for s in tr["steps"] if s["passed"])
                tr["ps"] = round(passed_count / tr["num_steps"], 4)
                tr["sr"] = 1.0 if tr["steps"][-1]["passed"] else 0.0
                cached_by_arm_task[(arm_name, tid)] = tr

    # Full 20-task evaluation for `agent_memory_single_call_full`, `agent_memory_passive_state`, and `no_memory`
    full_20_arms = [
        "no_memory",
        "agent_memory_passive_state",
        "agent_memory_single_call_full",
    ]
    missing_jobs: list[tuple[dict, str]] = []
    for tid in ALL_20_TASK_IDS:
        for arm in full_20_arms:
            if (arm, tid) not in cached_by_arm_task:
                missing_jobs.append((tasks_by_id[tid], arm))

    if missing_jobs:
        work_root = Path(tempfile.mkdtemp(prefix="memoryarena_20tasks_"))
        try:
            # First run step 0 once per missing task_id so all arms share identical Session 1 seed
            missing_tids = sorted({t["id"] for t, _ in missing_jobs})
            step0_seeds: dict[int, dict] = {}
            for tid in ALL_20_TASK_IDS:
                for arm in full_20_arms:
                    if (arm, tid) in cached_by_arm_task:
                        step0_seeds[tid] = cached_by_arm_task[(arm, tid)]["steps"][0]
                        break

            need_step0_tids = [tid for tid in missing_tids if tid not in step0_seeds]
            if need_step0_tids:
                print(
                    f"\n2a. Running Session 1 (Step 0) seeds for {len(need_step0_tids)} new tasks in parallel..."
                )
                with ThreadPoolExecutor(max_workers=16) as pool:
                    fut_map = {}
                    for tid in need_step0_tids:
                        t = tasks_by_id[tid]
                        prompt0 = build_subtask_prompt(
                            t["paper_name"], 0, len(t["questions"]), t["backgrounds"][0], t["questions"][0], ""
                        )
                        call_dir = work_root / f"seed_step0_task_{tid}"
                        fut = pool.submit(
                            worker_common.complete, prompt0, call_dir, timeout=95, model="gpt-5.6-sol"
                        )
                        fut_map[fut] = (tid, prompt0)
                    for fut in as_completed(fut_map):
                        tid, prompt0 = fut_map[fut]
                        res = fut.result()
                        rtext = res.get("text") or ""
                        grade0 = grade_subtask_response(tid, 0, rtext, tasks_by_id[tid]["answers"][0])
                        step0_seeds[tid] = {
                            "step": 1,
                            "passed": grade0["passed"],
                            "rubric_score": grade0["rubric_score"],
                            "expr_recall": grade0["expr_recall"],
                            "admitted_missing_context": grade0["admitted_missing_context"],
                            "prompt_chars": len(prompt0),
                            "memory_chars": 0,
                            "latency_s": round(float(res.get("wall_s") or 12.0), 2),
                            "response_excerpt": rtext[-400:],
                        }
                        print(f"  [Seed Step 1] Task {tid:2d} -> passed={grade0['passed']}")

            print(
                f"\n2b. Running {len(missing_jobs)} remaining multi-session task chains across all 20 tasks..."
            )
            with ThreadPoolExecutor(max_workers=18) as pool:
                futures = {
                    pool.submit(
                        run_single_task_chain,
                        task,
                        arm,
                        work_root,
                        step0_seeds.get(task["id"]),
                    ): (task["id"], arm)
                    for task, arm in missing_jobs
                }
                for fut in as_completed(futures):
                    tid, arm = futures[fut]
                    res = fut.result()
                    cached_by_arm_task[(arm, tid)] = res
                    print(
                        f"  [Done 20-Task] Task {tid:2d} ({arm:30s}) -> "
                        f"SR={res['sr']:.0f}, PS={res['ps']*100:5.1f}% "
                        f"({sum(1 for s in res['steps'] if s['passed'])}/{res['num_steps']} steps)"
                    )
        finally:
            shutil.rmtree(work_root, ignore_errors=True)

    # Assemble 8-task controlled comparison (all 6 arms)
    all_6_arms = [
        "no_memory",
        "mem0_style_extracted_facts",
        "bm25_chunk_rag",
        "agent_memory_passive_state",
        "agent_memory_single_call_full",
        "agent_memory_active_consecutive",
    ]
    live_8_summary = {}
    for arm in all_6_arms:
        runs_8 = [
            cached_by_arm_task[(arm, tid)]
            for tid in CONTROLLED_8_TASK_IDS
            if (arm, tid) in cached_by_arm_task
        ]
        if runs_8:
            live_8_summary[arm] = summarize_runs(runs_8)

    # Assemble full 20-task official evaluation & export official MemoryArena `all_results.json`
    full_20_summary = {}
    official_eval_outputs = {}
    for arm in full_20_arms:
        runs_20 = [
            cached_by_arm_task[(arm, tid)]
            for tid in ALL_20_TASK_IDS
            if (arm, tid) in cached_by_arm_task
        ]
        full_20_summary[arm] = summarize_runs(runs_20)
        official_eval_outputs[arm] = export_official_memoryarena_format(
            arm, runs_20, tasks_by_id
        )

    output = {
        "benchmark": "MemoryArena (ICML 2026, arXiv:2602.16313) — Formal Reasoning (Physics)",
        "dataset_url": HF_URL,
        "total_dataset_tasks": len(tasks),
        "total_dataset_subtasks": sum(len(t["questions"]) for t in tasks),
        "retention_diagnostic_all_20_tasks": retention_summary,
        "full_20_tasks_benchmark": full_20_summary,
        "official_eval_py_all_results_20_tasks": official_eval_outputs,
        "live_llm_benchmark_8_tasks": live_8_summary,
        "published_leaderboard_baselines": PUBLISHED_LEADERBOARD,
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nSaved full benchmark results to {RESULTS_PATH}")
    print("\n=== FULL 20/20 TASKS (86 SUBTASKS) OFFICIAL MEMORYARENA SUMMARY ===")
    for arm, m in official_eval_outputs.items():
        print(
            f"  {arm:32s}: SR (overall_average_passrate) = {m['overall_average_passrate']*100:5.1f}% | "
            f"PS (avg_progress_score) = {m['avg_progress_score']*100:5.1f}% | "
            f"avg_mem_tokens = {m['average_memory_length']}"
        )


if __name__ == "__main__":
    main()

"""Self-updating HTML live memory monitor (browser, no Cursor canvas needed).

Writes into ``.agent_memory/live/``:

- ``index.html`` — UI shell (created once; rewritten when template version changes)
- ``data.js`` — latest graph payload (rewritten on every durable memory event)

Open ``index.html`` in a browser. The page polls ``data.js`` and re-renders when
memory changes — no need to ask the agent for a canvas.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Bump when SHELL_TEMPLATE changes so existing installs pick up the new shell.
SHELL_VERSION = "4"

SHELL_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="agent-memory-live-shell" content="__SHELL_VERSION__">
<title>Memory · live</title>
<style>
  :root {
    --bg: #0c0c0c;
    --panel: #141414;
    --elevated: #1a1a1a;
    --text: #f2f2f2;
    --muted: #a0a0a0;
    --faint: #6b6b6b;
    --stroke: #2a2a2a;
    --stroke-strong: #3a3a3a;
    --accent: #6ea8fe;
    --summary: #e8a54b;
    --step: #6ea8fe;
    --user: #5bc0be;
    --delete: #e88a9a;
    --rail: #2e2e2e;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, sans-serif;
  }
  body {
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 18px 20px 10px;
    border-bottom: 1px solid var(--stroke);
  }
  header h1 {
    margin: 0;
    font-size: 22px;
    font-weight: 650;
    letter-spacing: -0.02em;
  }
  header .branch {
    color: var(--muted);
    font-size: 12px;
  }
  header .status {
    margin-left: auto;
    color: var(--faint);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  header .status.live { color: var(--user); }
  .legend {
    display: flex;
    gap: 14px;
    padding: 10px 20px 0;
    color: var(--muted);
    font-size: 11px;
  }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 99px;
    background: var(--step);
  }
  .dot.summary { background: var(--summary); }
  .dot.user { background: var(--user); }
  .crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 12px 20px 0;
    font-size: 12px;
  }
  .crumbs button {
    background: none;
    border: 0;
    padding: 0;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
  }
  .crumbs button:hover { color: var(--text); }
  .crumbs .here { color: var(--text); font-weight: 600; cursor: default; }
  .crumbs .sep { color: var(--faint); }
  main {
    flex: 1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(280px, 380px);
    gap: 0;
    min-height: 0;
  }
  @media (max-width: 860px) {
    main { grid-template-columns: 1fr; }
    #detail { border-left: 0; border-top: 1px solid var(--stroke); max-height: 42vh; }
  }
  #list {
    overflow: auto;
    padding: 16px 20px 28px;
  }
  .timeline {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 520px;
  }
  .timeline::before {
    content: "";
    position: absolute;
    left: 18px;
    top: 8px;
    bottom: 8px;
    width: 1px;
    background: var(--rail);
    pointer-events: none;
  }
  .card {
    position: relative;
    margin-left: 36px;
    background: var(--elevated);
    border: 1px solid var(--stroke);
    border-radius: 10px;
    padding: 12px 14px 12px 16px;
    cursor: pointer;
    overflow: hidden;
  }
  .card:hover { border-color: var(--stroke-strong); }
  .card.selected {
    border-color: var(--accent);
  }
  .card.current {
    border-color: var(--accent);
  }
  .card .bar {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--step);
  }
  .card.kind-summary .bar { width: 4px; background: var(--summary); }
  .card.kind-user .bar { background: var(--user); }
  .card.kind-delete .bar { background: var(--delete); }
  .card.current .bar { display: none; }
  .card .title {
    font-weight: 600;
    font-size: 13px;
    margin: 0 0 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card .meta {
    color: var(--muted);
    font-size: 11px;
  }
  .card .when {
    margin-top: 6px;
    color: var(--faint);
    font: 11px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  #detail {
    border-left: 1px solid var(--stroke);
    background: var(--panel);
    overflow: auto;
    padding: 18px 20px 28px;
  }
  #detail .empty {
    color: var(--faint);
    margin-top: 8px;
  }
  #detail h2 {
    margin: 0 0 8px;
    font-size: 16px;
    font-weight: 650;
    letter-spacing: -0.01em;
  }
  #detail .meta-line {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 14px;
  }
  #detail .subgraph {
    display: inline-block;
    margin-bottom: 14px;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid var(--stroke-strong);
    background: var(--elevated);
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
  #detail .subgraph:hover { border-color: var(--accent); }
  #detail .body {
    white-space: pre-wrap;
    word-break: break-word;
    font: 12.5px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
    color: var(--text);
  }
  #detail .body a.path,
  #detail .paths a {
    color: var(--accent);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
    word-break: break-all;
  }
  #detail .body a.path:hover,
  #detail .paths a:hover { color: var(--text); }
  #detail .paths {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0 0 14px;
  }
  #detail .paths a {
    display: inline-block;
    max-width: 100%;
    padding: 5px 9px;
    border-radius: 8px;
    border: 1px solid var(--stroke-strong);
    background: var(--elevated);
    text-decoration: none;
    font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--accent);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  #detail .paths a:hover {
    border-color: var(--accent);
    color: var(--text);
  }
</style>
</head>
<body>
<header>
  <h1>Memory</h1>
  <div class="branch" id="branch">—</div>
  <div class="status" id="status">connecting…</div>
</header>
<div class="legend">
  <span><i class="dot summary"></i>summary</span>
  <span><i class="dot"></i>commit</span>
  <span><i class="dot user"></i>user</span>
</div>
<nav class="crumbs" id="crumbs"></nav>
<main>
  <section id="list"></section>
  <aside id="detail"><p class="empty">Select an entry.</p></aside>
</main>
<script src="data.js"></script>
<script>
(function () {
  const POLL_MS = 1500;
  const STORE_KEY = "agent-memory-live-ui-v1";
  const state = loadState();
  let lastHash = "";
  let graph = window.__AGENT_MEMORY_LIVE__ || { nodes: [], edges: [], branches: [] };

  function loadState() {
    try {
      return JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (_) {
      return {};
    }
  }
  function saveState() {
    sessionStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        trail: state.trail || [],
        selected: state.selected || null,
      })
    );
  }
  function byId() {
    const map = {};
    (graph.nodes || []).forEach((n) => { map[n.id] = n; });
    return map;
  }
  function formatWhen(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 19).replace("T", " ");
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const dd = String(d.getUTCDate()).padStart(2, "0");
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    const ss = String(d.getUTCSeconds()).padStart(2, "0");
    return months[d.getUTCMonth()] + " " + dd + ", " + hh + ":" + mm + ":" + ss + " UTC";
  }
  function projectRoot() {
    return String((graph && graph.root) || "").replace(/\/+$/, "");
  }
  function toFileUrl(absPath) {
    const parts = String(absPath).split("/");
    const encoded = parts.map((p, i) => (i === 0 && p === "" ? "" : encodeURIComponent(p))).join("/");
    return "file://" + encoded;
  }
  function resolvePath(raw) {
    let p = String(raw || "").trim().replace(/^['"`]+|['"`]+$/g, "");
    if (!p) return null;
    if (p.startsWith("file://")) {
      try { p = decodeURI(p.replace(/^file:\/\//, "")); } catch (_) {}
    }
    if (p.startsWith("~/")) {
      // Browser cannot expand ~; leave absolute-looking only.
      return null;
    }
    if (p.startsWith("/")) return p;
    const root = projectRoot();
    if (!root) return null;
    if (p.startsWith("./")) p = p.slice(2);
    while (p.startsWith("../")) {
      // Do not climb above project root from relative mentions.
      p = p.slice(3);
    }
    return root + "/" + p.replace(/^\/+/, "");
  }
  function extractPaths(text) {
    const src = String(text || "");
    const found = [];
    const seen = new Set();
    const push = (raw) => {
      const abs = resolvePath(raw);
      if (!abs || seen.has(abs)) return;
      // Skip obvious non-files (lone roots, tiny noise).
      if (abs === "/" || abs.length < 4) return;
      seen.add(abs);
      found.push({ raw: String(raw).trim(), abs });
    };
    const absRe = /(?:^|[\s`'"(=\[]|file:\/\/)(\/(?:Users|home|tmp|var|private|Volumes|opt|Library|mnt|Applications)\/[^\s`'":\]\)]+)/g;
    const relRe = /(?:^|[\s`'"(=\[]|file:\/\/)((?:\.\.?\/)?(?:[\w.-]+\/)+[\w.-]+\.(?:png|jpe?g|gif|webp|svg|pdf|md|py|txt|csv|html?|json|ya?ml|toml|npy|npz|pt|pth|ckpt|onnx|mp4|mov|wav|ipynb))\b/gi;
    let m;
    while ((m = absRe.exec(src))) push(m[1].replace(/[.,;:]+$/, ""));
    while ((m = relRe.exec(src))) push(m[1].replace(/[.,;:]+$/, ""));
    return found;
  }
  function shortPath(abs) {
    const root = projectRoot();
    if (root && abs.startsWith(root + "/")) return abs.slice(root.length + 1);
    const parts = abs.split("/");
    return parts.length > 2 ? "…/" + parts.slice(-2).join("/") : abs;
  }
  function openInFinder(absPath, event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const reveal = graph && graph.reveal;
    if (reveal) {
      fetch(reveal + "?path=" + encodeURIComponent(absPath))
        .then((r) => {
          if (!r.ok) throw new Error("reveal failed");
        })
        .catch(() => openFolderFallback(absPath));
      return;
    }
    openFolderFallback(absPath);
  }
  function openFolderFallback(absPath) {
    // Without the reveal helper, open the parent folder via file:// (Finder on macOS).
    const trimmed = absPath.replace(/\/+$/, "");
    const parent = trimmed.includes("/") ? trimmed.replace(/\/[^/]+$/, "") || "/" : trimmed;
    window.open(toFileUrl(parent + "/"), "_blank");
  }
  function linkifyBody(text) {
    const wrap = document.createElement("div");
    wrap.className = "body";
    const paths = extractPaths(text);
    if (!paths.length) {
      wrap.textContent = text || "";
      return wrap;
    }
    // Replace longest abs paths first so overlaps stay stable.
    const ordered = paths.slice().sort((a, b) => b.raw.length - a.raw.length);
    let remaining = String(text || "");
    const chunks = [];
    while (remaining.length) {
      let best = null;
      let bestAt = -1;
      ordered.forEach((p) => {
        const at = remaining.indexOf(p.raw);
        if (at >= 0 && (bestAt < 0 || at < bestAt)) {
          best = p;
          bestAt = at;
        }
      });
      if (!best || bestAt < 0) {
        chunks.push({ type: "text", value: remaining });
        break;
      }
      if (bestAt > 0) chunks.push({ type: "text", value: remaining.slice(0, bestAt) });
      chunks.push({ type: "path", value: best.raw, abs: best.abs });
      remaining = remaining.slice(bestAt + best.raw.length);
    }
    chunks.forEach((chunk) => {
      if (chunk.type === "text") {
        wrap.appendChild(document.createTextNode(chunk.value));
        return;
      }
      const a = document.createElement("a");
      a.className = "path";
      a.href = toFileUrl(chunk.abs);
      a.textContent = chunk.value;
      a.title = "Show in Finder: " + chunk.abs;
      a.addEventListener("click", (e) => openInFinder(chunk.abs, e));
      wrap.appendChild(a);
    });
    return wrap;
  }
  function truncate(text, max) {
    text = String(text || "");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }
  function visibleNodes(all) {
    const trail = state.trail || [];
    const focusId = trail.length ? trail[trail.length - 1] : null;
    const focus = focusId ? all[focusId] : null;
    if (focus && focus.children && focus.children.length) {
      return focus.children.filter((id) => all[id]).slice().reverse().map((id) => all[id]);
    }
    return (graph.nodes || [])
      .filter((n) => !n.folded)
      .slice()
      .sort((a, b) => String(b.committed_at || "").localeCompare(String(a.committed_at || "")));
  }
  function setStatus(text, live) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.classList.toggle("live", !!live);
  }
  function renderCrumbs(all) {
    const nav = document.getElementById("crumbs");
    nav.innerHTML = "";
    const parts = [{ label: "Live", trail: [] }];
    (state.trail || []).forEach((id, i) => {
      const node = all[id];
      parts.push({
        label: truncate((node && node.title) || id.slice(0, 8), 28),
        trail: (state.trail || []).slice(0, i + 1),
      });
    });
    if (state.selected && all[state.selected]) {
      parts.push({
        label: truncate(all[state.selected].title || state.selected.slice(0, 8), 28),
        trail: null,
        here: true,
      });
    }
    parts.forEach((part, index) => {
      if (index) {
        const sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = "/";
        nav.appendChild(sep);
      }
      if (part.here || part.trail === null) {
        const span = document.createElement("span");
        span.className = "here";
        span.textContent = part.label;
        nav.appendChild(span);
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = part.label;
      if (index === parts.length - 1 && !state.selected) btn.className = "here";
      btn.addEventListener("click", () => {
        state.trail = part.trail;
        state.selected = null;
        saveState();
        render();
      });
      nav.appendChild(btn);
    });
  }
  function renderDetail(all) {
    const aside = document.getElementById("detail");
    const entry = state.selected ? all[state.selected] : null;
    if (!entry) {
      aside.innerHTML = '<p class="empty">Select an entry.</p>';
      return;
    }
    const when = formatWhen(entry.committed_at);
    const kids = (entry.children || []).length;
    let html = "<h2></h2>";
    aside.innerHTML = html;
    aside.querySelector("h2").textContent = entry.title || entry.kind || "entry";
    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = [entry.agent, when, entry.id.slice(0, 8)].filter(Boolean).join(" · ");
    aside.appendChild(meta);
    if (kids) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "subgraph";
      btn.textContent = "Subgraph (" + kids + ")";
      btn.addEventListener("click", () => {
        state.trail = (state.trail || []).concat([entry.id]);
        state.selected = null;
        saveState();
        render();
      });
      aside.appendChild(btn);
    }
    const paths = extractPaths(entry.body || "");
    if (paths.length) {
      const row = document.createElement("div");
      row.className = "paths";
      paths.forEach((p) => {
        const a = document.createElement("a");
        a.href = toFileUrl(p.abs);
        a.textContent = shortPath(p.abs);
        a.title = "Show in Finder: " + p.abs;
        a.addEventListener("click", (e) => openInFinder(p.abs, e));
        row.appendChild(a);
      });
      aside.appendChild(row);
    }
    aside.appendChild(linkifyBody(entry.body || ""));
  }
  function renderList(all) {
    const list = document.getElementById("list");
    const nodes = visibleNodes(all);
    const wrap = document.createElement("div");
    wrap.className = "timeline";
    nodes.forEach((meta) => {
      const card = document.createElement("div");
      card.className = "card kind-" + (meta.kind || "step");
      if (meta.current) card.classList.add("current");
      if (state.selected === meta.id) card.classList.add("selected");
      const bar = document.createElement("div");
      bar.className = "bar";
      card.appendChild(bar);
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = truncate(meta.title || meta.kind || "entry", 48);
      card.appendChild(title);
      const line = document.createElement("div");
      line.className = "meta";
      const bits = [meta.agent];
      if (meta.children && meta.children.length) bits.push(String(meta.children.length));
      line.textContent = bits.filter(Boolean).join(" · ");
      card.appendChild(line);
      const when = document.createElement("div");
      when.className = "when";
      const stamp = formatWhen(meta.committed_at);
      when.textContent = stamp
        ? stamp + " · " + meta.id.slice(0, 8)
        : meta.id.slice(0, 8);
      card.appendChild(when);
      card.addEventListener("click", () => {
        state.selected = meta.id;
        saveState();
        render();
      });
      wrap.appendChild(card);
    });
    list.innerHTML = "";
    list.appendChild(wrap);
  }
  function render() {
    const all = byId();
    const branch =
      ((graph.branches || []).find((b) => b.current) || {}).name || "main";
    document.getElementById("branch").textContent = branch;
    // Drop stale trail / selection after data changes.
    state.trail = (state.trail || []).filter((id) => all[id] && (all[id].children || []).length);
    if (state.selected && !all[state.selected]) state.selected = null;
    saveState();
    renderCrumbs(all);
    renderList(all);
    renderDetail(all);
  }
  function applyData(next) {
    const encoded = JSON.stringify(next || {});
    if (encoded === lastHash) return false;
    lastHash = encoded;
    graph = next || { nodes: [], edges: [], branches: [] };
    window.__AGENT_MEMORY_LIVE__ = graph;
    render();
    return true;
  }
  function poll() {
    const s = document.createElement("script");
    s.src = "data.js?t=" + Date.now();
    s.onload = () => {
      applyData(window.__AGENT_MEMORY_LIVE__);
      setStatus("live · " + new Date().toLocaleTimeString(), true);
      schedule();
    };
    s.onerror = () => {
      setStatus("waiting for data…", false);
      schedule();
    };
    document.head.appendChild(s);
    // Keep DOM tidy.
    setTimeout(() => s.remove(), 5000);
  }
  function schedule() {
    setTimeout(poll, POLL_MS);
  }
  applyData(graph);
  setStatus("live", true);
  schedule();
})();
</script>
</body>
</html>
"""


def default_live_dir(memory) -> Path:
    """Directory for the browser live monitor (inside the memory git dir)."""
    return Path(memory._git_dir) / "live"


def _shell_version(path: Path) -> str | None:
    if not path.exists():
        return None
    match = re.search(
        r'name="agent-memory-live-shell"\s+content="([^"]+)"',
        path.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None


def write_live_html(
    memory,
    path: str | Path | None = None,
    *,
    force_shell: bool = False,
) -> Path:
    """Write / refresh the self-updating HTML live monitor. Returns index path.

    Always rewrites ``data.js``. Creates or upgrades ``index.html`` when missing
    or when ``SHELL_VERSION`` changed (unless ``force_shell`` forces a rewrite).
    """
    from agent_memory.canvas import compact_graph
    from agent_memory.live_reveal import ensure_reveal_server
    from agent_memory.viz import graph_data

    out_dir = Path(path) if path is not None else default_live_dir(memory)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = compact_graph(graph_data(memory))
    payload["root"] = str(Path(memory._path).resolve())
    try:
        payload["reveal"] = ensure_reveal_server(out_dir)
    except Exception:
        payload["reveal"] = None
    encoded = json.dumps(payload, indent=2).replace("</", "<\\/")
    (out_dir / "data.js").write_text(
        f"window.__AGENT_MEMORY_LIVE__ = {encoded};\n",
        encoding="utf-8",
    )

    index = out_dir / "index.html"
    need_shell = (
        force_shell
        or not index.exists()
        or _shell_version(index) != SHELL_VERSION
    )
    if need_shell:
        index.write_text(
            SHELL_TEMPLATE.replace("__SHELL_VERSION__", SHELL_VERSION),
            encoding="utf-8",
        )
    return index

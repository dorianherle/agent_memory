"""Self-updating HTML live memory monitor (browser, no Cursor canvas needed).

Writes into ``.agent_memory/live/``:

- ``index.html`` — UI shell (created once; rewritten when the template version changes)
- ``data.js`` — latest graph payload (rewritten on every durable memory event)
- ``actions.js`` — recent tool calls, including retrievals (rewritten on each call)

Open ``index.html`` in a browser. The page polls and re-renders when memory
changes. Three cards: commit history, the current state packet, and agent
actions. Dirty files come from the reveal helper's ``/working`` endpoint.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

# Bump when SHELL_TEMPLATE changes so existing installs pick up the new shell.
SHELL_VERSION = "23"

SHELL_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="agent-memory-live-shell" content="__SHELL_VERSION__">
<title>agent-memory</title>
<style>
  :root {
    --bg: #f4f7fb;
    --ink: #0f172a;
    --muted: #64748b;
    --faint: #94a3b8;
    --line: #e2e8f0;
    --card: #ffffff;
    --navy: #0b1220;
    --teal: #0f766e;
    --blue: #2563eb;
    --amber: #b45309;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; }
  body {
    background: var(--bg);
    color: var(--ink);
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
  }
  button, input { font: inherit; color: inherit; }
  button { cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 36px 20px 56px; }
  header { text-align: center; }
  .logo { display: inline-flex; filter: drop-shadow(0 8px 16px rgba(15, 23, 42, 0.12)); }
  h1 {
    margin: 14px 0 8px;
    font-size: 40px;
    line-height: 1;
    font-weight: 750;
    letter-spacing: -1.4px;
  }
  .lede {
    margin: 0 auto;
    max-width: 640px;
    color: var(--muted);
    font-size: 15px;
    line-height: 1.55;
  }
  .lede strong { color: var(--ink); font-weight: 650; }
  .status {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    margin: 14px 0 0;
    color: var(--faint);
    font: 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .status::before {
    content: "";
    width: 7px;
    height: 7px;
    border-radius: 99px;
    background: currentColor;
  }
  .status.live { color: var(--teal); }
  .grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr) minmax(0, 0.96fr);
    gap: 16px;
    align-items: stretch;
    margin-top: 28px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    box-shadow: 0 1px 1px rgba(15, 23, 42, 0.04), 0 10px 28px rgba(15, 23, 42, 0.05);
    min-height: 460px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .card-h {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    padding: 13px 14px;
    border-bottom: 1px solid var(--line);
    font: 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .card-h strong { font-weight: 650; }
  .card-h span { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px 0; }
  .tabs[hidden] { display: none; }
  .branch-tab {
    border: 1px solid var(--line);
    background: #fff;
    border-radius: 999px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 650;
    color: #334155;
  }
  .branch-tab.here { background: var(--ink); border-color: var(--ink); color: #fff; }
  #commits { position: relative; flex: 1; overflow: auto; padding: 6px 0 8px; max-height: 560px; }
  .spine {
    position: absolute;
    left: 20px;
    top: 18px;
    bottom: 18px;
    width: 2px;
    background: #e2e8f0;
  }
  .entry { position: relative; }
  .commit {
    display: grid;
    grid-template-columns: 18px 62px max-content minmax(0, 1fr);
    gap: 8px;
    align-items: center;
    width: 100%;
    padding: 7px 14px 7px 12px;
    border: 0;
    background: transparent;
    text-align: left;
  }
  .commit:hover, .commit.on { background: #f8fafc; }
  .dot {
    width: 12px;
    height: 12px;
    border-radius: 99px;
    border: 2px solid var(--ink);
    background: #fff;
    position: relative;
    z-index: 1;
    justify-self: center;
  }
  .dot.goal { border-color: var(--blue); background: #dbeafe; }
  .dot.summary { border-color: var(--teal); background: #ccfbf1; }
  .dot.handoff { border-color: var(--ink); background: var(--ink); }
  .dot.user { border-color: var(--blue); background: #fff; }
  .dot.open { border-color: #d97706; background: #fef3c7; }
  .dot.current { box-shadow: 0 0 0 3px #dbeafe; }
  .sha { font: 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 650; }
  .pill {
    font: 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
    font-weight: 650;
    padding: 3px 6px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: #f8fafc;
    color: var(--ink);
  }
  .pill.goal { color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }
  .pill.summary { color: var(--teal); background: #f0fdfa; border-color: #99f6e4; }
  .pill.open { color: var(--amber); background: #fffbeb; border-color: #fde68a; }
  .pill.user { color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }
  .title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #475569;
    font-size: 13px;
  }
  .msg, .catalog {
    margin: 0 14px 8px 42px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: #f8fafc;
    white-space: pre-wrap;
    font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #334155;
  }
  .catalog { white-space: pre-wrap; }
  .catalog summary { cursor: pointer; font-weight: 650; }
  .files { border-top: 1px solid var(--line); padding: 8px 12px 12px; }
  .file {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    width: 100%;
    margin-top: 6px;
    padding: 7px 8px;
    border: 0;
    border-radius: 8px;
    background: #f8fafc;
    text-align: left;
    font: 12px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .file .path { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #334155; }
  .ok { color: var(--teal); font-weight: 650; white-space: nowrap; }
  .stale {
    color: var(--amber);
    background: #fef3c7;
    border: 1px solid #fde68a;
    border-radius: 6px;
    padding: 1px 6px;
    font-weight: 650;
    white-space: nowrap;
  }
  .state {
    background: var(--navy);
    border-color: var(--navy);
    color: #e2e8f0;
    box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
  }
  .state .card-h { border-bottom-color: #1e293b; }
  .state .card-h span { color: #94a3b8; }
  #state-body { flex: 1; overflow: auto; padding: 6px 0 12px; }
  .sec { display: grid; grid-template-columns: 3px minmax(0, 1fr); gap: 10px; padding: 12px 14px 4px; }
  .bar { border-radius: 99px; background: #64748b; }
  .bar.goal { background: #3b82f6; }
  .bar.steer { background: #64748b; }
  .bar.handoff { background: #2dd4bf; }
  .bar.open { background: #f59e0b; }
  .bar.related { background: #f59e0b; }
  .sec h3 {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin: 0 0 6px;
    font: 650 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #93c5fd;
  }
  .sec.handoff h3 { color: #5eead4; }
  .sec.steer h3 { color: #cbd5e1; }
  .sec.open h3, .sec.related h3 { color: #fcd34d; }
  .bud { color: #64748b; font-weight: 500; }
  .line, .more {
    margin: 0 0 4px;
    font: 12.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #e2e8f0;
  }
  .more { color: #94a3b8; }
  button.id {
    border: 0;
    background: none;
    padding: 0;
    color: #60a5fa;
    font: inherit;
    font-weight: 650;
  }
  button.id:hover { text-decoration: underline; }
  .mark {
    color: #fbbf24;
    font-weight: 650;
  }
  #running[hidden] { display: none; }
  #running { padding: 8px 14px 0; }
  .run {
    margin: 0 0 6px;
    color: var(--muted);
    font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  #action-list { flex: 1; overflow: auto; max-height: 640px; }
  .act { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; }
  .act-top { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
  .tool { font: 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; }
  .tool.find { color: #1d4ed8; }
  .tool.summarize { color: var(--teal); }
  .tool.delete { color: #be123c; }
  .when { color: var(--faint); font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .detail {
    margin: 6px 0 0;
    padding: 6px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #f8fafc;
    color: #334155;
    font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
    overflow-wrap: anywhere;
  }
  .empty { margin: 8px 14px; color: var(--muted); font-size: 13px; }
  .empty.light { color: #94a3b8; }
  @media (max-width: 980px) {
    .grid { grid-template-columns: 1fr; }
    h1 { font-size: 32px; }
    .card { min-height: 0; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo" aria-hidden="true">
      <svg width="56" height="56" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="16" fill="#0B1220"/>
        <path d="M23 18 L43 32 L23 46" fill="none" stroke="#E2E8F0" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="23" cy="18" r="4.4" fill="#0B1220" stroke="#F8FAFC" stroke-width="2.3"/>
        <circle cx="23" cy="46" r="4.4" fill="#0B1220" stroke="#F8FAFC" stroke-width="2.3"/>
        <circle cx="43" cy="32" r="5.3" fill="#2DD4BF"/>
      </svg>
    </div>
    <h1>agent-memory</h1>
    <p class="lede">Local, Git-backed engineering notebook for <strong>multi-session AI agents</strong> — keeping uncut notes, file snapshots, and a bounded state packet across fresh contexts.</p>
    <p class="status" id="status">connecting…</p>
  </header>
  <div class="grid">
    <section class="card" id="history" aria-label="Commit history">
      <div class="card-h"><strong>1. .agent_memory/</strong><span>Git DAG + SHA-256</span></div>
      <div class="tabs" id="branches" hidden></div>
      <div id="commits"></div>
      <div class="files" id="files"></div>
    </section>
    <section class="card state" id="state" aria-label="Memory state">
      <div class="card-h"><strong>2. # MEMORY STATE</strong><span id="state-budget">≤ 1,200 chars</span></div>
      <div id="state-body"></div>
    </section>
    <section class="card" id="actions" aria-label="Agent actions">
      <div class="card-h"><strong>3. Lookup</strong><span>Uncut recall</span></div>
      <div id="running" hidden></div>
      <div id="action-list"></div>
    </section>
  </div>
</div>
<script src="data.js"></script>
<script>
(function () {
  const POLL_MS = 1500;
  const STORE_KEY = "agent-memory-live-ui-v2";
  const TOOLS = {
    find: "memory_find",
    save: "memory_save",
    summarize: "memory_summarize",
    delete: "memory_delete",
    branch: "memory_branch",
    inspect: "memory_inspect",
    inspect_done: "memory_inspect_done",
    open_questions: "memory_open_questions",
    goal_create: "memory_goal_create",
    goal_use: "memory_goal_use"
  };
  let graph = window.__AGENT_MEMORY_LIVE__ || { nodes: [], edges: [], branches: [] };
  let lastHash = "";
  let lastNow = null;
  const state = loadState();

  function loadState() {
    try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}") || {}; }
    catch (_) { return {}; }
  }
  function saveState() {
    sessionStorage.setItem(STORE_KEY, JSON.stringify({
      selected: state.selected || null,
      branch: state.branch || null,
      expanded: state.expanded || []
    }));
  }
  function byId() {
    const map = {};
    (graph.nodes || []).forEach((n) => { map[n.id] = n; });
    return map;
  }
  function isDemo() { return !!(graph && graph.demo); }
  function projectRoot() { return String((graph && graph.root) || "").replace(/\/+$/, ""); }
  function repoName() {
    const parts = projectRoot().split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }
  function firstLine(text) { return String(text || "").trim().split("\n")[0]; }
  function restLines(text) {
    return String(text || "").trim().split("\n").slice(1).map((s) => s.trim()).filter(Boolean).slice(0, 3);
  }
  function shortId(id) { return String(id || "").slice(0, 7); }
  function formatWhen(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function setStatus(text, live) {
    const el = document.getElementById("status");
    const name = repoName();
    el.textContent = name ? (text + " · " + name) : text;
    el.classList.toggle("live", !!live);
  }
  function lineage(tip) {
    const parents = {};
    (graph.edges || []).forEach((e) => {
      if ((e.kind || "parent") !== "parent") return;
      (parents[e.to] = parents[e.to] || []).push(e.from);
    });
    const seen = new Set();
    const stack = tip ? [tip] : [];
    while (stack.length) {
      const id = stack.pop();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      (parents[id] || []).forEach((p) => stack.push(p));
    }
    return seen;
  }
  function focusedBranch() {
    const rows = graph.branches || [];
    if (state.branch && rows.some((b) => b.name === state.branch)) return state.branch;
    const current = rows.find((b) => b.current);
    return (current && current.name) || (rows[0] && rows[0].name) || "";
  }
  function roleOf(n) {
    const p = graph.prompt || {};
    if (p.goal && n.id === p.goal.id) return "goal";
    if (n.kind === "summary") return "summary";
    if (n.kind === "open_question") return "open";
    if (n.kind === "user") return "user";
    const found = p.findings || [];
    if (found.length && n.id === found[found.length - 1].id) return "handoff";
    return "step";
  }
  function rowsFor(name) {
    const all = byId();
    const branch = (graph.branches || []).find((b) => b.name === name);
    const keep = branch && branch.tip ? lineage(branch.tip) : null;
    const nodes = (graph.nodes || []).filter((n) => {
      if (!n || n.kind === "delete" || n.folded) return false;
      if (keep && keep.size && !keep.has(n.id)) return false;
      return true;
    });
    nodes.sort((a, b) => String(a.committed_at || "").localeCompare(String(b.committed_at || "")));
    const out = [];
    nodes.forEach((n) => {
      out.push(n);
      if ((state.expanded || []).indexOf(n.id) >= 0) {
        (n.children || []).forEach((id) => {
          if (all[id]) out.push(Object.assign({ child: true }, all[id]));
        });
      }
    });
    return out;
  }
  function isExpanded(id) { return (state.expanded || []).indexOf(id) >= 0; }
  function toggleExpand(id) {
    const cur = (state.expanded || []).slice();
    const at = cur.indexOf(id);
    if (at >= 0) cur.splice(at, 1);
    else cur.push(id);
    state.expanded = cur;
  }
  function selectId(id) {
    state.selected = id;
    const node = byId()[id];
    if (node && node.folded) {
      (graph.nodes || []).forEach((n) => {
        if ((n.children || []).indexOf(id) >= 0 && !isExpanded(n.id)) toggleExpand(n.id);
      });
    }
    saveState();
    render();
    const hit = document.querySelector('.entry[data-id="' + String(id).replace(/"/g, "") + '"]');
    if (hit) hit.scrollIntoView({ block: "nearest" });
  }
  function renderBranches() {
    const el = document.getElementById("branches");
    const rows = graph.branches || [];
    el.innerHTML = "";
    if (rows.length <= 1) { el.hidden = true; return; }
    el.hidden = false;
    const focus = focusedBranch();
    rows.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "branch-tab" + (b.name === focus ? " here" : "");
      btn.textContent = b.name + (b.current ? " · you" : "");
      btn.addEventListener("click", () => {
        state.branch = b.name;
        saveState();
        render();
      });
      el.appendChild(btn);
    });
  }
  function renderCommits() {
    const box = document.getElementById("commits");
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 28;
    const first = !box.childElementCount;
    box.innerHTML = "";
    const rows = rowsFor(focusedBranch());
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Commits show up here after the first save.";
      box.appendChild(empty);
      return;
    }
    const spine = document.createElement("div");
    spine.className = "spine";
    box.appendChild(spine);
    rows.forEach((n) => {
      const entry = document.createElement("div");
      entry.className = "entry";
      entry.dataset.id = n.id;
      const role = roleOf(n);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "commit" + (state.selected === n.id ? " on" : "");
      const dot = document.createElement("i");
      dot.className = "dot " + role + (n.current ? " current" : "");
      const sha = document.createElement("span");
      sha.className = "sha";
      sha.textContent = shortId(n.id);
      const pill = document.createElement("span");
      pill.className = "pill " + role;
      pill.textContent = role;
      const title = document.createElement("span");
      title.className = "title";
      title.textContent = firstLine(n.title || n.kind || "entry");
      btn.appendChild(dot);
      btn.appendChild(sha);
      btn.appendChild(pill);
      btn.appendChild(title);
      btn.addEventListener("click", () => {
        if ((n.children || []).length) toggleExpand(n.id);
        state.selected = state.selected === n.id ? null : n.id;
        saveState();
        render();
      });
      entry.appendChild(btn);
      if (state.selected === n.id) {
        const msg = document.createElement("div");
        msg.className = "msg";
        msg.textContent = (n.body || n.title || "").trim() || "(empty)";
        entry.appendChild(msg);
        if (n.mcp_tools) {
          const cat = document.createElement("details");
          cat.className = "catalog";
          const sum = document.createElement("summary");
          sum.textContent = "MCP tool catalog";
          const pre = document.createElement("div");
          pre.textContent = n.mcp_tools;
          cat.appendChild(sum);
          cat.appendChild(pre);
          entry.appendChild(cat);
        }
      }
      box.appendChild(entry);
    });
    if (first || atBottom) box.scrollTop = box.scrollHeight;
  }
  function idButton(id) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "id";
    btn.textContent = "[" + shortId(id) + "]";
    btn.addEventListener("click", () => selectId(id));
    return btn;
  }
  function addBlock(parent, tone, title, entry, withBudget) {
    if (!entry || !(entry.text || "").trim()) return;
    const sec = document.createElement("div");
    sec.className = "sec " + tone;
    const bar = document.createElement("i");
    bar.className = "bar " + tone;
    const body = document.createElement("div");
    const h = document.createElement("h3");
    h.textContent = "## " + title;
    if (withBudget) {
      const bud = document.createElement("span");
      bud.className = "bud";
      bud.textContent = String(entry.text || "").length + "c";
      h.appendChild(bud);
    }
    const line = document.createElement("p");
    line.className = "line";
    line.appendChild(idButton(entry.id));
    line.appendChild(document.createTextNode(" " + firstLine(entry.text)));
    if (entry.stale) {
      const mark = document.createElement("span");
      mark.className = "mark";
      mark.textContent = " [stale]";
      line.appendChild(mark);
    }
    body.appendChild(h);
    body.appendChild(line);
    restLines(entry.text).forEach((t) => {
      const more = document.createElement("p");
      more.className = "more";
      more.textContent = t;
      body.appendChild(more);
    });
    sec.appendChild(bar);
    sec.appendChild(body);
    parent.appendChild(sec);
  }
  function addMany(parent, tone, title, entries) {
    const rows = (entries || []).filter((e) => e && (e.text || "").trim());
    if (!rows.length) return;
    const sec = document.createElement("div");
    sec.className = "sec " + tone;
    const bar = document.createElement("i");
    bar.className = "bar " + tone;
    const body = document.createElement("div");
    const h = document.createElement("h3");
    h.textContent = "## " + title;
    body.appendChild(h);
    rows.forEach((entry) => {
      const line = document.createElement("p");
      line.className = "line";
      line.appendChild(idButton(entry.id));
      line.appendChild(document.createTextNode(" " + firstLine(entry.text)));
      if (entry.stale) {
        const mark = document.createElement("span");
        mark.className = "mark";
        mark.textContent = " [stale]";
        line.appendChild(mark);
      }
      body.appendChild(line);
    });
    sec.appendChild(bar);
    sec.appendChild(body);
    parent.appendChild(sec);
  }
  function renderState() {
    const body = document.getElementById("state-body");
    const budget = document.getElementById("state-budget");
    const p = graph.prompt || {};
    const injected = String(p.injected || "");
    budget.textContent = injected ? (injected.length + " / 1,200") : "≤ 1,200 chars";
    body.innerHTML = "";
    const found = p.findings || [];
    if (!p.goal && !p.steering && !(p.questions || []).length && !found.length && !injected) {
      const empty = document.createElement("p");
      empty.className = "empty light";
      empty.textContent = "The first request becomes the goal.";
      body.appendChild(empty);
      return;
    }
    addBlock(body, "goal", "Goal", p.goal, true);
    addBlock(body, "steer", "Steering", p.steering, false);
    if (found.length) addBlock(body, "handoff", "Handoff", found[found.length - 1], true);
    addMany(body, "open", "Open", p.questions || []);
    if (found.length > 1) addMany(body, "related", "Related", found.slice(0, -1).slice(-3));
  }
  function toolLabel(action) {
    return TOOLS[action] || action || "memory";
  }
  function renderActions() {
    const box = document.getElementById("action-list");
    const rows = (graph.actions || []).slice().reverse();
    box.innerHTML = "";
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Retrievals show up here when the agent calls memory_find.";
      box.appendChild(empty);
      return;
    }
    rows.forEach((row) => {
      const item = document.createElement("div");
      item.className = "act";
      const top = document.createElement("div");
      top.className = "act-top";
      const name = document.createElement("span");
      name.className = "tool " + (row.action || "");
      name.textContent = toolLabel(row.action);
      const when = document.createElement("time");
      when.className = "when";
      when.textContent = formatWhen(row.at);
      top.appendChild(name);
      top.appendChild(when);
      item.appendChild(top);
      if (row.detail) {
        const detail = document.createElement("div");
        detail.className = "detail";
        detail.textContent = row.detail;
        item.appendChild(detail);
      }
      box.appendChild(item);
    });
  }
  function toFileUrl(absPath) {
    return "file://" + String(absPath).split("/").map((p, i) => (i === 0 ? p : encodeURIComponent(p))).join("/");
  }
  function openInFinder(absPath, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const reveal = graph && graph.reveal;
    if (reveal) {
      fetch(reveal + "?path=" + encodeURIComponent(absPath)).catch(() => {
        window.open(toFileUrl(absPath), "_blank");
      });
      return;
    }
    window.open(toFileUrl(absPath), "_blank");
  }
  function fileRow(label, badge, stale, abs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "file";
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = label;
    const mark = document.createElement("span");
    mark.className = stale ? "stale" : "ok";
    mark.textContent = badge;
    btn.appendChild(path);
    btn.appendChild(mark);
    if (abs) btn.addEventListener("click", (e) => openInFinder(abs, e));
    return btn;
  }
  function renderRunning(data) {
    const box = document.getElementById("running");
    box.innerHTML = "";
    const inspecting = data && data.inspecting;
    if (inspecting && inspecting.id) {
      const line = document.createElement("p");
      line.className = "run";
      line.textContent = "inspecting " + shortId(inspecting.id);
      box.appendChild(line);
    }
    ((data && data.processes) || []).forEach((proc) => {
      const line = document.createElement("p");
      line.className = "run";
      line.textContent = proc.command || "running";
      box.appendChild(line);
    });
    box.hidden = !box.childElementCount;
  }
  function renderFiles(data) {
    lastNow = data || null;
    const box = document.getElementById("files");
    box.innerHTML = "";
    if (!data) {
      const wait = document.createElement("p");
      wait.className = "empty";
      wait.textContent = "checking files…";
      box.appendChild(wait);
      return;
    }
    renderRunning(data);
    const files = data.files || [];
    if (!files.length) {
      box.appendChild(fileRow("working tree", "sha256 ✓", false, null));
      return;
    }
    const root = projectRoot();
    files.slice(0, 8).forEach((f) => {
      const rel = String(f.path || "");
      const abs = rel.startsWith("/") ? rel : (root ? root + "/" + rel : rel);
      box.appendChild(fileRow(rel, "[stale]", true, abs));
    });
    if (files.length > 8) {
      const more = document.createElement("p");
      more.className = "empty";
      more.textContent = "+" + (files.length - 8) + " more changed";
      box.appendChild(more);
    }
  }
  function workingEndpoint() {
    if (graph && graph.working) return graph.working;
    const reveal = graph && graph.reveal;
    if (!reveal) return null;
    return String(reveal).replace(/\/reveal\/?$/, "/working");
  }
  function render() {
    const names = new Set((graph.branches || []).map((b) => b.name));
    if (state.branch && !names.has(state.branch)) state.branch = null;
    renderBranches();
    renderCommits();
    renderState();
    renderActions();
    if (lastNow || (graph && graph.now)) renderFiles(lastNow || graph.now);
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
  function loadActions() {
    if (isDemo()) return;
    const s = document.createElement("script");
    s.src = "actions.js?t=" + Date.now();
    s.onload = () => {
      const rows = window.__AGENT_MEMORY_ACTIONS__;
      if (!Array.isArray(rows)) return;
      graph.actions = rows;
      renderActions();
    };
    document.head.appendChild(s);
    setTimeout(() => s.remove(), 4000);
  }
  function pollWorking() {
    if (isDemo()) {
      renderFiles(graph.now || { files: [], processes: [], inspecting: null });
      return;
    }
    const url = workingEndpoint();
    if (!url) {
      renderFiles(null);
      return;
    }
    fetch(url).then((r) => {
      if (!r.ok) throw new Error("working");
      return r.json();
    }).then(renderFiles).catch(() => {
      if (!document.getElementById("files").childElementCount) {
        renderFiles(null);
      }
    });
  }
  function poll() {
    if (isDemo()) return;
    const s = document.createElement("script");
    s.src = "data.js?t=" + Date.now();
    s.onload = () => {
      applyData(window.__AGENT_MEMORY_LIVE__);
      loadActions();
      const inspecting = lastNow && lastNow.inspecting;
      setStatus(inspecting && inspecting.id ? "inspecting" : "live", true);
      pollWorking();
      schedule();
    };
    s.onerror = () => { setStatus("waiting for data…", false); schedule(); };
    document.head.appendChild(s);
    setTimeout(() => s.remove(), 5000);
  }
  function schedule() { setTimeout(poll, POLL_MS); }
  applyData(graph);
  setStatus(isDemo() ? "demo" : "live", true);
  pollWorking();
  if (!isDemo()) { loadActions(); schedule(); }
})();
</script>
</body>
</html>
"""


# ponytail: last 40 tool calls; raise the cap if one session outgrows the card
ACTIONS_CAP = 40


def default_live_dir(memory) -> Path:
    """Directory for the browser live monitor (inside the memory git dir)."""
    return Path(memory._git_dir) / "live"


def _actions_path(live_dir: Path) -> Path:
    return Path(live_dir) / "actions.json"


def load_actions(live_dir: Path) -> list:
    path = _actions_path(live_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)][-ACTIONS_CAP:]


def _write_actions_js(live_dir: Path, rows: list) -> None:
    encoded = json.dumps(rows).replace("</", "<\\/")
    (Path(live_dir) / "actions.js").write_text(
        f"window.__AGENT_MEMORY_ACTIONS__ = {encoded};\n",
        encoding="utf-8",
    )


def record_action(memory, action: str, detail: str = "") -> None:
    """Append one tool call for the live page. Failures are ignored."""
    try:
        live = default_live_dir(memory)
        live.mkdir(parents=True, exist_ok=True)
        rows = load_actions(live)
        rows.append(
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "action": str(action or "")[:40],
                "detail": str(detail or "").replace("\n", " ")[:140],
            }
        )
        rows = rows[-ACTIONS_CAP:]
        _actions_path(live).write_text(
            json.dumps(rows, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_actions_js(live, rows)
    except Exception:
        return


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
    from agent_memory.turn_prompts import attach_turn_prompts
    from agent_memory.viz import graph_data

    out_dir = Path(path) if path is not None else default_live_dir(memory)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = compact_graph(graph_data(memory))
    attach_turn_prompts(payload["nodes"], out_dir)
    payload["prompt"] = memory.prompt_payload()
    payload["root"] = str(Path(memory._path).resolve())
    try:
        payload["reveal"] = ensure_reveal_server(
            out_dir, root=Path(memory._path).resolve()
        )
    except Exception:
        payload["reveal"] = None
    if payload.get("reveal"):
        payload["working"] = str(payload["reveal"]).rsplit("/", 1)[0] + "/working"
    payload["actions"] = load_actions(out_dir)
    _write_actions_js(out_dir, payload["actions"])
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


def demo_payload() -> dict:
    """Sample graph + Now strip so the live shell can be opened without a store."""
    def node(
        nid: str,
        kind: str,
        agent: str,
        title: str,
        body: str,
        at: str,
        *,
        tip: bool = False,
        current: bool = False,
        folded: bool = False,
        branches: list[str] | None = None,
        children: list[str] | None = None,
        color: str = "green",
    ) -> dict:
        return {
            "id": nid,
            "kind": kind,
            "agent": agent,
            "color": color,
            "title": title,
            "body": body,
            "tip": tip,
            "current": current,
            "folded": folded,
            "branches": list(branches or []),
            "committed_at": at,
            "children": list(children or []),
        }

    # main: a1–a5 (polling abandoned). retry forks at a3: b1–b2 (current).
    a1, a2, a3, a4, a5 = "a1b2c3d4", "b2c3d4e5", "c3d4e5f6", "d4e5f6a7", "e5f6a7b8"
    b1, b2 = "f6a7b8c9", "a7b8c9d0"
    c1, c2 = "c9d0e1f2", "d0e1f2a3"
    f1, f2 = "aabbcc11", "bbccdd22"
    now = time.time()
    return {
        "demo": True,
        "root": "/Users/demo/project",
        "nodes": [
            node(
                a1, "user", "user",
                "auth is slow on every login",
                "auth is slow on every login\n\nWhy is /login taking ~2s?",
                "2026-08-17T09:00:00+00:00",
                color="blue",
            ),
            node(
                a2, "step", "agent",
                "session lookup does a full scan",
                "Root cause: src/auth/session.py:88 walks every row.\n"
                "Evidence: EXPLAIN on sessions showed Seq Scan.",
                "2026-08-17T09:08:00+00:00",
            ),
            node(
                a3, "summary", "agent",
                "Auth moved to short-lived tokens; login p95 1.9s → 240ms",
                "Changed: src/auth/session.py, src/auth/tokens.py.\n"
                "Decided: refresh in a cookie, not localStorage (XSS).",
                "2026-08-17T09:40:00+00:00",
                children=[f1, f2],
                color="orange",
            ),
            node(
                f1, "step", "agent",
                "added token table + cookie refresh",
                "src/auth/tokens.py: new Token table, 15m access / 7d refresh.",
                "2026-08-17T09:22:00+00:00",
                folded=True,
            ),
            node(
                f2, "step", "agent",
                "login p95 1.9s → 240ms",
                "Benchmark: locust 50 users, p95 240ms after the index.",
                "2026-08-17T09:35:00+00:00",
                folded=True,
            ),
            node(
                a4, "user", "user",
                "try the polling retry loop",
                "Can we just poll until the session row shows up?",
                "2026-08-17T10:00:00+00:00",
                color="blue",
            ),
            node(
                a5, "step", "agent",
                "polling breaks under concurrent writes",
                "Two logins race; the waiter never sees the commit.\n"
                "Abandoned — branched away to retry.",
                "2026-08-17T10:20:00+00:00",
                tip=True,
                branches=["main"],
            ),
            node(
                b1, "user", "user",
                "what do we want most of the time",
                "what do we want most of the time",
                "2026-08-17T11:00:00+00:00",
                color="blue",
            ),
            node(
                b2, "step", "agent",
                "Most of the time: retrieve, don’t converse.",
                "Default is memory_find, not a chat with the store.\n"
                "Now strip + git-style lanes on the live page.",
                "2026-08-17T11:12:00+00:00",
                tip=True,
                current=True,
                branches=["retry"],
            ),
            node(
                "eef1f2f3", "open_question", "agent",
                "OPEN: is the retry gate sound?",
                "Measure memory_find calls per thinker turn before/after.\n"
                "Rests on the retrieval findings below.",
                "2026-08-17T11:20:00+00:00",
                color="green",
            ),
            node(
                c1, "user", "user",
                "try a vector index instead",
                "What if we embed sessions and skip the table?",
                "2026-08-17T10:30:00+00:00",
                color="blue",
            ),
            node(
                c2, "step", "explore·a1b2c3",
                "embeddings help find, not the login path",
                "Login still needs an exact key. Dead end.",
                "2026-08-17T10:45:00+00:00",
                tip=True,
                branches=["explore"],
                color="purple",
            ),
        ],
        "edges": [
            {"from": a1, "to": a2, "kind": "parent"},
            {"from": a2, "to": a3, "kind": "parent"},
            {"from": a3, "to": a4, "kind": "parent"},
            {"from": a4, "to": a5, "kind": "parent"},
            {"from": a3, "to": b1, "kind": "parent"},
            {"from": b1, "to": b2, "kind": "parent"},
            {"from": b2, "to": "eef1f2f3", "kind": "parent"},
            {"from": a3, "to": c1, "kind": "parent"},
            {"from": c1, "to": c2, "kind": "parent"},
            {"from": f1, "to": f2, "kind": "parent"},
            {"from": a3, "to": f1, "kind": "fold"},
            {"from": f1, "to": f2, "kind": "fold"},
        ],
        "branches": [
            {"name": "main", "tip": a5, "current": False},
            {"name": "retry", "tip": "eef1f2f3", "current": True},
            {"name": "explore", "tip": c2, "current": False},
        ],
        "agents": [
            {"name": "agent", "color": "green"},
            {"name": "user", "color": "blue"},
            {"name": "explore·a1b2c3", "color": "purple"},
        ],
        "prompt": {
            "goal": {
                "id": a1,
                "agent": "user",
                "text": "auth is slow on every login\n\nWhy is /login taking ~2s?",
                "stale": False,
                "depends_on": [],
            },
            "steering": {
                "id": b1,
                "agent": "user",
                "text": "what do we want most of the time",
                "stale": False,
                "depends_on": [],
            },
            "questions": [
                {
                    "id": "eef1f2f3",
                    "agent": "agent",
                    "text": "OPEN: is the retry gate sound?",
                    "stale": False,
                    "depends_on": [b2],
                }
            ],
            "findings": [
                {
                    "id": b2,
                    "agent": "agent",
                    "text": (
                        "Most of the time: retrieve, don’t converse.\n"
                        "Default is memory_find, not a chat with the store."
                    ),
                    "stale": False,
                    "depends_on": [],
                }
            ],
            "injected": (
                "## Goal\n[a1b2c3d4] auth is slow on every login\n\n"
                "## Steering\n[f6a7b8c9] what do we want most of the time\n"
            ),
        },
        "now": {
            "ok": True,
            "files": [
                {"path": "agent_memory/live_html.py", "status": "M", "mtime": now - 12},
                {"path": "agent_memory/live_reveal.py", "status": "M", "mtime": now - 40},
                {
                    "path": "agent_memory/tests/test_live_reveal.py",
                    "status": "A",
                    "mtime": now - 8,
                },
                {"path": "README.md", "status": "M", "mtime": now - 180},
            ],
            "total": 4,
            "vs": "last memory snapshot",
            "processes": [
                {
                    "pid": 66442,
                    "command": ".venv/bin/python -m pytest agent_memory/tests -q",
                    "cwd": "/Users/demo/project",
                    "running_for_ms": 42000,
                    "started_at": "2026-08-17T11:14:00+00:00",
                }
            ],
            "inspecting": None,
        },
        "actions": [
            {
                "at": "2026-08-17T11:12:04+00:00",
                "action": "find",
                "detail": "query=login p95",
            },
            {
                "at": "2026-08-17T11:12:20+00:00",
                "action": "find",
                "detail": "ids=a7b8c9d0",
            },
            {
                "at": "2026-08-17T11:13:01+00:00",
                "action": "save",
                "detail": "Most of the time: retrieve, don't converse.",
            },
        ],
    }


def write_live_demo(path: str | Path) -> Path:
    """Write a self-contained demo page (no memory store, no polling)."""
    encoded = json.dumps(demo_payload(), indent=2).replace("</", "<\\/")
    html = SHELL_TEMPLATE.replace("__SHELL_VERSION__", SHELL_VERSION)
    html = html.replace("<title>agent-memory</title>", "<title>agent-memory · demo</title>")
    html = html.replace(
        '<script src="data.js"></script>',
        f"<script>window.__AGENT_MEMORY_LIVE__ = {encoded};</script>",
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target

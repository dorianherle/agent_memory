"""Self-updating HTML live memory monitor (browser, no Cursor canvas needed).

Writes into ``.agent_memory/live/``:

- ``index.html`` — UI shell (created once; rewritten when template version changes)
- ``data.js`` — latest graph payload (rewritten on every durable memory event)

Open ``index.html`` in a browser. The page polls ``data.js`` and re-renders when
memory changes. A ``Now`` strip polls the reveal helper's ``/working`` endpoint
for dirty files, live Cursor terminals, and inspect state. The timeline is a
git-style lane graph so branches are visible.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

# Bump when SHELL_TEMPLATE changes so existing installs pick up the new shell.
SHELL_VERSION = "22"

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
    --rail: #3a3a3a;
    --warn: #e8a54b;
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
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--stroke);
  }
  header h1 {
    margin: 0;
    font-size: 18px;
    font-weight: 650;
    letter-spacing: -0.02em;
  }
  header .branch {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: 12px;
  }
  header .branch button {
    background: none;
    border: 0;
    padding: 0;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
  }
  header .branch button:hover { color: var(--text); }
  header .branch button.focus { color: var(--text); font-weight: 650; }
  header .branch button.here {
    box-shadow: inset 0 -2px 0 var(--accent);
  }
  header .branch-tab {
    padding: 2px 9px !important;
    border-radius: 999px !important;
    border: 1px solid var(--b, var(--stroke-strong)) !important;
    background: color-mix(in srgb, var(--b, var(--accent)) 16%, transparent) !important;
    color: var(--text) !important;
    font: 11px/1.3 ui-sans-serif, system-ui, sans-serif !important;
    font-weight: 650 !important;
  }
  header .branch-tab.focus {
    background: color-mix(in srgb, var(--b, var(--accent)) 32%, transparent) !important;
    box-shadow: inset 0 0 0 1px var(--b, var(--accent));
  }
  header .branch-label {
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  header .branch-tab.here::after {
    content: " · you";
    color: var(--faint);
    font-weight: 500;
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
    padding: 6px 14px 0;
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
  .dot.open_question { background: var(--warn); }
  .crumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 6px 14px 8px;
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
    grid-template-columns: minmax(0, 1fr) minmax(320px, 400px);
    gap: 0;
    min-height: 0;
  }
  @media (max-width: 860px) {
    main { grid-template-columns: 1fr; }
    #rail { border-left: 0; border-top: 1px solid var(--stroke); max-height: 48vh; }
    .now-grid { grid-template-columns: 1fr; }
  }
  #list {
    overflow: auto;
    padding: 10px 0 20px;
  }
  #rail {
    border-left: 1px solid var(--stroke);
    background: var(--panel);
    overflow: auto;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  #detail {
    padding: 10px 12px 16px;
    border-bottom: 1px solid var(--stroke);
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
  #detail .section {
    margin-top: 16px;
  }
  #detail .section:first-of-type { margin-top: 8px; }
  #detail .section-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  #detail .section-head h3 {
    margin: 0;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }
  #detail .copy {
    margin-left: auto;
    background: var(--elevated);
    border: 1px solid var(--stroke-strong);
    color: var(--muted);
    border-radius: 6px;
    padding: 2px 8px;
    font: 10px/1.4 ui-sans-serif, system-ui, sans-serif;
    cursor: pointer;
  }
  #detail .copy:hover { color: var(--text); border-color: var(--accent); }
  #detail .body {
    white-space: pre-wrap;
    word-break: break-word;
    font: 12.5px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
    color: var(--text);
  }
  #detail .body.mono {
    font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    background: var(--elevated);
    border: 1px solid var(--stroke);
    border-radius: 8px;
    padding: 10px 12px;
    max-height: min(55vh, 520px);
    overflow: auto;
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
  #now {
    padding: 8px 12px 10px;
    border-top: 1px solid var(--stroke);
    background: var(--panel);
    margin-top: auto;
  }
  .now-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .now-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 8px;
  }
  .now-head h2 {
    margin: 0;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .now-head .meta {
    color: var(--faint);
    font-size: 11px;
  }
  #now-run, #now-files {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
    max-height: 16vh;
    overflow: auto;
  }
  #now-run li, #now-files li {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }
  #now-run .pulse {
    flex: 0 0 7px;
    width: 7px;
    height: 7px;
    margin-top: 5px;
    border-radius: 99px;
    background: var(--user);
    animation: pulse 1.4s ease-in-out infinite;
  }
  #now-run .pulse.warn { background: var(--warn); }
  #now-run .pulse.idle {
    background: var(--faint);
    animation: none;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
  #now-run .cmd {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
  }#now-run .cmd .who { color: var(--muted); font-family: inherit; }
  #now-files .dir {
    color: var(--faint);
    font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
    margin-top: 4px;
  }
  #now-files .dir:first-child { margin-top: 0; }
  #now-files .code {
    flex: 0 0 2.2em;
    color: var(--summary);
    font: 10px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
    font-weight: 650;
  }
  #questions {
    display: none;
    border: 1px solid var(--warn);
    background: color-mix(in srgb, var(--warn) 12%, transparent);
    border-radius: 8px;
    margin: 0 20px 8px;
    padding: 8px 12px;
    font-size: 12px;
    line-height: 1.45;
  }
  #questions .q-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-weight: 650;
    color: var(--warn);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
  }
  #questions .q-text { margin-top: 3px; }
  #questions .q-item { margin-top: 6px; }
  #questions .q-deps button {
    background: none;
    border: 0;
    padding: 0;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
    text-decoration: underline dotted;
  }
  #prompt {
    display: none;
    padding: 8px 12px 10px;
    background: var(--panel);
  }
  #prompt .prompt-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 8px;
  }
  #prompt .prompt-head h2 {
    margin: 0;
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }
  #prompt .prompt-head .meta {
    color: var(--faint);
    font-size: 11px;
  }
  #prompt .copy {
    margin-left: auto;
    background: var(--elevated);
    border: 1px solid var(--stroke-strong);
    color: var(--muted);
    border-radius: 6px;
    padding: 2px 8px;
    font: 10px/1.4 ui-sans-serif, system-ui, sans-serif;
    cursor: pointer;
  }
  #prompt .copy:hover { color: var(--text); border-color: var(--accent); }
  .prompt-grid {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .prompt-col {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .prompt-block h3 {
    margin: 0 0 2px;
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .prompt-block .who {
    color: var(--faint);
    font: 10px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
    margin-bottom: 2px;
  }
  .prompt-block .who button {
    background: none;
    border: 0;
    padding: 0;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
    text-decoration: underline dotted;
  }
  .prompt-block .who .stale { color: var(--warn); }
  .prompt-block .body {
    white-space: pre-wrap;
    word-break: break-word;
    font: 12px/1.4 ui-sans-serif, system-ui, -apple-system, sans-serif;
    color: var(--text);
    background: var(--elevated);
    border: 1px solid var(--stroke);
    border-radius: 6px;
    padding: 6px 8px;
    max-height: 7.2em;
    overflow: auto;
  }
  .prompt-block .q-deps { margin-top: 4px; }
  .prompt-block .q-deps button {
    background: none;
    border: 0;
    padding: 0;
    margin-right: 6px;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
    text-decoration: underline dotted;
  }
  #now-files .code.st-A,
  #now-files .code.st-qq { color: var(--user); }
  #now-files .code.st-D { color: var(--delete); }
  #now-files a {
    color: var(--accent);
    text-decoration: none;
    font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  #now-files a:hover { color: var(--text); }
  .age {
    margin-left: auto;
    color: var(--faint);
    font: 11px/1.3 ui-sans-serif, system-ui, sans-serif;
    font-variant-numeric: tabular-nums;
    flex: 0 0 auto;
  }
  .graph {
    --lanes: 1;
    --lane-w: 100%;
    position: relative;
    width: calc(var(--lanes) * var(--lane-w));
    min-width: 100%;
  }
  .graph svg.rails {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 0;
  }
  .trow {
    display: grid;
    grid-template-columns: repeat(var(--lanes, 1), var(--lane-w));
    align-items: center;
    min-height: 52px;
    position: relative;
    z-index: 1;
  }
  .cell {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    width: var(--lane-w);
    min-height: 52px;
    padding: 4px 18px 4px 14px;
  }
  .gutter {
    position: relative;
    flex: 0 0 16px;
    width: 16px;
    height: 16px;
  }
  .gutter .commit {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 12px;
    height: 12px;
    border-radius: 99px;
    background: var(--b, var(--step));
    transform: translate(-50%, -50%);
    z-index: 1;
    box-shadow: 0 0 0 3px var(--bg);
  }
  .gutter .commit.kind-summary { background: var(--summary); }
  .gutter .commit.kind-user { background: var(--user); }
  .gutter .commit.current {
    box-shadow: 0 0 0 3px var(--bg), 0 0 0 5px var(--b, var(--accent));
  }
  .gutter .commit.other, .gutter .commit.stub {
    background: var(--b, var(--summary));
  }
  .card {
    position: relative;
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: 8px;
    background: transparent;
    border: 0;
    border-radius: 6px;
    padding: 4px 6px;
    cursor: pointer;
  }
  .card:hover .title span { color: var(--accent); }
  .card.selected .title span, .card.current .title span { color: var(--text); }
  .card.selected, .card.current {
    background: color-mix(in srgb, var(--b, var(--accent)) 12%, transparent);
  }
  .card .title {
    font-weight: 600;
    font-size: 12px;
    margin: 0;
    min-width: 0;
    flex: 1;
  }
  .card .title span {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card.stub {
    flex: 0 1 auto;
    width: fit-content;
    max-width: 100%;
    padding: 3px 10px;
    border-radius: 999px;
    border: 1px solid var(--b, var(--summary));
    background: color-mix(in srgb, var(--b, var(--summary)) 18%, var(--elevated));
    font: 11px/1.3 ui-sans-serif, system-ui, sans-serif;
    font-weight: 650;
  }
  .card.stub .title { flex: none; }
  .card .meta {
    color: var(--muted);
    font-size: 10px;
    flex: 0 0 auto;
  }
  .card .when {
    margin: 0;
    color: var(--faint);
    font: 10px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
    flex: 0 0 auto;
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
  <span><i class="dot open_question"></i>open</span>
</div>
<nav class="crumbs" id="crumbs"></nav>
<section id="questions">
  <div class="q-head"><span>Open questions</span></div>
  <div id="questions-list"></div>
</section>
<main>
  <section id="list"></section>
  <aside id="rail">
    <section id="detail" style="display:none"></section>
    <section id="prompt">
      <div class="prompt-head">
        <h2>Prompt</h2>
        <span class="meta" id="prompt-meta"></span>
        <button type="button" class="copy" id="prompt-copy">Copy injected</button>
      </div>
      <div class="prompt-grid">
        <div class="prompt-col" id="prompt-pinned"></div>
        <div class="prompt-col" id="prompt-findings"></div>
      </div>
    </section>
    <section id="now">
      <div class="now-grid">
        <div>
          <div class="now-head">
            <h2>Running</h2>
            <span class="meta" id="now-run-meta"></span>
          </div>
          <ul id="now-run"></ul>
        </div>
        <div>
          <div class="now-head">
            <h2>Files</h2>
            <span class="meta" id="now-files-meta">checking files…</span>
          </div>
          <ul id="now-files"></ul>
        </div>
      </div>
    </section>
  </aside>
</main>
<script src="data.js"></script>
<script>
(function () {
  const POLL_MS = 1500;
  const STORE_KEY = "agent-memory-live-ui-v1";
  const state = loadState();
  let lastHash = "";
  let graph = window.__AGENT_MEMORY_LIVE__ || { nodes: [], edges: [], branches: [] };
  let lastNow = null;
  let stickRight = true;
  let stickBottom = true;
  let lastRails = null;

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
        branch: state.branch || null,
        expanded: state.expanded || [],
        openBranches: state.openBranches || [],
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
      return null;
    }
    if (p.startsWith("/")) return p;
    const root = projectRoot();
    if (!root) return null;
    if (p.startsWith("./")) p = p.slice(2);
    while (p.startsWith("../")) {
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
      .filter((n) => !n.folded && n.kind !== "delete")
      .slice()
      .sort((a, b) => String(b.committed_at || "").localeCompare(String(a.committed_at || "")));
  }
  function setStatus(text, live) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.classList.toggle("live", !!live);
  }
  function workingEndpoint() {
    if (graph && graph.working) return graph.working;
    const reveal = graph && graph.reveal;
    if (!reveal) return null;
    return String(reveal).replace(/\/reveal\/?$/, "/working");
  }
  function formatAge(mtime) {
    if (!mtime) return "";
    const sec = Math.max(0, (Date.now() / 1000) - Number(mtime));
    if (sec < 60) return Math.floor(sec) + "s ago";
    if (sec < 3600) return Math.floor(sec / 60) + "m ago";
    if (sec < 86400) return Math.floor(sec / 3600) + "h ago";
    return Math.floor(sec / 86400) + "d ago";
  }
  function formatDuration(ms) {
    const sec = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    if (sec < 60) return sec + "s";
    if (sec < 3600) return Math.floor(sec / 60) + "m " + (sec % 60) + "s";
    return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
  }
  function latestVisible() {
    const nodes = (graph.nodes || []).filter((n) => !n.folded && n.kind !== "delete");
    nodes.sort((a, b) => String(b.committed_at || "").localeCompare(String(a.committed_at || "")));
    return nodes[0] || null;
  }
  function addNowRow(list, { pulse, text, title, age, warn }) {
    const li = document.createElement("li");
    const dot = document.createElement("i");
    dot.className = "pulse" + (warn ? " warn" : pulse ? "" : " idle");
    const cmd = document.createElement("span");
    cmd.className = "cmd";
    cmd.textContent = text;
    if (title) cmd.title = title;
    li.appendChild(dot);
    li.appendChild(cmd);
    if (age) {
      const stamp = document.createElement("span");
      stamp.className = "age";
      stamp.textContent = age;
      li.appendChild(stamp);
    }
    list.appendChild(li);
  }
  function renderNow(data) {
    lastNow = data || lastNow || {};
    const run = document.getElementById("now-run");
    const runMeta = document.getElementById("now-run-meta");
    const filesEl = document.getElementById("now-files");
    const filesMeta = document.getElementById("now-files-meta");
    const processes = (lastNow && lastNow.processes) || [];
    const inspecting = lastNow && lastNow.inspecting;
    const files = (lastNow && lastNow.files) || [];
    const total = (lastNow && lastNow.total) || files.length;
    const latest = latestVisible();

    run.innerHTML = "";
    if (inspecting && inspecting.id) {
      addNowRow(run, {
        pulse: true,
        warn: true,
        text: "inspecting " + String(inspecting.id).slice(0, 8) +
          (inspecting.branch ? " · return to " + inspecting.branch : ""),
        title: "Working files are at that snapshot",
      });
    }
    processes.forEach((proc) => {
      addNowRow(run, {
        pulse: true,
        text: proc.command,
        title: (proc.cwd ? proc.cwd + " · " : "") + "pid " + proc.pid,
        age: formatDuration(proc.running_for_ms),
      });
    });
    if (latest) {
      const working = latest.kind === "user";
      addNowRow(run, {
        pulse: working && !processes.length && !inspecting,
        text: (working ? "turn: " : "last: ") + truncate(latest.title || latest.kind, 56),
        title: latest.title,
      });
    }
    if (!run.children.length) {
      addNowRow(run, { pulse: false, text: "idle" });
    }
    runMeta.textContent = processes.length
      ? processes.length + " live"
      : inspecting
        ? "inspecting"
        : "idle";

    filesEl.innerHTML = "";
    if (!files.length) {
      filesMeta.textContent = "clean vs last memory save";
      return;
    }
    filesMeta.textContent = total + " changed · vs last memory save";
    const groups = new Map();
    files.forEach((f) => {
      const path = String(f.path || "");
      const i = path.lastIndexOf("/");
      const dir = i < 0 ? "" : path.slice(0, i);
      const name = i < 0 ? path : path.slice(i + 1);
      if (!groups.has(dir)) groups.set(dir, []);
      groups.get(dir).push({ file: f, name: name });
    });
    groups.forEach((rows, dir) => {
      if (dir) {
        const head = document.createElement("li");
        head.className = "dir";
        head.textContent = dir + "/";
        filesEl.appendChild(head);
      }
      rows.forEach((row) => {
        const f = row.file;
        const li = document.createElement("li");
        const code = document.createElement("span");
        const stamp = String(f.status || "?").replace(/\s/g, "");
        code.className = "code st-" + stamp.replace(/\?/g, "q");
        code.textContent = f.status || "?";
        const a = document.createElement("a");
        const abs = String(f.path || "").startsWith("/")
          ? f.path
          : projectRoot() + "/" + f.path;
        a.href = toFileUrl(abs);
        a.textContent = row.name;
        a.title = "Show in Finder: " + abs;
        a.addEventListener("click", (e) => openInFinder(abs, e));
        const age = document.createElement("span");
        age.className = "age";
        age.textContent = formatAge(f.mtime);
        li.appendChild(code);
        li.appendChild(a);
        li.appendChild(age);
        filesEl.appendChild(li);
      });
    });
  }
  function isDemo() {
    return !!(graph && graph.demo);
  }
  function pollWorking() {
    if (isDemo()) {
      renderNow(graph.now || { files: [], processes: [], inspecting: null });
      return;
    }
    const url = workingEndpoint();
    const meta = document.getElementById("now-files-meta");
    if (!url) {
      meta.textContent = "no file watcher";
      renderNow({ files: [], processes: [], inspecting: null });
      return;
    }
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error("working");
        return r.json();
      })
      .then(renderNow)
      .catch(() => {
        if (!document.getElementById("now-files").children.length) {
          meta.textContent = "waiting for file watcher…";
        }
      });
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
  function addSection(aside, title, text, { mono = false, copyLabel = "Copy" } = {}) {
    if (!text) return;
    const section = document.createElement("div");
    section.className = "section";
    const head = document.createElement("div");
    head.className = "section-head";
    const h3 = document.createElement("h3");
    h3.textContent = title;
    head.appendChild(h3);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy";
    btn.textContent = copyLabel;
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied";
        setTimeout(() => { btn.textContent = copyLabel; }, 1200);
      } catch (_) {
        btn.textContent = "Failed";
        setTimeout(() => { btn.textContent = copyLabel; }, 1200);
      }
    });
    head.appendChild(btn);
    section.appendChild(head);
    const body = linkifyBody(text);
    if (mono) body.classList.add("mono");
    section.appendChild(body);
    aside.appendChild(section);
  }
  function renderDetail(all) {
    const aside = document.getElementById("detail");
    const entry = state.selected ? all[state.selected] : null;
    if (!entry) {
      aside.innerHTML = "";
      aside.style.display = "none";
      return;
    }
    aside.style.display = "block";
    const when = formatWhen(entry.committed_at);
    const kids = (entry.children || []).length;
    aside.innerHTML = "";
    const head = document.createElement("div");
    head.className = "section-head";
    const h2 = document.createElement("h2");
    h2.textContent = entry.title || entry.kind || "entry";
    head.appendChild(h2);
    aside.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = [
      "commit " + entry.id.slice(0, 8),
      entry.agent,
      when,
    ].filter(Boolean).join(" · ");
    aside.appendChild(meta);
    const message = (entry.body || entry.goal || "").trim();
    addSection(aside, "Message", message || "(empty)", { mono: true });
    if (kids) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "subgraph";
      btn.textContent = "Subgraph (" + kids + ")";
      btn.addEventListener("click", () => {
        if (!isExpanded(entry.id)) toggleExpand(entry.id);
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
    if (entry.kind === "user") {
      if (entry.instructions) {
        addSection(aside, "1. Memory instructions (prompt text)", entry.instructions, {
          mono: true,
        });
      }
      if (entry.rule) {
        addSection(aside, "2. Always-apply rule → system/rules prompt", entry.rule, {
          mono: true,
        });
      }
      if (entry.mcp_tools) {
        addSection(aside, "3. MCP tool catalog (also sent as tool schemas)", entry.mcp_tools, {
          mono: true,
        });
      }
      const stateText = entry.memory_state || entry.injected || "";
      if (stateText) {
        addSection(aside, "4. Injected MEMORY STATE", stateText, { mono: true });
      }
      if (entry.packet) {
        addSection(aside, "Full memory packet (copy)", entry.packet, {
          mono: true,
          copyLabel: "Copy packet",
        });
      }
    }
  }
  const BRANCH_COLORS = ["#e8a54b", "#9386f2", "#e88a9a", "#5bc0be", "#6ea8fe"];
  function branchColor(name) {
    const s = String(name || "");
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return BRANCH_COLORS[h % BRANCH_COLORS.length];
  }
  function currentBranchName() {
    const row = (graph.branches || []).find((b) => b.current);
    return (row && row.name) || ((graph.branches || [])[0] || {}).name || "main";
  }
  function lineageOf(tipId, parentsOf) {
    const seen = new Set();
    const stack = tipId ? [tipId] : [];
    while (stack.length) {
      const id = stack.pop();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      (parentsOf[id] || []).forEach((p) => stack.push(p));
    }
    return seen;
  }
  function attachPoint(tipId, onFocus, parentsOf) {
    let id = tipId;
    const seen = new Set();
    while (id && !seen.has(id)) {
      if (onFocus.has(id)) return id;
      seen.add(id);
      id = (parentsOf[id] || [])[0] || null;
    }
    return null;
  }
  function isBranchOpen(name) {
    return (state.openBranches || []).indexOf(name) >= 0;
  }
  function isExpanded(id) {
    return (state.expanded || []).indexOf(id) >= 0;
  }
  function toggleExpand(id) {
    const cur = (state.expanded || []).slice();
    const at = cur.indexOf(id);
    if (at >= 0) cur.splice(at, 1);
    else cur.push(id);
    state.expanded = cur;
  }
  function focusContext() {
    const parentsAll = parentMap();
    const focus = currentBranchName();
    const tip = ((graph.branches || []).find((b) => b.name === focus) || {}).tip;
    const onFocus = lineageOf(tip, parentsAll);
    return { focus: focus, tip: tip, onFocus: onFocus, parentsAll: parentsAll };
  }
  function toggleBranch(name, selectId) {
    const current = currentBranchName();
    const row = (graph.branches || []).find((b) => b.name === name);
    state.branch = name;
    if (name === current) {
      state.openBranches = [];
      state.selected = selectId || (row && row.tip) || null;
      state.reveal = state.selected;
      saveState();
      render();
      return;
    }
    const cur = (state.openBranches || []).slice();
    const at = cur.indexOf(name);
    if (at >= 0) cur.splice(at, 1);
    else cur.push(name);
    state.openBranches = cur;
    state.selected = selectId || (row && row.tip) || null;
    state.reveal = isBranchOpen(name)
      ? (state.selected || ("stub:" + name))
      : ("stub:" + name);
    saveState();
    render();
  }
  function parentMap(nodes, folds) {
    const ids = nodes ? new Set(nodes.map((n) => n.id)) : null;
    const map = {};
    (graph.edges || []).forEach((e) => {
      const kind = e.kind || "parent";
      if (kind !== "parent" && !(folds && kind === "fold")) return;
      if (ids && (!ids.has(e.from) || !ids.has(e.to))) return;
      (map[e.to] = map[e.to] || []).push(e.from);
    });
    if (folds && nodes) {
      const have = new Set(nodes.map((n) => n.id));
      const all = byId();
      (state.expanded || []).forEach((id) => {
        const kids = (all[id] && all[id].children) || [];
        kids.forEach((cid, i) => {
          const parent = i === 0 ? id : kids[i - 1];
          if (!have.has(cid) || !have.has(parent)) return;
          const row = map[cid] || [];
          if (row.indexOf(parent) < 0) row.push(parent);
          map[cid] = row;
        });
      });
    }
    return map;
  }
  function graphNodes(all) {
    const ctx = focusContext();
    if (!ctx.onFocus.size) {
      return visibleNodes(all);
    }
    const current = currentBranchName();
    const show = new Set(ctx.onFocus);
    const stubs = [];
    (graph.branches || []).forEach((b) => {
      if ((b.name || "main") === current) return;
      if (isBranchOpen(b.name)) {
        lineageOf(b.tip, ctx.parentsAll).forEach((x) => show.add(x));
        return;
      }
      const tip = all[b.tip] || {};
      stubs.push({
        id: "stub:" + b.name,
        stub: true,
        kind: "stub",
        branch: b.name,
        tip: b.tip,
        attach: attachPoint(b.tip, ctx.onFocus, ctx.parentsAll),
        title: "BRANCH " + b.name,
        body: tip.title || "",
        committed_at: tip.committed_at || "",
        agent: "",
      });
    });
    (state.expanded || []).forEach((id) => {
      ((all[id] && all[id].children) || []).forEach((cid) => show.add(cid));
    });
    const nodes = (graph.nodes || []).filter(
      (n) => show.has(n.id) && n.kind !== "delete"
    );
    return nodes.concat(stubs);
  }
  function lanePlan(ctx, nodes) {
    const current = currentBranchName();
    const others = (graph.branches || [])
      .map((b) => b.name || "main")
      .filter((name) => name !== current);
    const order = others.concat([current]);
    const laneOfName = {};
    order.forEach((name, i) => { laneOfName[name] = i; });
    const sets = {};
    (graph.branches || []).forEach((b) => {
      sets[b.name] = lineageOf(b.tip, ctx.parentsAll);
    });
    const ownerOf = {};
    const laneOf = {};
    nodes.forEach((n) => {
      let name = current;
      if (n.stub) name = n.branch;
      else if (!ctx.onFocus.has(n.id)) {
        const hit = others.find((bn) => sets[bn] && sets[bn].has(n.id));
        if (hit) name = hit;
      }
      ownerOf[n.id] = name;
      laneOf[n.id] = laneOfName[name] == null ? 0 : laneOfName[name];
    });
    return {
      laneOf: laneOf,
      ownerOf: ownerOf,
      laneOfName: laneOfName,
      laneCount: order.length,
      order: order,
    };
  }
  function renderRails(wrap, svg, nodes, parentsOf, plan) {
    const box = wrap.getBoundingClientRect();
    const dots = {};
    wrap.querySelectorAll(".cell[data-id]").forEach((cell) => {
      const dot = cell.querySelector(".commit");
      if (!dot) return;
      const r = dot.getBoundingClientRect();
      dots[cell.dataset.id] = {
        x: r.left + r.width / 2 - box.left,
        y: r.top + r.height / 2 - box.top,
      };
    });
    svg.setAttribute("width", String(Math.max(box.width, wrap.scrollWidth)));
    svg.setAttribute("height", String(wrap.scrollHeight));
    svg.innerHTML = "";
    const ns = "http://www.w3.org/2000/svg";
    const add = (d, color, width) => {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", String(width || 2.5));
      path.setAttribute("stroke-linecap", "round");
      svg.appendChild(path);
    };
    nodes.forEach((n) => {
      (parentsOf[n.id] || []).forEach((pid) => {
        const a = dots[n.id];
        const b = dots[pid];
        if (!a || !b) return;
        const color = branchColor((plan.ownerOf || {})[n.id]);
        const mid = (a.y + b.y) / 2;
        add(
          a.x === b.x
            ? "M " + a.x + " " + a.y + " L " + b.x + " " + b.y
            : "M " + a.x + " " + a.y + " C " + a.x + " " + mid + ", " + b.x + " " + mid + ", " + b.x + " " + b.y,
          color,
          a.x === b.x ? 2.5 : 3.5
        );
      });
    });
  }
  function renderList(all) {
    const list = document.getElementById("list");
    const atRoot = !(state.trail || []).length;
    const ctx = focusContext();
    const nodes = atRoot ? graphNodes(all) : visibleNodes(all);
    const parentsOf = parentMap(nodes.filter((n) => !n.stub), true);
    nodes.forEach((n) => {
      if (n.stub && n.attach) parentsOf[n.id] = [n.attach];
    });
    const plan = lanePlan(ctx, nodes);
    const depths = {};
    const depthOf = (id) => {
      if (depths[id] != null) return depths[id];
      depths[id] = 0;
      const ps = ctx.parentsAll[id] || [];
      depths[id] = ps.length ? 1 + Math.max.apply(null, ps.map(depthOf)) : 0;
      return depths[id];
    };
    const atDepth = {};
    let maxD = 0;
    nodes.forEach((n) => {
      const d = n.stub
        ? (n.tip ? depthOf(n.tip) : n.attach ? depthOf(n.attach) + 1 : 0)
        : depthOf(n.id);
      const lane = plan.laneOf[n.id] == null ? 0 : plan.laneOf[n.id];
      maxD = Math.max(maxD, d);
      (atDepth[d] = atDepth[d] || {})[lane] = n;
    });
    const wrap = document.createElement("div");
    wrap.className = "graph";
    wrap.style.setProperty("--lanes", String(Math.max(1, plan.laneCount)));
    wrap.style.setProperty("--lane-w", Math.max(280, list.clientWidth) + "px");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "rails");
    wrap.appendChild(svg);
    const fillCell = (cell, meta) => {
      const owner = (plan.ownerOf || {})[meta.id] || currentBranchName();
      const color = branchColor(owner);
      cell.dataset.id = meta.id;
      const rail = document.createElement("div");
      rail.className = "gutter";
      const commit = document.createElement("i");
      commit.className = "commit kind-" + (meta.kind || "step");
      if (meta.current) commit.classList.add("current");
      if (meta.stub) commit.classList.add("stub");
      if (owner !== currentBranchName()) commit.classList.add("other");
      commit.style.setProperty("--b", color);
      const expandable = !!(meta.children && meta.children.length);
      if (expandable || meta.stub) commit.style.cursor = "pointer";
      rail.appendChild(commit);
      const card = document.createElement("div");
      card.className = "card kind-" + (meta.kind || "step") + (meta.stub ? " stub" : "");
      card.style.setProperty("--b", color);
      if (meta.current) card.classList.add("current");
      if (state.selected === meta.id || state.selected === meta.tip) card.classList.add("selected");
      const title = document.createElement("div");
      title.className = "title";
      const label = document.createElement("span");
      label.textContent = meta.stub
        ? "BRANCH " + meta.branch
        : truncate(meta.title || meta.kind || "entry", 42);
      title.appendChild(label);
      card.appendChild(title);
      const openNode = (toggle) => {
        if (meta.stub) {
          toggleBranch(meta.branch, meta.tip);
          return;
        }
        state.selected = meta.id;
        if (toggle && expandable) toggleExpand(meta.id);
        saveState();
        render();
        document.getElementById("detail").scrollIntoView({ block: "start" });
      };
      commit.addEventListener("click", (e) => {
        e.stopPropagation();
        openNode(true);
      });
      card.addEventListener("click", () => openNode(false));
      cell.appendChild(rail);
      cell.appendChild(card);
    };
    for (let d = maxD; d >= 0; d--) {
      const at = atDepth[d];
      if (!at) continue;
      const trow = document.createElement("div");
      trow.className = "trow";
      for (let lane = 0; lane < plan.laneCount; lane++) {
        const cell = document.createElement("div");
        cell.className = "cell" + (at[lane] ? "" : " empty");
        if (at[lane]) fillCell(cell, at[lane]);
        trow.appendChild(cell);
      }
      wrap.appendChild(trow);
    }
    list.innerHTML = "";
    list.appendChild(wrap);
    bindPan(list);
    lastRails = () => {
      wrap.style.setProperty("--lane-w", Math.max(280, list.clientWidth) + "px");
      renderRails(wrap, svg, nodes, parentsOf, plan);
    };
    lastRails();
    if (state.reveal) {
      const hit = wrap.querySelector('.cell[data-id="' + state.reveal + '"]');
      if (hit) hit.scrollIntoView({ block: "center", inline: "nearest" });
      stickRight = list.scrollLeft + list.clientWidth >= list.scrollWidth - 8;
      stickBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
      state.reveal = null;
    } else {
      if (stickRight) list.scrollLeft = list.scrollWidth;
      if (stickBottom) list.scrollTop = list.scrollHeight;
    }
  }
  function bindPan(list) {
    if (list.dataset.pan) return;
    list.dataset.pan = "1";
    list.addEventListener("scroll", () => {
      stickRight = list.scrollLeft + list.clientWidth >= list.scrollWidth - 8;
      stickBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
    }, { passive: true });
    new ResizeObserver(() => {
      if (lastRails) lastRails();
      if (stickRight) list.scrollLeft = list.scrollWidth;
      if (stickBottom) list.scrollTop = list.scrollHeight;
    }).observe(list);
  }
  function depButton(id) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "[" + String(id).slice(0, 8) + "]";
    const target = byId()[String(id)] || null;
    btn.title = target ? "Show entry " + target.id.slice(0, 8)
      : "Entry not on this view";
    btn.addEventListener("click", () => {
      if (!target) return;
      state.selected = target.id;
      saveState();
      render();
      document.getElementById("detail").scrollIntoView({behavior: "smooth"});
    });
    return btn;
  }
  function renderQuestions() {
    const el = document.getElementById("questions");
    const list = document.getElementById("questions-list");
    const promptQs = ((graph.prompt || {}).questions || []);
    if (promptQs.length) {
      el.style.display = "none";
      return;
    }
    const qs = (graph.nodes || []).filter((n) => n.kind === "open_question");
    if (!qs.length) {
      el.style.display = "none";
      return;
    }
    el.style.display = "block";
    list.innerHTML = "";
    qs.forEach((q) => {
      const item = document.createElement("div");
      item.className = "q-item";
      const text = document.createElement("div");
      text.className = "q-text";
      text.textContent = "[" + String(q.id).slice(0, 8) + "] "
        + (q.title || "").trim();
      item.appendChild(text);
      const deps = document.createElement("div");
      deps.className = "q-deps";
      (q.depends_on || []).forEach((id) => deps.appendChild(depButton(id)));
      if (deps.childNodes.length) item.appendChild(deps);
      list.appendChild(item);
    });
  }
  function promptHasContent(p) {
    if (!p) return false;
    return !!(p.goal || p.steering
      || (p.questions || []).length
      || (p.findings || []).length
      || (p.injected || "").trim());
  }
  function promptBlock(title, entry, { extra } = {}) {
    if (!entry || !(entry.text || "").trim()) return null;
    const block = document.createElement("div");
    block.className = "prompt-block";
    const h3 = document.createElement("h3");
    h3.textContent = title;
    block.appendChild(h3);
    const who = document.createElement("div");
    who.className = "who";
    const btn = depButton(entry.id);
    who.appendChild(btn);
    if (entry.agent) {
      const agent = document.createElement("span");
      agent.textContent = " @" + entry.agent;
      who.appendChild(agent);
    }
    if (entry.stale) {
      const stale = document.createElement("span");
      stale.className = "stale";
      stale.textContent = " [stale]";
      who.appendChild(stale);
    }
    block.appendChild(who);
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = entry.text;
    block.appendChild(body);
    const deps = (entry.depends_on || []);
    if (deps.length) {
      const row = document.createElement("div");
      row.className = "q-deps";
      deps.forEach((id) => row.appendChild(depButton(id)));
      block.appendChild(row);
    }
    if (extra) block.appendChild(extra);
    return block;
  }
  function renderPrompt() {
    const el = document.getElementById("prompt");
    const pinned = document.getElementById("prompt-pinned");
    const findings = document.getElementById("prompt-findings");
    const meta = document.getElementById("prompt-meta");
    const copy = document.getElementById("prompt-copy");
    const p = graph.prompt || {};
    if (!promptHasContent(p)) {
      el.style.display = "none";
      return;
    }
    el.style.display = "block";
    pinned.innerHTML = "";
    findings.innerHTML = "";
    const add = (parent, title, entry) => {
      const block = promptBlock(title, entry);
      if (block) parent.appendChild(block);
    };
    add(pinned, "Goal", p.goal);
    add(pinned, "Steering", p.steering);
    (p.questions || []).forEach((q) => add(pinned, "Open question", q));
    const found = p.findings || [];
    const findingsHead = document.createElement("div");
    findingsHead.className = "prompt-block";
    const findingsTitle = document.createElement("h3");
    findingsTitle.textContent = "Findings so far";
    findingsHead.appendChild(findingsTitle);
    findings.appendChild(findingsHead);
    if (!found.length) {
      const body = document.createElement("div");
      body.className = "body";
      body.textContent = "No findings yet.";
      findingsHead.appendChild(body);
    } else {
      found.forEach((item) => {
        const block = promptBlock("Finding", item);
        if (block) {
          block.querySelector("h3").remove();
          findings.appendChild(block);
        }
      });
    }
    const n = found.length;
    meta.textContent = n ? (n + (n === 1 ? " finding" : " findings")) : "";
    copy.onclick = async () => {
      const text = p.injected || "";
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = "Copy injected"; }, 1200);
      } catch (_) {
        copy.textContent = "Failed";
        setTimeout(() => { copy.textContent = "Copy injected"; }, 1200);
      }
    };
  }
  function renderBranches() {
    const el = document.getElementById("branch");
    el.innerHTML = "";
    const mark = document.createElement("span");
    mark.className = "branch-label";
    mark.textContent = "BRANCH";
    el.appendChild(mark);
    const ctx = focusContext();
    const rows = graph.branches || [];
    if (rows.length <= 1) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "focus here";
      btn.textContent = ctx.focus;
      btn.title = "BRANCH " + ctx.focus + " · you are here";
      el.appendChild(btn);
      return;
    }
    rows.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "branch-tab";
      btn.style.setProperty("--b", branchColor(b.name));
      if (b.current) btn.classList.add("here");
      if (b.name === (state.branch || ctx.focus) || isBranchOpen(b.name)) {
        btn.classList.add("focus");
      }
      btn.textContent = b.name;
      btn.title = b.current
        ? "BRANCH " + b.name + " · you are here"
        : (isBranchOpen(b.name) ? "Collapse" : "Expand") + " BRANCH " + b.name;
      btn.addEventListener("click", () => toggleBranch(b.name));
      el.appendChild(btn);
    });
  }
  function render() {
    const all = byId();
    renderBranches();
    renderQuestions();
    renderPrompt();
    state.trail = (state.trail || []).filter((id) => all[id] && (all[id].children || []).length);
    if (state.selected && !all[state.selected]) state.selected = null;
    const names = new Set((graph.branches || []).map((b) => b.name));
    if (state.branch && !names.has(state.branch)) state.branch = null;
    saveState();
    renderCrumbs(all);
    renderList(all);
    renderDetail(all);
    if (lastNow) renderNow(lastNow);
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
    if (isDemo()) return;
    const s = document.createElement("script");
    s.src = "data.js?t=" + Date.now();
    s.onload = () => {
      applyData(window.__AGENT_MEMORY_LIVE__);
      setStatus("live · " + new Date().toLocaleTimeString(), true);
      pollWorking();
      schedule();
    };
    s.onerror = () => {
      setStatus("waiting for data…", false);
      schedule();
    };
    document.head.appendChild(s);
    setTimeout(() => s.remove(), 5000);
  }
  function schedule() {
    setTimeout(poll, POLL_MS);
  }
  applyData(graph);
  setStatus(isDemo() ? "demo" : "live", true);
  pollWorking();
  if (!isDemo()) schedule();
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
    }


def write_live_demo(path: str | Path) -> Path:
    """Write a self-contained demo page (no memory store, no polling)."""
    encoded = json.dumps(demo_payload(), indent=2).replace("</", "<\\/")
    html = SHELL_TEMPLATE.replace("__SHELL_VERSION__", SHELL_VERSION)
    html = html.replace("<title>Memory · live</title>", "<title>Memory · demo</title>")
    html = html.replace(
        '<script src="data.js"></script>',
        f"<script>window.__AGENT_MEMORY_LIVE__ = {encoded};</script>",
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target

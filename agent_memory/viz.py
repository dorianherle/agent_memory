import json
from datetime import datetime
from pathlib import Path

TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Agent Memory</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body { margin: 0; font-family: -apple-system, sans-serif; display: flex; height: 100vh; }
  #graph { flex: 1; }
  #details { width: 360px; border-left: 1px solid #ddd; padding: 16px; overflow: auto; }
  #details pre { white-space: pre-wrap; word-break: break-word; font-size: 13px; }
  .hint { color: #888; font-size: 14px; }
</style>
</head>
<body>
<div id="graph"></div>
<div id="details">
  <p class="hint">Click a node for its full entry.</p>
  <p class="hint">Double-click a gold summary to unfold the steps behind it.</p>
  <p class="hint">gold = summary, blue = user, white = step, red = delete</p>
</div>
<script>
const data = __DATA__;
const nodes = new vis.DataSet(data.nodes);
const edges = new vis.DataSet(data.edges);
const network = new vis.Network(
  document.getElementById("graph"),
  { nodes: nodes, edges: edges },
  {
    layout: { hierarchical: { direction: "UD", sortMethod: "directed", levelSeparation: 80 } },
    edges: { arrows: "to" },
    nodes: { shape: "box", font: { face: "monospace", size: 12 } },
    physics: false,
  }
);
network.on("click", (params) => {
  if (!params.nodes.length) return;
  const node = nodes.get(params.nodes[0]);
  const text = node.detail.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  document.getElementById("details").innerHTML = "<pre>" + text + "</pre>";
});
network.on("doubleClick", (params) => {
  if (!params.nodes.length) return;
  const node = nodes.get(params.nodes[0]);
  if (!node.children) return;
  const show = nodes.get(node.children[0]).hidden;
  node.children.forEach((id) => nodes.update({ id: id, hidden: !show }));
  edges.forEach((edge) => {
    if (edge.fold === node.id) edges.update({ id: edge.id, hidden: !show });
  });
});
</script>
</body>
</html>
"""

COLORS = {"summary": "#f6c344", "user": "#6ea8fe", "step": "#f2f2f2", "delete": "#f4a6a6"}


def _kind(row, payload):
    if row["kind"] == "delete":
        return "delete"
    if "covers" in payload:
        return "summary"
    if payload.get("user"):
        return "user"
    return "step"


def export_html(memory, path=None) -> Path:
    memory._sync_catalog()
    current = memory._home_branch()
    tips = {}
    for name, tip in memory._refs().items():
        label = name + (" (you are here)" if name == current else "")
        tips.setdefault(tip, []).append(label)

    raw = memory._run("log", "--all", "--format=%H %P", check=False)
    parents = {}
    rows = {}
    for line in raw.splitlines():
        sha, *sha_parents = line.split()
        parents[sha] = sha_parents
        row = memory._catalog.get(sha)
        if row is not None:
            rows[sha] = row
    covers = {}
    for sha, row in rows.items():
        payload = json.loads(row["payload"])
        wanted = [c for c in payload.get("covers", []) if c in parents]
        if wanted:
            covers[sha] = wanted
    folded = {step for steps in covers.values() for step in steps}

    def nearest_visible(sha):
        while sha in folded:
            if not parents.get(sha):
                return None
            sha = parents[sha][0]
        return sha

    edges = []
    seen = set()
    for sha in rows:
        if sha in folded:
            continue
        for parent in parents[sha]:
            visible = nearest_visible(parent)
            if visible and (visible, sha) not in seen:
                seen.add((visible, sha))
                edges.append({"from": visible, "to": sha})
    # Folded steps hang under their summary as a hidden, dashed chain.
    for summary, steps in covers.items():
        chain = [summary] + steps
        for start, end in zip(chain, chain[1:]):
            edges.append({
                "id": f"fold-{summary}-{end}",
                "from": start,
                "to": end,
                "dashes": True,
                "hidden": True,
                "fold": summary,
            })

    nodes = []
    for sha, row in rows.items():
        payload = json.loads(row["payload"])
        kind = _kind(row, payload)
        first_line = row["message"].split("\n")[0]
        head = sha[:8]
        if sha in covers:
            head += f" [+{len(covers[sha])}]"
        label = f"{head}\n{first_line[:34]}"
        if sha in tips:
            label += f"\n[{', '.join(tips[sha])}]"
        moment = datetime.fromtimestamp(row["committed_us"] / 1_000_000)
        detail = (
            f"[{sha[:8]}] {kind}\n"
            f"{moment.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"branches: {', '.join(tips.get(sha, [])) or '-'}\n\n"
            f"{row['message']}\n\n"
            f"payload: {json.dumps(payload, indent=2)}"
        )
        node = {"id": sha, "label": label, "color": COLORS[kind], "detail": detail}
        if sha in folded:
            node["hidden"] = True
        if sha in covers:
            node["children"] = covers[sha]
        if sha in tips:
            node["borderWidth"] = 3
            if any(name.startswith(current) for name in tips[sha]):
                node["color"] = {"background": COLORS[kind], "border": "#e4572e"}
        nodes.append(node)

    data = json.dumps({"nodes": nodes, "edges": edges}).replace("</", "<\\/")
    page = Path(path) if path else memory._git_dir / "graph.html"
    page.write_text(TEMPLATE.replace("__DATA__", data))
    return page

import json
from datetime import datetime, timezone
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

KINDS = {
    "summary": {"color": "#f6c344", "label": "summary"},
    "user": {"color": "#6ea8fe", "label": "user"},
    "step": {"color": "#f2f2f2", "label": "step"},
    "delete": {"color": "#f4a6a6", "label": "delete"},
}


def _kind(row, payload):
    if row["kind"] == "delete":
        return "delete"
    if "covers" in payload:
        return "summary"
    if payload.get("user"):
        return "user"
    return "step"


def graph_data(memory) -> dict:
    """Structured graph for any renderer: HTML, canvas, or custom UI.

    Returns ``{nodes, edges, kinds, branches}``.
    """
    memory._sync_catalog()
    current = memory._home_branch()
    tip_names: dict[str, list[str]] = {}
    branch_rows = []
    for name, tip in sorted(memory._refs().items()):
        tip_names.setdefault(tip, []).append(name)
        branch_rows.append(
            {"name": name, "tip": tip, "current": name == current}
        )

    raw = memory._run("log", "--all", "--format=%H %P", check=False)
    parents: dict[str, list[str]] = {}
    rows = {}
    for line in raw.splitlines():
        sha, *sha_parents = line.split()
        parents[sha] = sha_parents
        row = memory._catalog.get(sha)
        if row is not None:
            rows[sha] = row

    covers: dict[str, list[str]] = {}
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
                edges.append({"from": visible, "to": sha, "kind": "parent"})
    for summary, steps in covers.items():
        chain = [summary] + steps
        for start, end in zip(chain, chain[1:]):
            edges.append(
                {
                    "id": f"fold-{summary}-{end}",
                    "from": start,
                    "to": end,
                    "kind": "fold",
                    "fold": summary,
                    "hidden": True,
                }
            )

    nodes = []
    for sha, row in rows.items():
        payload = json.loads(row["payload"])
        kind = _kind(row, payload)
        first_line = row["message"].split("\n")[0]
        head = sha[:8]
        if sha in covers:
            head += f" [+{len(covers[sha])}]"
        label = f"{head}\n{first_line[:34]}"
        names = tip_names.get(sha, [])
        if names:
            label += f"\n[{', '.join(names)}]"
        moment = datetime.fromtimestamp(
            row["committed_us"] / 1_000_000, tz=timezone.utc
        )
        detail = (
            f"[{sha[:8]}] {kind}\n"
            f"{moment.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"branches: {', '.join(names) or '-'}\n\n"
            f"{row['message']}\n\n"
            f"payload: {json.dumps(payload, indent=2)}"
        )
        node = {
            "id": sha,
            "kind": kind,
            "label": label,
            "message": row["message"],
            "detail": detail,
            "branches": names,
            "folded": sha in folded,
            "tip": bool(names),
            "current": any(name == current for name in names),
            "committed_at": moment.isoformat(),
        }
        if sha in covers:
            node["children"] = covers[sha]
        nodes.append(node)

    return {
        "nodes": nodes,
        "edges": edges,
        "kinds": KINDS,
        "branches": branch_rows,
    }


def _vis_payload(data: dict, current_branch: str | None) -> dict:
    """Map structured graph_data into the vis-network HTML shape."""
    colors = {kind: meta["color"] for kind, meta in data["kinds"].items()}
    nodes = []
    for node in data["nodes"]:
        kind = node["kind"]
        color = colors[kind]
        label = node["label"]
        if node.get("branches") and current_branch:
            tagged = [
                name + (" (you are here)" if name == current_branch else "")
                for name in node["branches"]
            ]
            lines = label.split("\n")
            if len(lines) >= 3:
                lines[-1] = f"[{', '.join(tagged)}]"
                label = "\n".join(lines)
        vis = {
            "id": node["id"],
            "label": label,
            "color": color,
            "detail": node["detail"],
        }
        if node.get("folded"):
            vis["hidden"] = True
        if node.get("children"):
            vis["children"] = node["children"]
        if node.get("tip"):
            vis["borderWidth"] = 3
            if node.get("current"):
                vis["color"] = {"background": color, "border": "#e4572e"}
        nodes.append(vis)

    edges = []
    for edge in data["edges"]:
        if edge.get("kind") == "fold":
            edges.append(
                {
                    "id": edge["id"],
                    "from": edge["from"],
                    "to": edge["to"],
                    "dashes": True,
                    "hidden": True,
                    "fold": edge["fold"],
                }
            )
        else:
            edges.append({"from": edge["from"], "to": edge["to"]})
    return {"nodes": nodes, "edges": edges}


def export_html(memory, path=None) -> Path:
    data = graph_data(memory)
    current = next(
        (branch["name"] for branch in data["branches"] if branch["current"]),
        None,
    )
    payload = _vis_payload(data, current)
    encoded = json.dumps(payload).replace("</", "<\\/")
    page = Path(path) if path else memory._git_dir / "graph.html"
    page.write_text(TEMPLATE.replace("__DATA__", encoded))
    return page

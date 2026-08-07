"""Write a Cursor `.canvas.tsx` from structured memory graph data."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

CANVAS_TEMPLATE = r'''import {
  Card,
  CardBody,
  CardHeader,
  H1,
  Pill,
  Row,
  Stack,
  Stat,
  Text,
  computeDAGLayout,
  useHostTheme,
} from "cursor/canvas";

type Kind = "summary" | "user" | "step" | "delete";

type GraphNode = {
  id: string;
  kind: Kind;
  title: string;
  tip: boolean;
  current: boolean;
  folded: boolean;
  branches: string[];
};

type GraphEdge = {
  from: string;
  to: string;
  kind: "parent" | "fold";
};

type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  branches: Array<{ name: string; tip: string; current: boolean }>;
};

const GRAPH = __DATA__ as Graph;

const NODE_W = 168;
const NODE_H = 52;

function kindTone(kind: Kind): "neutral" | "info" | "warning" | "danger" {
  if (kind === "summary") return "warning";
  if (kind === "user") return "info";
  if (kind === "delete") return "danger";
  return "neutral";
}

function MemoryGraphCanvas() {
  const theme = useHostTheme();
  const visible = GRAPH.nodes.filter((n) => !n.folded);
  const visibleIds = new Set(visible.map((n) => n.id));
  const layoutEdges = GRAPH.edges.filter(
    (e) =>
      e.kind === "parent" &&
      visibleIds.has(e.from) &&
      visibleIds.has(e.to),
  );

  const layout = computeDAGLayout({
    nodes: visible.map((n) => ({ id: n.id })),
    edges: layoutEdges.map((e) => ({ from: e.from, to: e.to })),
    direction: "vertical",
    nodeWidth: NODE_W,
    nodeHeight: NODE_H,
    rankGap: 56,
    nodeGap: 28,
    padding: 20,
  });

  const byId = Object.fromEntries(visible.map((n) => [n.id, n]));
  const pos = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
  const currentBranch =
    GRAPH.branches.find((b) => b.current)?.name ?? "main";

  const fillFor = (kind: Kind, current: boolean) => {
    if (current) return theme.accent.primary;
    if (kind === "summary") return theme.fill.secondary;
    if (kind === "user") return theme.fill.tertiary;
    if (kind === "delete") return theme.fill.quaternary;
    return theme.fill.primary;
  };

  const strokeFor = (kind: Kind, current: boolean) => {
    if (current) return theme.accent.primary;
    if (kind === "summary") return theme.stroke.secondary;
    return theme.stroke.primary;
  };

  return (
    <Stack gap={16} style={{ padding: 16 }}>
      <Stack gap={6}>
        <H1>Agent Memory</H1>
        <Text tone="secondary" size="small">
          Live graph from memory store · branch {currentBranch}
        </Text>
      </Stack>

      <Row gap={12} style={{ flexWrap: "wrap" }}>
        <Stat value={String(visible.length)} label="Visible entries" />
        <Stat value={String(GRAPH.branches.length)} label="Branches" />
        <Stat
          value={String(GRAPH.nodes.filter((n) => n.kind === "summary").length)}
          label="Summaries"
        />
      </Row>

      {GRAPH.branches.length > 0 ? (
        <Row gap={8} style={{ flexWrap: "wrap" }}>
          {GRAPH.branches.map((branch) => (
            <Pill
              key={branch.name}
              tone={branch.current ? "info" : "neutral"}
              size="small"
              active={branch.current}
            >
              {branch.current ? `${branch.name} · here` : branch.name}
            </Pill>
          ))}
        </Row>
      ) : null}

      <Card>
        <CardHeader>Graph</CardHeader>
        <CardBody style={{ padding: 12, overflow: "auto" }}>
          {visible.length === 0 ? (
            <Text tone="secondary">Nothing saved yet.</Text>
          ) : (
            <svg
              width={layout.width}
              height={layout.height}
              viewBox={`0 0 ${layout.width} ${layout.height}`}
              role="img"
              aria-label="Memory graph"
            >
              {layout.edges.map((edge, i) => (
                <line
                  key={`${edge.from}-${edge.to}-${i}`}
                  x1={edge.sourceX}
                  y1={edge.sourceY}
                  x2={edge.targetX}
                  y2={edge.targetY}
                  stroke={
                    edge.isBackEdge
                      ? theme.stroke.secondary
                      : theme.stroke.primary
                  }
                  strokeWidth={1.5}
                  strokeDasharray={edge.isBackEdge ? "4 3" : undefined}
                />
              ))}
              {layout.nodes.map((node) => {
                const meta = byId[node.id];
                if (!meta) return null;
                const p = pos[node.id];
                const title =
                  meta.title.length > 28
                    ? `${meta.title.slice(0, 27)}…`
                    : meta.title;
                return (
                  <g key={node.id} transform={`translate(${p.x}, ${p.y})`}>
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={6}
                      fill={fillFor(meta.kind, meta.current)}
                      stroke={strokeFor(meta.kind, meta.current)}
                      strokeWidth={meta.tip || meta.current ? 2 : 1}
                      opacity={meta.current ? 0.92 : 1}
                    />
                    <text
                      x={10}
                      y={20}
                      fill={
                        meta.current
                          ? theme.text.onAccent
                          : theme.text.primary
                      }
                      fontSize={11}
                      fontFamily="ui-monospace, monospace"
                    >
                      {node.id.slice(0, 8)} · {meta.kind}
                    </text>
                    <text
                      x={10}
                      y={38}
                      fill={
                        meta.current
                          ? theme.text.onAccent
                          : theme.text.secondary
                      }
                      fontSize={11}
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                    >
                      {title}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>Entries</CardHeader>
        <CardBody>
          <Stack gap={8}>
            {visible.map((node) => (
              <Row key={node.id} gap={8} align="center">
                <Pill tone={kindTone(node.kind)} size="small">
                  {node.kind}
                </Pill>
                <Text size="small" weight="medium" style={{ fontFamily: "ui-monospace, monospace" }}>
                  {node.id.slice(0, 8)}
                </Text>
                <Text size="small" style={{ flex: 1 }}>
                  {node.title}
                </Text>
                {node.current ? (
                  <Pill tone="info" size="small" active>
                    here
                  </Pill>
                ) : null}
              </Row>
            ))}
          </Stack>
        </CardBody>
      </Card>
    </Stack>
  );
}

export default MemoryGraphCanvas;
'''


def compact_graph(data: dict) -> dict:
    """Slim graph payload for embedding in a canvas (no detail blobs)."""
    nodes = []
    for node in data.get("nodes", []):
        message = node.get("message") or ""
        title = message.split("\n", 1)[0].strip()[:80] or node.get("kind", "step")
        nodes.append(
            {
                "id": node["id"],
                "kind": node.get("kind", "step"),
                "title": title,
                "tip": bool(node.get("tip")),
                "current": bool(node.get("current")),
                "folded": bool(node.get("folded")),
                "branches": list(node.get("branches") or []),
            }
        )
    edges = []
    for edge in data.get("edges", []):
        edges.append(
            {
                "from": edge["from"],
                "to": edge["to"],
                "kind": edge.get("kind", "parent"),
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "branches": list(data.get("branches") or []),
    }


def project_slug(root: Path) -> str:
    """Cursor project folder name for a workspace path."""
    resolved = root.resolve()
    text = str(resolved)
    if text.startswith("/"):
        text = text[1:]
    return text.replace("/", "-")


def default_canvas_dir(root: Path) -> Path:
    """IDE-managed canvases directory for this workspace."""
    override = os.environ.get("CURSOR_CANVAS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home()
        / ".cursor"
        / "projects"
        / project_slug(root)
        / "canvases"
    )


def render_canvas_tsx(data: dict) -> str:
    """Return `.canvas.tsx` source for the given graph_data payload."""
    payload = json.dumps(compact_graph(data), indent=2)
    # Avoid breaking out of a script context if a message ever contains </.
    payload = payload.replace("</", "<\\/")
    return CANVAS_TEMPLATE.replace("__DATA__", payload)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()) or "agent-memory-graph"
    if not cleaned.endswith(".canvas.tsx"):
        cleaned = cleaned.removesuffix(".tsx").removesuffix(".canvas")
        cleaned = f"{cleaned}.canvas.tsx"
    return cleaned


def write_canvas(
    memory,
    path: str | Path | None = None,
    *,
    name: str = "agent-memory-graph",
) -> Path:
    """Build graph_data and write a Cursor canvas file. Returns the path."""
    from agent_memory.viz import graph_data

    data = graph_data(memory)
    source = render_canvas_tsx(data)
    if path is not None:
        target = Path(path)
    else:
        target = default_canvas_dir(memory._path) / _safe_name(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    return target

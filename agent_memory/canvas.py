"""Write a Cursor `.canvas.tsx` from structured memory graph data."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

CANVAS_TEMPLATE = r'''import {
  Button,
  Card,
  CardBody,
  CardHeader,
  H1,
  H2,
  Pill,
  Row,
  Spacer,
  Stack,
  Stat,
  Text,
  computeDAGLayout,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type Kind = "summary" | "user" | "step" | "delete";
type ColorKey =
  | "blue"
  | "green"
  | "orange"
  | "purple"
  | "cyan"
  | "pink"
  | "yellow"
  | "gray";

type GraphNode = {
  id: string;
  kind: Kind;
  agent: string;
  color: ColorKey;
  title: string;
  tip: boolean;
  current: boolean;
  folded: boolean;
  branches: string[];
  committed_at: string;
  children: string[];
};

type GraphEdge = {
  from: string;
  to: string;
  kind: "parent" | "fold";
  fold?: string;
};

type AgentMeta = {
  name: string;
  color: ColorKey;
};

type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  branches: Array<{ name: string; tip: string; current: boolean }>;
  agents: AgentMeta[];
};

const GRAPH = __DATA__ as Graph;

const NODE_W = 188;
const NODE_H = 56;
const GANTT_ROW_H = 28;
const GANTT_BAR_H = 16;
const ALL_NODES = Object.fromEntries(GRAPH.nodes.map((n) => [n.id, n]));

function kindTone(kind: Kind): "neutral" | "info" | "warning" | "danger" {
  if (kind === "summary") return "warning";
  if (kind === "user") return "info";
  if (kind === "delete") return "danger";
  return "neutral";
}

function MemoryGraphCanvas() {
  const theme = useHostTheme();
  const [expandedIds, setExpandedIds] = useCanvasState<string[]>(
    "expanded-summaries",
    [],
  );
  const [focusId, setFocusId] = useCanvasState<string | null>("focus-id", null);

  const expanded = new Set(expandedIds);
  const revealed = new Set<string>();
  for (const id of expandedIds) {
    const node = ALL_NODES[id];
    if (!node?.children?.length) continue;
    for (const child of node.children) revealed.add(child);
  }

  const visible = GRAPH.nodes.filter((n) => !n.folded || revealed.has(n.id));
  const visibleIds = new Set(visible.map((n) => n.id));

  const layoutEdges = [
    ...GRAPH.edges.filter(
      (e) =>
        e.kind === "parent" &&
        visibleIds.has(e.from) &&
        visibleIds.has(e.to),
    ),
    ...GRAPH.edges.filter(
      (e) =>
        e.kind === "fold" &&
        e.fold &&
        expanded.has(e.fold) &&
        visibleIds.has(e.from) &&
        visibleIds.has(e.to),
    ),
  ];

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

  const agentColor = (key: ColorKey) =>
    theme.category?.[key] ?? theme.fill.primary;

  const focus = focusId ? ALL_NODES[focusId] : null;
  const focusChildren =
    focus?.children?.length
      ? focus.children.map((id) => ALL_NODES[id]).filter(Boolean)
      : [];

  const toggleSummary = (id: string) => {
    const node = ALL_NODES[id];
    if (!node?.children?.length) {
      setFocusId(id);
      return;
    }
    const open = expanded.has(id);
    setExpandedIds((prev) =>
      open ? prev.filter((x) => x !== id) : [...prev, id],
    );
    setFocusId(open ? null : id);
  };

  const timeline = [...visible].sort((a, b) =>
    a.committed_at < b.committed_at ? -1 : a.committed_at > b.committed_at ? 1 : 0,
  );
  const agentRows = GRAPH.agents.length
    ? GRAPH.agents
    : Array.from(new Set(visible.map((n) => n.agent))).map((name) => ({
        name,
        color: (visible.find((n) => n.agent === name)?.color ?? "gray") as ColorKey,
      }));
  const times = timeline
    .map((n) => Date.parse(n.committed_at))
    .filter((t) => !Number.isNaN(t));
  const tMin = times.length ? Math.min(...times) : 0;
  const tMax = times.length ? Math.max(...times) : 1;
  const span = Math.max(tMax - tMin, 1);
  const ganttW = 520;
  const ganttH = Math.max(agentRows.length, 1) * GANTT_ROW_H + 8;
  const laneOf = Object.fromEntries(agentRows.map((a, i) => [a.name, i]));
  const summaryCount = GRAPH.nodes.filter(
    (n) => n.kind === "summary" && n.children.length > 0,
  ).length;

  return (
    <Stack gap={16} style={{ padding: 16 }}>
      <Stack gap={6}>
        <H1>Agent Memory</H1>
        <Text tone="secondary" size="small">
          Shared memory · colored by agent · branch {currentBranch}
        </Text>
        <Text tone="secondary" size="small">
          Click a summary to expand the steps that led up to it.
        </Text>
      </Stack>

      <Row gap={12} style={{ flexWrap: "wrap" }}>
        <Stat value={String(visible.length)} label="Visible entries" />
        <Stat value={String(GRAPH.agents.length || agentRows.length)} label="Agents" />
        <Stat value={String(GRAPH.branches.length)} label="Branches" />
        <Stat value={String(summaryCount)} label="Expandable summaries" />
      </Row>

      {agentRows.length > 0 ? (
        <Stack gap={8}>
          <H2>Agents</H2>
          <Row gap={8} style={{ flexWrap: "wrap" }}>
            {agentRows.map((agent) => (
              <Pill key={agent.name} size="small" active={agent.name === "main"}>
                <span
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: 99,
                    background: agentColor(agent.color),
                    marginRight: 6,
                  }}
                />
                {agent.name}
              </Pill>
            ))}
          </Row>
        </Stack>
      ) : null}

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

      {focus && focusChildren.length > 0 ? (
        <Card>
          <CardHeader>Led up to · @{focus.agent}</CardHeader>
          <CardBody>
            <Stack gap={10}>
              <Row gap={8} align="center">
                <Text weight="medium" style={{ flex: 1 }}>
                  {focus.title}
                </Text>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setExpandedIds((prev) =>
                      prev.filter((x) => x !== focus.id),
                    );
                    setFocusId(null);
                  }}
                >
                  Collapse
                </Button>
              </Row>
              <Text tone="secondary" size="small">
                {focusChildren.length} folded step
                {focusChildren.length === 1 ? "" : "s"} under this summary
              </Text>
              {focusChildren.map((child, index) => (
                <Row key={child.id} gap={8} align="center">
                  <Text
                    size="small"
                    style={{ fontFamily: "ui-monospace, monospace", width: 24 }}
                  >
                    {index + 1}.
                  </Text>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 99,
                      background: agentColor(child.color),
                      flexShrink: 0,
                    }}
                  />
                  <Pill tone={kindTone(child.kind)} size="small">
                    {child.kind}
                  </Pill>
                  <Text
                    size="small"
                    weight="medium"
                    style={{ fontFamily: "ui-monospace, monospace" }}
                  >
                    @{child.agent}
                  </Text>
                  <Text size="small" style={{ flex: 1 }}>
                    {child.title}
                  </Text>
                </Row>
              ))}
            </Stack>
          </CardBody>
        </Card>
      ) : null}

      {timeline.length > 0 ? (
        <Card>
          <CardHeader>Who said what</CardHeader>
          <CardBody style={{ padding: 12, overflow: "auto" }}>
            <svg
              width={ganttW + 120}
              height={ganttH}
              viewBox={`0 0 ${ganttW + 120} ${ganttH}`}
              role="img"
              aria-label="Memory timeline by agent"
            >
              {agentRows.map((agent, i) => (
                <g key={agent.name}>
                  <text
                    x={0}
                    y={i * GANTT_ROW_H + 18}
                    fill={theme.text.secondary}
                    fontSize={11}
                    fontFamily="ui-monospace, monospace"
                  >
                    {agent.name.length > 14
                      ? `${agent.name.slice(0, 13)}…`
                      : agent.name}
                  </text>
                  <line
                    x1={100}
                    y1={i * GANTT_ROW_H + GANTT_ROW_H / 2}
                    x2={100 + ganttW}
                    y2={i * GANTT_ROW_H + GANTT_ROW_H / 2}
                    stroke={theme.stroke.tertiary}
                    strokeWidth={1}
                  />
                </g>
              ))}
              {timeline.map((node) => {
                const t = Date.parse(node.committed_at);
                if (Number.isNaN(t)) return null;
                const lane = laneOf[node.agent] ?? 0;
                const x = 100 + ((t - tMin) / span) * (ganttW - 12);
                return (
                  <rect
                    key={node.id}
                    x={x}
                    y={lane * GANTT_ROW_H + (GANTT_ROW_H - GANTT_BAR_H) / 2}
                    width={10}
                    height={GANTT_BAR_H}
                    rx={3}
                    fill={agentColor(node.color)}
                    opacity={node.kind === "summary" ? 1 : 0.85}
                    stroke={
                      focusId === node.id || node.current
                        ? theme.accent.primary
                        : theme.stroke.primary
                    }
                    strokeWidth={focusId === node.id || node.current ? 2 : 0.5}
                    style={{ cursor: node.children.length ? "pointer" : "default" }}
                    onClick={() => toggleSummary(node.id)}
                  >
                    <title>{`@${node.agent} · ${node.kind} · ${node.title}`}</title>
                  </rect>
                );
              })}
            </svg>
            <Spacer />
            <Text tone="secondary" size="small">
              Each tick is a memory entry on that agent&apos;s lane (time →).
            </Text>
          </CardBody>
        </Card>
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
                  strokeDasharray={
                    GRAPH.edges.some(
                      (e) =>
                        e.kind === "fold" &&
                        e.from === edge.from &&
                        e.to === edge.to,
                    )
                      ? "4 3"
                      : edge.isBackEdge
                        ? "4 3"
                        : undefined
                  }
                />
              ))}
              {layout.nodes.map((node) => {
                const meta = byId[node.id];
                if (!meta) return null;
                const p = pos[node.id];
                const title =
                  meta.title.length > 26
                    ? `${meta.title.slice(0, 25)}…`
                    : meta.title;
                const fill = agentColor(meta.color);
                const isFocus = focusId === meta.id;
                const isOpen = expanded.has(meta.id);
                const canExpand = meta.children.length > 0;
                return (
                  <g
                    key={node.id}
                    transform={`translate(${p.x}, ${p.y})`}
                    style={{ cursor: canExpand ? "pointer" : "default" }}
                    onClick={() => toggleSummary(meta.id)}
                  >
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={6}
                      fill={fill}
                      stroke={
                        isFocus || meta.current
                          ? theme.accent.primary
                          : theme.stroke.primary
                      }
                      strokeWidth={isFocus || meta.tip || meta.current ? 2 : 1}
                      opacity={meta.folded ? 0.75 : 0.92}
                    />
                    <text
                      x={10}
                      y={18}
                      fill={theme.text.primary}
                      fontSize={10}
                      fontFamily="ui-monospace, monospace"
                    >
                      @{meta.agent} · {meta.kind}
                      {canExpand ? (isOpen ? " · open" : " · +") : ""}
                    </text>
                    <text
                      x={10}
                      y={36}
                      fill={theme.text.primary}
                      fontSize={11}
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                    >
                      {title}
                    </text>
                    <text
                      x={10}
                      y={48}
                      fill={theme.text.secondary}
                      fontSize={9}
                      fontFamily="ui-monospace, monospace"
                    >
                      {node.id.slice(0, 8)}
                      {canExpand ? ` · ${meta.children.length} steps` : ""}
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
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 99,
                    background: agentColor(node.color),
                    flexShrink: 0,
                  }}
                />
                <Pill
                  tone={kindTone(node.kind)}
                  size="small"
                  active={focusId === node.id}
                  onClick={() => toggleSummary(node.id)}
                >
                  {node.kind}
                  {node.children.length
                    ? expanded.has(node.id)
                      ? " · open"
                      : " · +"
                    : ""}
                </Pill>
                <Text
                  size="small"
                  weight="medium"
                  style={{ fontFamily: "ui-monospace, monospace" }}
                >
                  @{node.agent}
                </Text>
                <Text
                  size="small"
                  style={{ fontFamily: "ui-monospace, monospace" }}
                >
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
                "agent": node.get("agent") or "main",
                "color": node.get("color") or "gray",
                "title": title,
                "tip": bool(node.get("tip")),
                "current": bool(node.get("current")),
                "folded": bool(node.get("folded")),
                "branches": list(node.get("branches") or []),
                "committed_at": node.get("committed_at") or "",
                "children": list(node.get("children") or []),
            }
        )
    edges = []
    for edge in data.get("edges", []):
        row = {
            "from": edge["from"],
            "to": edge["to"],
            "kind": edge.get("kind", "parent"),
        }
        if edge.get("fold"):
            row["fold"] = edge["fold"]
        edges.append(row)
    agents = []
    for row in data.get("agents") or []:
        agents.append(
            {
                "name": row.get("name") or "main",
                "color": row.get("color") or "gray",
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "branches": list(data.get("branches") or []),
        "agents": agents,
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

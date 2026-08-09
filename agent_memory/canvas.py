"""Write a Cursor `.canvas.tsx` from structured memory graph data."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Max chars of entry body embedded per node (keeps canvas source bounded).
_MAX_BODY = 16_000

# Click any node → entry (message + body). Summaries offer Open subgraph.
# Classic text breadcrumbs: Live › nest › entry
CANVAS_TEMPLATE = r'''import {
  Button,
  H1,
  Pill,
  Row,
  Stack,
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
  body: string;
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

type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  branches: Array<{ name: string; tip: string; current: boolean }>;
  agents: Array<{ name: string; color: ColorKey }>;
};

const GRAPH = __DATA__ as Graph;
const ALL = Object.fromEntries(GRAPH.nodes.map((n) => [n.id, n]));

const NODE_W = 280;
const NODE_H = 72;
const ACCENT_W = 3;

function formatWhen(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso.slice(0, 16).replace("T", " ");
  }
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${months[d.getUTCMonth()]} ${dd}, ${hh}:${mm}:${ss} UTC`;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function Crumb({
  label,
  current,
  onClick,
  theme,
}: {
  label: string;
  current: boolean;
  onClick?: () => void;
  theme: ReturnType<typeof useHostTheme>;
}) {
  return (
    <span
      onClick={current ? undefined : onClick}
      style={{
        cursor: current || !onClick ? "default" : "pointer",
        color: current ? theme.text.primary : theme.text.secondary,
        fontSize: 12,
        fontWeight: current ? 600 : 400,
        fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

function MemoryGraphCanvas() {
  const theme = useHostTheme();
  const [trail, setTrail] = useCanvasState<string[]>("drill-trail", []);
  const [selected, setSelected] = useCanvasState<string | null>(
    "selected-entry",
    null,
  );

  const currentBranch =
    GRAPH.branches.find((b) => b.current)?.name ?? "main";
  const kindColor = (kind: Kind) => {
    const cat = theme.category;
    if (kind === "summary") return cat?.orange ?? theme.accent.primary;
    if (kind === "user") return cat?.cyan ?? theme.fill.primary;
    if (kind === "delete") return cat?.pink ?? theme.fill.tertiary;
    return cat?.blue ?? theme.fill.secondary; // normal commits / steps
  };
  const nodeFill = theme.bg?.elevated ?? theme.fill.secondary;
  const nodeStroke = theme.stroke.secondary;

  const focusId = trail.length ? trail[trail.length - 1] : null;
  const focus = focusId ? ALL[focusId] : null;
  const entry = selected ? ALL[selected] : null;

  let visible: GraphNode[];
  let layoutEdges: GraphEdge[];

  if (focus?.children?.length) {
    // Cover order is oldest→newest; reverse so newest is first / on top.
    const childIds = focus.children.filter((id) => ALL[id]).slice().reverse();
    visible = childIds.map((id) => ALL[id]);
    layoutEdges = [];
    for (let i = 0; i < childIds.length - 1; i++) {
      layoutEdges.push({
        from: childIds[i],
        to: childIds[i + 1],
        kind: "fold",
        fold: focus.id,
      });
    }
  } else {
    visible = GRAPH.nodes
      .filter((n) => !n.folded)
      .slice()
      .sort((a, b) =>
        (b.committed_at || "").localeCompare(a.committed_at || ""),
      );
    const ids = new Set(visible.map((n) => n.id));
    // Parent edges are older→newer; flip for layout so newest ranks on top.
    layoutEdges = GRAPH.edges
      .filter(
        (e) =>
          e.kind === "parent" && ids.has(e.from) && ids.has(e.to),
      )
      .map((e) => ({
        from: e.to,
        to: e.from,
        kind: e.kind,
        fold: e.fold,
      }));
  }

  const layout = computeDAGLayout({
    nodes: visible.map((n) => ({ id: n.id })),
    edges: layoutEdges.map((e) => ({ from: e.from, to: e.to })),
    direction: "vertical",
    nodeWidth: NODE_W,
    nodeHeight: NODE_H,
    rankGap: 40,
    nodeGap: 20,
    padding: 20,
  });

  const byId = Object.fromEntries(visible.map((n) => [n.id, n]));
  const pos = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));

  const openEntry = (id: string) => setSelected(id);

  const openSubgraph = (id: string) => {
    const node = ALL[id];
    if (!node?.children?.length) return;
    setSelected(null);
    setTrail((prev) => [...prev, id]);
  };

  const goRoot = () => {
    setSelected(null);
    setTrail([]);
  };

  const goTo = (index: number) => {
    setSelected(null);
    if (index < 0) {
      setTrail([]);
      return;
    }
    setTrail((prev) => prev.slice(0, index + 1));
  };

  const crumbLabel = (id: string) => {
    const node = ALL[id];
    if (!node) return id.slice(0, 8);
    return truncate(node.title, 36);
  };

  const atGraphRoot = !focus && !entry;

  const crumbs: Array<{
    key: string;
    label: string;
    current: boolean;
    onClick?: () => void;
  }> = [
    {
      key: "live",
      label: "Live",
      current: atGraphRoot,
      onClick: goRoot,
    },
  ];
  trail.forEach((id, index) => {
    crumbs.push({
      key: id,
      label: crumbLabel(id),
      current: !entry && index === trail.length - 1,
      onClick: () => goTo(index),
    });
  });
  if (entry) {
    crumbs.push({
      key: `entry-${entry.id}`,
      label: crumbLabel(entry.id),
      current: true,
    });
  }

  const metaFor = (node: GraphNode) =>
    [
      node.agent,
      node.id.slice(0, 8),
      node.committed_at ? formatWhen(node.committed_at) : null,
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <Stack gap={16} style={{ padding: 16 }}>
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 6,
          flexWrap: "nowrap",
          overflowX: "auto",
          minHeight: 20,
        }}
      >
        {crumbs.map((crumb, index) => (
          <div
            key={crumb.key}
            style={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: 6,
              flexShrink: 0,
            }}
          >
            {index > 0 ? (
              <span
                style={{
                  color: theme.text.tertiary,
                  fontSize: 12,
                }}
              >
                /
              </span>
            ) : null}
            <Crumb
              label={crumb.label}
              current={crumb.current}
              onClick={crumb.onClick}
              theme={theme}
            />
          </div>
        ))}
      </div>

      {entry ? (
        <Stack gap={12}>
          <Stack gap={8}>
            <H1>{entry.title}</H1>
            <Text tone="tertiary" size="small">
              {metaFor(entry)}
            </Text>
            {entry.children.length > 0 ? (
              <div>
                <Button
                  variant="secondary"
                  onClick={() => openSubgraph(entry.id)}
                >
                  Subgraph ({entry.children.length})
                </Button>
              </div>
            ) : null}
          </Stack>
          <Text
            style={{
              whiteSpace: "pre-wrap",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 12,
              lineHeight: 1.5,
            }}
          >
            {entry.body || entry.title}
          </Text>
        </Stack>
      ) : (
        <Stack gap={12}>
          {atGraphRoot ? (
            <Stack gap={2}>
              <H1>Memory</H1>
              <Text tone="tertiary" size="small">
                {currentBranch}
              </Text>
            </Stack>
          ) : null}

          {atGraphRoot ? (
            <Row gap={8} style={{ flexWrap: "wrap" }}>
              {(
                [
                  ["summary", "summary"],
                  ["step", "commit"],
                  ["user", "user"],
                ] as Array<[Kind, string]>
              ).map(([kind, label]) => (
                <Pill key={kind} size="small">
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: 99,
                      background: kindColor(kind),
                      marginRight: 6,
                    }}
                  />
                  {label}
                </Pill>
              ))}
            </Row>
          ) : null}

          {visible.length === 0 ? (
            <Text tone="secondary">Nothing here yet.</Text>
          ) : (
            <svg
              width={layout.width}
              height={layout.height}
              viewBox={`0 0 ${layout.width} ${layout.height}`}
              role="img"
              aria-label={focus ? "Summary subgraph" : "Live memory graph"}
            >
              {layout.edges.map((edge, i) => (
                <line
                  key={`${edge.from}-${edge.to}-${i}`}
                  x1={edge.sourceX}
                  y1={edge.sourceY}
                  x2={edge.targetX}
                  y2={edge.targetY}
                  stroke={theme.stroke.tertiary}
                  strokeWidth={1}
                  strokeDasharray={focus ? "3 4" : undefined}
                />
              ))}
              {layout.nodes.map((node) => {
                const meta = byId[node.id];
                if (!meta) return null;
                const p = pos[node.id];
                const title = truncate(meta.title, 34);
                const canDrill = meta.children.length > 0;
                const when = formatWhen(meta.committed_at);
                const metaLine = [
                  meta.agent,
                  canDrill ? `${meta.children.length}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                const accent = kindColor(meta.kind);
                const barW = meta.kind === "summary" ? 4 : ACCENT_W;
                const emphasized = meta.current || meta.tip;
                // Current tip uses the accent border only — a left kind bar
                // next to that border reads as a double line.
                const showKindBar = !meta.current;
                return (
                  <g
                    key={node.id}
                    transform={`translate(${p.x}, ${p.y})`}
                    style={{ cursor: "pointer" }}
                    onClick={() => openEntry(meta.id)}
                  >
                    <rect
                      width={NODE_W}
                      height={NODE_H}
                      rx={8}
                      fill={nodeFill}
                      stroke={
                        meta.current
                          ? theme.accent.primary
                          : nodeStroke
                      }
                      strokeWidth={emphasized ? 1.5 : 1}
                    />
                    {showKindBar ? (
                      <>
                        <rect
                          x={0}
                          y={0}
                          width={barW}
                          height={NODE_H}
                          fill={accent}
                        />
                        <rect
                          x={barW}
                          y={0}
                          width={1}
                          height={NODE_H}
                          fill={nodeFill}
                        />
                      </>
                    ) : null}
                    <text
                      x={14}
                      y={22}
                      fill={theme.text.primary}
                      fontSize={12}
                      fontWeight={600}
                      fontFamily="ui-sans-serif, system-ui, -apple-system, sans-serif"
                    >
                      {title}
                    </text>
                    <text
                      x={14}
                      y={40}
                      fill={theme.text.secondary}
                      fontSize={10}
                      fontFamily="ui-sans-serif, system-ui, -apple-system, sans-serif"
                    >
                      {metaLine}
                    </text>
                    <text
                      x={14}
                      y={58}
                      fill={theme.text.tertiary}
                      fontSize={10}
                      fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                    >
                      {when || meta.id.slice(0, 8)}
                      {when ? ` · ${meta.id.slice(0, 8)}` : ""}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
        </Stack>
      )}
    </Stack>
  );
}



export default MemoryGraphCanvas;
'''


def compact_graph(data: dict) -> dict:
    """Slim graph payload for embedding in a canvas."""
    nodes = []
    for node in data.get("nodes", []):
        message = node.get("message") or ""
        title = message.split("\n", 1)[0].strip()[:80] or node.get("kind", "step")
        body = message
        if len(body) > _MAX_BODY:
            body = body[: _MAX_BODY - 1] + "…"
        nodes.append(
            {
                "id": node["id"],
                "kind": node.get("kind", "step"),
                "agent": node.get("agent") or "agent",
                "color": node.get("color") or "gray",
                "title": title,
                "body": body,
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
                "name": row.get("name") or "agent",
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


def _cursor_project_candidates(root: Path) -> list[str]:
    """Possible ~/.cursor/projects/<slug> names for this workspace.

    Cursor sometimes normalizes `_` to `-` in project folder names, so a
    path like `.../agent_memory` may live under `...-agent-memory`.
    """
    slug = project_slug(root)
    names = [slug]
    alt = slug.replace("_", "-")
    if alt != slug:
        names.append(alt)
    return names


def default_canvas_dir(root: Path) -> Path:
    """IDE-managed canvases directory for this workspace."""
    override = os.environ.get("CURSOR_CANVAS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    projects = Path.home() / ".cursor" / "projects"
    best: Path | None = None
    best_score = -1
    for name in _cursor_project_candidates(root):
        base = projects / name
        score = 0
        if (base / "terminals").is_dir():
            score += 10
        if (base / "agent-transcripts").is_dir():
            score += 5
        if (base / "canvases").is_dir():
            score += 2
        if score > best_score:
            best_score = score
            best = base / "canvases"
    if best is not None and best_score > 0:
        return best
    return projects / project_slug(root) / "canvases"


def render_canvas_tsx(data: dict) -> str:
    """Return `.canvas.tsx` source for the given graph_data payload."""
    payload = json.dumps(compact_graph(data), indent=2)
    payload = payload.replace("</", "<\\/")
    return CANVAS_TEMPLATE.replace("__DATA__", payload)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()) or "agent-memory-live"
    if not cleaned.endswith(".canvas.tsx"):
        cleaned = cleaned.removesuffix(".tsx").removesuffix(".canvas")
        cleaned = f"{cleaned}.canvas.tsx"
    return cleaned


def write_canvas(
    memory,
    path: str | Path | None = None,
    *,
    name: str = "agent-memory-live",
) -> Path:
    """Build graph_data and write the live Cursor canvas. Returns the path."""
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

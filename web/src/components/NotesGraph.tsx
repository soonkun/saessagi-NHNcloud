import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { KnowledgeGraphData } from "../types";
import { useStore } from "../store";

interface Props {
  data: KnowledgeGraphData;
  onNodeClick: (slug: string) => void;
}

interface RFNode {
  id: string;
  title: string;
  tags: string[];
  degree: number;
  x?: number;
  y?: number;
}

interface RFLink {
  source: string | RFNode;
  target: string | RFNode;
  kind: "wikilink" | "tag" | "doc";
}

// 테마에 따라 색상 결정 — accent 단일톤 + 명도 변화
function readCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function getThemePalette(theme: "dark" | "light"): {
  bg: string;
  node: string;
  nodeStroke: string;
  nodeActive: string;
  nodeDim: string;
  label: string;
  labelActive: string;
  labelDim: string;
  link: string;
  linkActive: string;
  linkDim: string;
  grid: string;
} {
  const accent = readCssVar("--color-accent") || "#c96442";
  if (theme === "light") {
    return {
      bg: "#fafbfd",
      node: "#9ba3b0",
      nodeStroke: "#ffffff",
      nodeActive: accent,
      nodeDim: "#dde1e7",
      label: "#3a3d44",
      labelActive: "#1f2024",
      labelDim: "#c1c5cd",
      link: "rgba(120,130,145,0.45)",
      linkActive: accent,
      linkDim: "rgba(180,185,195,0.18)",
      grid: "rgba(0,0,0,0.04)",
    };
  }
  return {
    bg: "#16181c",
    node: "#7f8694",
    nodeStroke: "#16181c",
    nodeActive: accent,
    nodeDim: "#2a2d33",
    label: "#c8ccd2",
    labelActive: "#ffffff",
    labelDim: "#444851",
    link: "rgba(160,165,180,0.32)",
    linkActive: accent,
    linkDim: "rgba(120,125,140,0.10)",
    grid: "rgba(255,255,255,0.04)",
  };
}

// 노드의 degree(연결 수)를 계산해 시각 가중치로 사용
function computeDegrees(
  nodes: KnowledgeGraphData["nodes"],
  edges: KnowledgeGraphData["edges"]
): Map<string, number> {
  const m = new Map<string, number>();
  for (const n of nodes) m.set(n.slug, 0);
  for (const e of edges) {
    m.set(e.source, (m.get(e.source) ?? 0) + 1);
    m.set(e.target, (m.get(e.target) ?? 0) + 1);
  }
  return m;
}

export default function NotesGraph({ data, onNodeClick }: Props): React.ReactElement {
  const theme = useStore((s) => s.theme);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 400, h: 300 });
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [, setRev] = useState(0); // 테마 변경 시 강제 리렌더

  const palette = useMemo(() => getThemePalette(theme), [theme]);

  // 테마 전환 시 한 번 더 리렌더 (다음 paint cycle에 CSS 변수가 반영되도록)
  useEffect(() => {
    const t = setTimeout(() => setRev((r) => r + 1), 16);
    return () => clearTimeout(t);
  }, [theme]);

  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const { graphData, neighbors } = useMemo(() => {
    const degrees = computeDegrees(data.nodes, data.edges);
    const nodes: RFNode[] = data.nodes.map((n) => ({
      id: n.slug,
      title: n.title,
      tags: n.tags,
      degree: degrees.get(n.slug) ?? 0,
    }));
    const links: RFLink[] = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind,
    }));
    // 노드별 이웃 집합 — 호버 시 강조용
    const neighbors = new Map<string, Set<string>>();
    for (const n of nodes) neighbors.set(n.id, new Set());
    for (const e of data.edges) {
      neighbors.get(e.source)?.add(e.target);
      neighbors.get(e.target)?.add(e.source);
    }
    return { graphData: { nodes, links }, neighbors };
  }, [data]);

  // 노드 반지름 — degree 기반, 4~10
  const radiusFor = useCallback((degree: number): number => {
    return 4 + Math.min(6, Math.sqrt(degree) * 1.6);
  }, []);

  const isActive = useCallback(
    (id: string): boolean => {
      if (!hoveredNodeId) return true; // 호버 없을 때는 모두 active
      if (id === hoveredNodeId) return true;
      return neighbors.get(hoveredNodeId)?.has(id) ?? false;
    },
    [hoveredNodeId, neighbors]
  );

  const isLinkActive = useCallback(
    (l: RFLink): boolean => {
      if (!hoveredNodeId) return true;
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      return s === hoveredNodeId || t === hoveredNodeId;
    },
    [hoveredNodeId]
  );

  return (
    <div
      ref={wrapRef}
      style={{
        flex: 1,
        position: "relative",
        overflow: "hidden",
        background: palette.bg,
      }}
    >
      {graphData.nodes.length === 0 ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            color: palette.labelDim,
            fontSize: 13,
          }}
        >
          노트가 없습니다.
        </div>
      ) : (
        <ForceGraph2D
          width={size.w}
          height={size.h}
          graphData={graphData}
          backgroundColor={palette.bg}
          // 물리 시뮬레이션 부드럽게
          d3AlphaDecay={0.022}
          d3VelocityDecay={0.32}
          cooldownTime={4000}
          // 노드 & 링크는 커스텀 캔버스 페인터로 완전 제어
          nodeRelSize={1}
          nodeLabel={(n) => (n as unknown as RFNode).title}
          linkSource="source"
          linkTarget="target"
          // 호버 콜백
          onNodeHover={(node) => {
            const id = node ? (node as unknown as RFNode).id : null;
            setHoveredNodeId(id);
            if (wrapRef.current) {
              wrapRef.current.style.cursor = id ? "pointer" : "default";
            }
          }}
          onNodeClick={(node) => {
            const n = node as unknown as RFNode;
            onNodeClick(n.id);
          }}
          // 배경 — 미세한 도트 그리드
          onRenderFramePre={(ctx, scale) => {
            const w = size.w;
            const h = size.h;
            // 화면 좌표계로 그리기 위해 transform 임시 reset
            ctx.save();
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.fillStyle = palette.bg;
            ctx.fillRect(0, 0, w, h);
            // 줌 레벨에 따라 도트 간격
            const step = Math.max(28, Math.min(64, 32 * (1 / scale)));
            ctx.fillStyle = palette.grid;
            for (let x = (step / 2) % step; x < w; x += step) {
              for (let y = (step / 2) % step; y < h; y += step) {
                ctx.beginPath();
                ctx.arc(x, y, 0.7, 0, Math.PI * 2);
                ctx.fill();
              }
            }
            ctx.restore();
          }}
          // 링크 — beforeCanvasObject로 노드 뒤에 그리도록 ForceGraph 내부에서 처리
          linkColor={(l) => {
            const link = l as unknown as RFLink;
            if (!isLinkActive(link)) return palette.linkDim;
            return hoveredNodeId ? palette.linkActive : palette.link;
          }}
          linkWidth={(l) => {
            const link = l as unknown as RFLink;
            if (!isLinkActive(link)) return 0.6;
            return hoveredNodeId ? 1.4 : 0.9;
          }}
          linkLineDash={(l) => {
            const link = l as unknown as RFLink;
            // tag 공유는 미묘하게 점선
            return link.kind === "tag" ? [3, 4] : null;
          }}
          // 노드 — 외곽선 있는 원 + 라벨
          nodeCanvasObject={(rawNode, ctx, scale) => {
            const n = rawNode as unknown as RFNode;
            if (n.x === undefined || n.y === undefined) return;
            const active = isActive(n.id);
            const r = radiusFor(n.degree);

            // 외곽 글로우 (액티브일 때만)
            if (active && hoveredNodeId === n.id) {
              ctx.beginPath();
              ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
              const gradient = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 5);
              gradient.addColorStop(0, palette.nodeActive + "55");
              gradient.addColorStop(1, palette.nodeActive + "00");
              ctx.fillStyle = gradient;
              ctx.fill();
            }

            // 노드 본체
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            const fillColor = active
              ? hoveredNodeId === n.id
                ? palette.nodeActive
                : hoveredNodeId
                  ? palette.nodeActive
                  : palette.node
              : palette.nodeDim;
            ctx.fillStyle = fillColor;
            ctx.fill();

            // 외곽선 — 배경과 동일한 색으로 분리감
            ctx.lineWidth = 1.2;
            ctx.strokeStyle = palette.nodeStroke;
            ctx.stroke();

            // 라벨 — 줌 레벨이 낮으면 생략 (글자 깨짐 방지)
            if (scale < 0.6 && !active) return;

            const fontSize = Math.max(10, 12 / Math.max(scale, 0.85));
            ctx.font =
              `${hoveredNodeId === n.id ? 600 : 400} ${fontSize}px ` +
              `-apple-system, "Pretendard", "Apple SD Gothic Neo", sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";

            const labelColor = active
              ? hoveredNodeId === n.id
                ? palette.labelActive
                : palette.label
              : palette.labelDim;
            ctx.fillStyle = labelColor;
            // 약간의 그림자로 가독성 보강
            if (theme === "dark" && active) {
              ctx.shadowColor = "rgba(0,0,0,0.6)";
              ctx.shadowBlur = 4;
            } else {
              ctx.shadowBlur = 0;
            }
            ctx.fillText(n.title, n.x, n.y + r + 4);
            ctx.shadowBlur = 0;
          }}
          nodePointerAreaPaint={(node, color, ctx) => {
            const n = node as unknown as RFNode & { x?: number; y?: number };
            if (n.x === undefined || n.y === undefined) return;
            const r = radiusFor(n.degree);
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
            ctx.fill();
          }}
        />
      )}

      {/* 좌하단 범례 */}
      <div
        style={{
          position: "absolute",
          left: 12,
          bottom: 12,
          fontSize: 10,
          color: palette.labelDim,
          background: theme === "dark" ? "rgba(22,24,28,0.7)" : "rgba(255,255,255,0.85)",
          border: `1px solid var(--color-border)`,
          borderRadius: 6,
          padding: "6px 8px",
          display: "flex",
          gap: 10,
          alignItems: "center",
          backdropFilter: "blur(4px)",
          pointerEvents: "none",
        }}
      >
        <LegendItem color={palette.link} label="위키링크" />
        <LegendItem color={palette.link} label="태그" dashed />
        <LegendItem color={palette.link} label="문서 공유" />
      </div>
    </div>
  );
}

function LegendItem({
  color,
  label,
  dashed = false,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}): React.ReactElement {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span
        style={{
          width: 18,
          height: 2,
          background: dashed
            ? `repeating-linear-gradient(90deg, ${color} 0 3px, transparent 3px 6px)`
            : color,
          display: "inline-block",
        }}
      />
      {label}
    </span>
  );
}

// M_19 GraphRAG 지식그래프 탭 (CR-18)
// 문서·엔티티·노트 통합 그래프 + 채팅 답변 근거(evidence) 하이라이트.
// 캔버스 페인터 스타일은 NotesGraph.tsx의 패턴을 따른다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { Network, RefreshCw, X } from "lucide-react";
import type { GraphRagData, GraphRagEvidence, GraphRagStatus, GraphRagNode } from "../types";
import {
  fetchGraphEvidence,
  fetchGraphRag,
  fetchGraphRagStatus,
  requestGraphReindex,
} from "../services/api";
import { useStore } from "../store";

// 엔티티 타입별 팔레트 (다크/라이트 공용 — 채도 낮춘 7색)
const TYPE_COLORS: Record<string, string> = {
  인물: "#e07a5f",
  조직: "#5f8fe0",
  사업: "#56b380",
  제도: "#b07fd8",
  기술: "#d8a44f",
  장소: "#4fb8c9",
  기타: "#9aa0ab",
};

const ENTITY_TYPES = ["인물", "조직", "사업", "제도", "기술", "장소", "기타"];

interface RFNode extends GraphRagNode {
  degree: number;
  x?: number;
  y?: number;
}

interface RFLink {
  source: string | RFNode;
  target: string | RFNode;
  kind: string;
  weight: number;
}

function readCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export default function GraphRagView(): React.ReactElement {
  const theme = useStore((s) => s.theme);
  const chatTab = useStore((s) => s.chatTab);
  const evidenceReq = useStore((s) => s.graphEvidenceReq);
  const setChatTab = useStore((s) => s.setChatTab);
  const setSelectedNoteSlug = useStore((s) => s.setSelectedNoteSlug);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 400, h: 300 });
  const [data, setData] = useState<GraphRagData | null>(null);
  const [status, setStatus] = useState<GraphRagStatus | null>(null);
  const [error, setError] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<GraphRagEvidence | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selected, setSelected] = useState<RFNode | null>(null);
  const [reindexing, setReindexing] = useState(false);

  const isDark = theme === "dark";
  const accent = readCssVar("--color-accent") || "#c96442";
  const bg = isDark ? "#16181c" : "#fafbfd";

  // ── 데이터 로드 ────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      setError("");
      const [g, s] = await Promise.all([
        fetchGraphRag(500, typeFilter),
        fetchGraphRagStatus(),
      ]);
      setData(g);
      setStatus(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [typeFilter]);

  useEffect(() => {
    if (chatTab === "graph") void load();
  }, [chatTab, load]);

  // 인덱싱 진행 중이면 3초 폴링
  const hasActiveIndexing =
    status?.indexing.some((i) => i.state === "pending" || i.state === "running") ?? false;
  useEffect(() => {
    if (chatTab !== "graph" || !hasActiveIndexing) return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [chatTab, hasActiveIndexing, load]);

  // 근거 하이라이트 요청 (채팅 "근거 그래프" 버튼)
  useEffect(() => {
    if (evidenceReq === 0) return;
    void (async () => {
      const ev = await fetchGraphEvidence();
      setEvidence(ev);
    })();
  }, [evidenceReq]);

  // ── 그래프 데이터 변환 ─────────────────────────────────────────────────────
  const { graphData, neighbors } = useMemo(() => {
    const nodes: RFNode[] = (data?.nodes ?? []).map((n) => ({ ...n, degree: 0 }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links: RFLink[] = [];
    const neighbors = new Map<string, Set<string>>();
    for (const e of data?.edges ?? []) {
      if (!byId.has(e.source) || !byId.has(e.target)) continue;
      links.push({ source: e.source, target: e.target, kind: e.kind, weight: e.weight });
      byId.get(e.source)!.degree += 1;
      byId.get(e.target)!.degree += 1;
      if (!neighbors.has(e.source)) neighbors.set(e.source, new Set());
      if (!neighbors.has(e.target)) neighbors.set(e.target, new Set());
      neighbors.get(e.source)!.add(e.target);
      neighbors.get(e.target)!.add(e.source);
    }
    return { graphData: { nodes, links }, neighbors };
  }, [data]);

  const evidenceIds = useMemo(() => {
    if (!evidence) return null;
    return new Set(evidence.nodes.map((n) => n.id));
  }, [evidence]);

  // ── 크기 추적 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // ── 노드 활성 판정: evidence 모드 > 호버 ──────────────────────────────────
  const isActive = useCallback(
    (id: string): boolean => {
      if (evidenceIds) return evidenceIds.has(id);
      if (!hoveredNodeId) return true;
      if (id === hoveredNodeId) return true;
      return neighbors.get(hoveredNodeId)?.has(id) ?? false;
    },
    [evidenceIds, hoveredNodeId, neighbors]
  );

  const nodeColor = useCallback(
    (n: RFNode): string => {
      if (n.kind === "document") return isDark ? "#8892a5" : "#6b7688";
      if (n.kind === "note") return accent;
      return TYPE_COLORS[n.type] ?? TYPE_COLORS["기타"];
    },
    [accent, isDark]
  );

  const radiusFor = useCallback(
    (n: RFNode): number => 4 + Math.min(7, Math.sqrt(n.degree) * 1.7),
    []
  );

  const handleNodeClick = useCallback(
    (raw: unknown) => {
      const n = raw as RFNode;
      if (n.kind === "document") {
        setChatTab("documents");
      } else if (n.kind === "note") {
        setSelectedNoteSlug(n.id);
        setChatTab("notes");
      } else {
        setSelected(n);
      }
    },
    [setChatTab, setSelectedNoteSlug]
  );

  // ── 렌더 ──────────────────────────────────────────────────────────────────
  const notConnected = status !== null && (!status.enabled || !status.connected);
  const stats = data?.stats ?? status?.stats ?? {};

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
      {/* 상단 컨트롤 바 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 10px",
          borderBottom: "1px solid var(--color-border)",
          flexWrap: "wrap",
          flexShrink: 0,
        }}
      >
        <Network size={14} style={{ color: "var(--color-accent)" }} />
        <span style={{ fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>
          엔티티 {stats.entities ?? 0} · 관계 {stats.relations ?? 0} · 문서 {stats.documents ?? 0} ·
          노트 {stats.notes ?? 0}
        </span>

        {ENTITY_TYPES.map((t) => {
          const on = typeFilter.length === 0 || typeFilter.includes(t);
          return (
            <button
              key={t}
              onClick={() =>
                setTypeFilter((prev) => {
                  const base = prev.length === 0 ? [...ENTITY_TYPES] : [...prev];
                  const next = base.includes(t) ? base.filter((x) => x !== t) : [...base, t];
                  return next.length === ENTITY_TYPES.length ? [] : next;
                })
              }
              style={{
                fontSize: "var(--fs-11)",
                padding: "1px 7px",
                borderRadius: 9,
                border: `1px solid ${on ? TYPE_COLORS[t] : "var(--color-border)"}`,
                background: on ? TYPE_COLORS[t] + (isDark ? "33" : "22") : "transparent",
                color: on ? "var(--color-text)" : "var(--color-text-muted)",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {t}
            </button>
          );
        })}

        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {hasActiveIndexing && (
            <span style={{ fontSize: "var(--fs-11)", color: "var(--color-accent)" }}>
              인덱싱 중…{" "}
              {status?.indexing
                .filter((i) => i.state === "running")
                .map((i) => `${i.done_chunks}/${i.total_chunks}`)
                .join(" ")}
            </span>
          )}
          <button
            onClick={() => {
              setReindexing(true);
              void requestGraphReindex()
                .then(() => load())
                .finally(() => setReindexing(false));
            }}
            disabled={reindexing || notConnected}
            title="모든 문서·노트를 그래프로 재인덱싱"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--fs-11)",
              padding: "3px 8px",
              borderRadius: 6,
              border: "1px solid var(--color-border)",
              background: "var(--color-bg)",
              color: "var(--color-text)",
              cursor: notConnected ? "not-allowed" : "pointer",
              fontFamily: "inherit",
              opacity: notConnected ? 0.5 : 1,
            }}
          >
            <RefreshCw size={11} className={reindexing ? "spin" : undefined} />
            재인덱싱
          </button>
        </div>
      </div>

      {/* evidence 모드 배너 */}
      {evidence && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            background: "var(--chip-doc-bg)",
            borderBottom: "1px solid var(--color-border)",
            fontSize: "var(--fs-12)",
            flexShrink: 0,
          }}
        >
          <span style={{ color: "var(--color-accent)", fontWeight: 600 }}>근거 그래프</span>
          <span
            style={{
              color: "var(--color-text-muted)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}
          >
            “{evidence.query}” — 매칭 개체·경유 관계·출처만 강조 표시
          </span>
          <button
            onClick={() => setEvidence(null)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 2,
              border: "none",
              background: "transparent",
              color: "var(--color-text-muted)",
              cursor: "pointer",
              fontSize: "var(--fs-11)",
              fontFamily: "inherit",
            }}
          >
            <X size={12} /> 전체 보기
          </button>
        </div>
      )}

      {/* 본문 */}
      <div ref={wrapRef} style={{ flex: 1, position: "relative", overflow: "hidden", background: bg }}>
        {notConnected ? (
          <CenterHint>
            GraphRAG가 비활성이거나 Neo4j에 연결할 수 없습니다.
            <br />
            conf.yaml의 <code>graphrag.enabled</code>와 Neo4j 상태를 확인하세요 (install.md 참조).
          </CenterHint>
        ) : error ? (
          <CenterHint>그래프 로드 실패: {error}</CenterHint>
        ) : graphData.nodes.length === 0 ? (
          <CenterHint>
            아직 그래프가 비어 있습니다.
            <br />
            문서를 업로드하거나 우상단 ‘재인덱싱’으로 기존 문서를 분석하세요.
          </CenterHint>
        ) : (
          <ForceGraph2D
            width={size.w}
            height={size.h}
            graphData={graphData}
            backgroundColor={bg}
            d3AlphaDecay={0.022}
            d3VelocityDecay={0.32}
            cooldownTime={4000}
            nodeRelSize={1}
            nodeLabel={(n) => (n as RFNode).label}
            onNodeHover={(node) => {
              const id = node ? (node as RFNode).id : null;
              setHoveredNodeId(id);
              if (wrapRef.current) wrapRef.current.style.cursor = id ? "pointer" : "default";
            }}
            onNodeClick={handleNodeClick}
            linkColor={(l) => {
              const link = l as RFLink;
              const s = typeof link.source === "string" ? link.source : link.source.id;
              const t = typeof link.target === "string" ? link.target : link.target.id;
              const active = isActive(s) && isActive(t);
              if (!active) return isDark ? "rgba(120,125,140,0.06)" : "rgba(180,185,195,0.12)";
              return link.kind === "rel"
                ? accent + (isDark ? "88" : "77")
                : isDark
                  ? "rgba(160,165,180,0.30)"
                  : "rgba(120,130,145,0.40)";
            }}
            linkWidth={(l) => {
              const link = l as RFLink;
              const s = typeof link.source === "string" ? link.source : link.source.id;
              const t = typeof link.target === "string" ? link.target : link.target.id;
              if (!(isActive(s) && isActive(t))) return 0.5;
              return link.kind === "rel" ? Math.min(3, 0.8 + link.weight * 0.3) : 0.8;
            }}
            linkLineDash={(l) => ((l as RFLink).kind === "mentioned_in" ? [2, 3] : null)}
            nodeCanvasObject={(rawNode, ctx, scale) => {
              const n = rawNode as RFNode;
              if (n.x === undefined || n.y === undefined) return;
              const active = isActive(n.id);
              const r = radiusFor(n);
              const color = nodeColor(n);
              const dimColor = isDark ? "#2a2d33" : "#dde1e7";

              // evidence 모드에서 근거 노드는 발광
              if (active && evidenceIds) {
                ctx.beginPath();
                ctx.arc(n.x, n.y, r + 6, 0, Math.PI * 2);
                const g = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 6);
                g.addColorStop(0, color + "66");
                g.addColorStop(1, color + "00");
                ctx.fillStyle = g;
                ctx.fill();
              }

              ctx.fillStyle = active ? color : dimColor;
              if (n.kind === "document") {
                // 문서 = 둥근 사각형
                const s2 = r * 1.6;
                ctx.beginPath();
                ctx.roundRect(n.x - s2 / 2, n.y - s2 / 2, s2, s2, 2.5);
                ctx.fill();
              } else if (n.kind === "note") {
                // 노트 = 마름모
                ctx.beginPath();
                ctx.moveTo(n.x, n.y - r * 1.25);
                ctx.lineTo(n.x + r * 1.25, n.y);
                ctx.lineTo(n.x, n.y + r * 1.25);
                ctx.lineTo(n.x - r * 1.25, n.y);
                ctx.closePath();
                ctx.fill();
              } else {
                ctx.beginPath();
                ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
                ctx.fill();
              }
              ctx.lineWidth = 1.1;
              ctx.strokeStyle = bg;
              ctx.stroke();

              if (scale < 0.7 && !active) return;
              if (!active && evidenceIds) return; // evidence 모드: dim 노드 라벨 생략

              const fontSize = Math.max(10, 12 / Math.max(scale, 0.85));
              ctx.font = `${hoveredNodeId === n.id ? 600 : 400} ${fontSize}px -apple-system, "Pretendard", "Apple SD Gothic Neo", sans-serif`;
              ctx.textAlign = "center";
              ctx.textBaseline = "top";
              ctx.fillStyle = active
                ? isDark
                  ? "#c8ccd2"
                  : "#3a3d44"
                : isDark
                  ? "#444851"
                  : "#c1c5cd";
              const label = n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label;
              ctx.fillText(label, n.x, n.y + r + 4);
            }}
            nodePointerAreaPaint={(node, color, ctx) => {
              const n = node as RFNode;
              if (n.x === undefined || n.y === undefined) return;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(n.x, n.y, radiusFor(n) + 4, 0, Math.PI * 2);
              ctx.fill();
            }}
          />
        )}

        {/* 엔티티 상세 미니 패널 */}
        {selected && (
          <div
            style={{
              position: "absolute",
              right: 10,
              top: 10,
              width: 220,
              background: isDark ? "rgba(22,24,28,0.92)" : "rgba(255,255,255,0.95)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              padding: "10px 12px",
              fontSize: "var(--fs-12)",
              backdropFilter: "blur(4px)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: nodeColor(selected),
                  flexShrink: 0,
                }}
              />
              <strong style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                {selected.label}
              </strong>
              <button
                onClick={() => setSelected(null)}
                style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--color-text-muted)" }}
              >
                <X size={12} />
              </button>
            </div>
            <div style={{ color: "var(--color-text-muted)" }}>타입: {selected.type || "기타"}</div>
            <div style={{ color: "var(--color-text-muted)" }}>
              연결: {selected.degree}건 — 연결된 문서·노트는 점선 엣지를 따라가세요
            </div>
          </div>
        )}

        {/* 좌하단 범례 */}
        <div
          style={{
            position: "absolute",
            left: 12,
            bottom: 12,
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            background: isDark ? "rgba(22,24,28,0.7)" : "rgba(255,255,255,0.85)",
            border: "1px solid var(--color-border)",
            borderRadius: 6,
            padding: "6px 8px",
            display: "flex",
            gap: 10,
            alignItems: "center",
            backdropFilter: "blur(4px)",
            pointerEvents: "none",
          }}
        >
          <span>● 엔티티</span>
          <span>■ 문서</span>
          <span>◆ 노트</span>
          <span>— 관계</span>
          <span>┄ 언급</span>
        </div>
      </div>
    </div>
  );
}

function CenterHint({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        color: "var(--color-text-muted)",
        fontSize: "var(--fs-13)",
        textAlign: "center",
        lineHeight: 1.7,
        padding: 20,
      }}
    >
      <div>{children}</div>
    </div>
  );
}

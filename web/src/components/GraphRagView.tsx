// M_19 GraphRAG 지식그래프 탭 (CR-18) + CR-21 실용화 개편.
// 문서·엔티티·노트 통합 그래프 + 채팅 답변 근거(evidence) 하이라이트.
// CR-21: 클릭=핀 고정/해제(게시판 핀 UX), 핀 포커스(연계 문서 위주 보기),
//        문서 상세/다운로드 패널, 시뮬레이션 안정화.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import { Download, ExternalLink, Network, Pin, PinOff, RefreshCw, X } from "lucide-react";
import type { GraphRagData, GraphRagEvidence, GraphRagStatus, GraphRagNode } from "../types";
import {
  fetchGraphEvidence,
  fetchGraphRag,
  fetchGraphRagStatus,
  openDocument,
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
  fx?: number;
  fy?: number;
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
  const fgRef = useRef<ForceGraphMethods<RFNode, RFLink> | undefined>(undefined);
  const [size, setSize] = useState({ w: 400, h: 300 });
  const [data, setData] = useState<GraphRagData | null>(null);
  const [status, setStatus] = useState<GraphRagStatus | null>(null);
  const [error, setError] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<GraphRagEvidence | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selected, setSelected] = useState<RFNode | null>(null);
  const [reindexing, setReindexing] = useState(false);

  // CR-21: 핀 고정 — id → 고정 좌표. 데이터 리로드 후에도 좌표 복원.
  const pinnedRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const [pinnedVersion, setPinnedVersion] = useState(0);
  const didFitRef = useRef(false);

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
  const { graphData, neighbors, byId } = useMemo(() => {
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
    // 핀 좌표 복원 — 리로드로 노드 객체가 재생성돼도 고정 유지
    for (const n of nodes) {
      const pin = pinnedRef.current.get(n.id);
      if (pin) {
        n.fx = pin.x;
        n.fy = pin.y;
        n.x = pin.x;
        n.y = pin.y;
      }
    }
    return { graphData: { nodes, links }, neighbors, byId };
  }, [data]);

  const evidenceIds = useMemo(() => {
    if (!evidence) return null;
    return new Set(evidence.nodes.map((n) => n.id));
  }, [evidence]);

  // CR-21: 핀 포커스 집합 — 핀 노드 + 직접 이웃 + 엔티티 경유 2-hop 문서·노트
  const focusSet = useMemo(() => {
    void pinnedVersion; // 핀 변경 시 재계산
    const pinnedAlive = [...pinnedRef.current.keys()].filter((id) => byId.has(id));
    if (pinnedAlive.length === 0) return null;
    const set = new Set<string>(pinnedAlive);
    for (const id of pinnedAlive) {
      for (const nb of neighbors.get(id) ?? []) {
        set.add(nb);
        const nbNode = byId.get(nb);
        if (nbNode?.kind === "entity") {
          for (const nb2 of neighbors.get(nb) ?? []) {
            const n2 = byId.get(nb2);
            if (n2 && n2.kind !== "entity") set.add(nb2); // 연계 문서·노트
          }
        }
      }
    }
    return set;
  }, [pinnedVersion, neighbors, byId]);

  // ── 크기 추적 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // ── 핀 조작 ────────────────────────────────────────────────────────────────
  const isPinned = useCallback(
    (id: string): boolean => pinnedRef.current.has(id),
    // pinnedVersion으로 참조 무효화 (Map은 mutate되므로)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pinnedVersion]
  );

  const pinNode = useCallback((n: RFNode) => {
    if (n.x === undefined || n.y === undefined) return;
    n.fx = n.x;
    n.fy = n.y;
    pinnedRef.current.set(n.id, { x: n.x, y: n.y });
    setPinnedVersion((v) => v + 1);
  }, []);

  const unpinNode = useCallback((n: RFNode) => {
    n.fx = undefined;
    n.fy = undefined;
    pinnedRef.current.delete(n.id);
    setPinnedVersion((v) => v + 1);
  }, []);

  const unpinAll = useCallback(() => {
    pinnedRef.current.clear();
    for (const n of graphData.nodes) {
      n.fx = undefined;
      n.fy = undefined;
    }
    setPinnedVersion((v) => v + 1);
    fgRef.current?.d3ReheatSimulation();
  }, [graphData.nodes]);

  // ── 노드 활성 판정: evidence > 핀 포커스 > 호버 ───────────────────────────
  const isActive = useCallback(
    (id: string): boolean => {
      if (evidenceIds) return evidenceIds.has(id);
      if (focusSet) return focusSet.has(id);
      if (!hoveredNodeId) return true;
      if (id === hoveredNodeId) return true;
      return neighbors.get(hoveredNodeId)?.has(id) ?? false;
    },
    [evidenceIds, focusSet, hoveredNodeId, neighbors]
  );

  const nodeColor = useCallback(
    (n: RFNode): string => {
      if (n.kind === "document") return isDark ? "#9fb3d1" : "#5b7396";
      if (n.kind === "note") return accent;
      return TYPE_COLORS[n.type] ?? TYPE_COLORS["기타"];
    },
    [accent, isDark]
  );

  // 문서·노트는 탐색의 주 대상 — 엔티티보다 크게
  const radiusFor = useCallback((n: RFNode): number => {
    const base = n.kind === "entity" ? 4 : 6;
    return base + Math.min(8, Math.sqrt(n.degree) * 1.7);
  }, []);

  // 클릭 = 핀 토글 + 상세 패널 (CR-21)
  const handleNodeClick = useCallback(
    (raw: unknown) => {
      const n = raw as RFNode;
      if (isPinned(n.id)) {
        unpinNode(n);
        setSelected((prev) => (prev?.id === n.id ? null : prev));
      } else {
        pinNode(n);
        setSelected(n);
      }
    },
    [isPinned, pinNode, unpinNode]
  );

  // 상세 패널의 연결 칩 클릭 — 해당 노드로 카메라 이동 + 핀 + 선택
  const focusNodeById = useCallback(
    (id: string) => {
      const n = byId.get(id);
      if (!n || n.x === undefined || n.y === undefined) return;
      pinNode(n);
      setSelected(n);
      fgRef.current?.centerAt(n.x, n.y, 500);
    },
    [byId, pinNode]
  );

  // 선택 노드의 연결 목록 (패널용) — degree 높은 순, 문서·노트 우선
  const selectedConnections = useMemo(() => {
    if (!selected) return [];
    const ids = [...(neighbors.get(selected.id) ?? [])];
    const nodes = ids
      .map((id) => byId.get(id))
      .filter((n): n is RFNode => n !== undefined);
    nodes.sort((a, b) => {
      const ka = a.kind === "entity" ? 1 : 0;
      const kb = b.kind === "entity" ? 1 : 0;
      if (ka !== kb) return ka - kb;
      return b.degree - a.degree;
    });
    return nodes.slice(0, 10);
  }, [selected, neighbors, byId]);

  // ── 렌더 ──────────────────────────────────────────────────────────────────
  const notConnected = status !== null && (!status.enabled || !status.connected);
  const stats = data?.stats ?? status?.stats ?? {};
  const pinnedCount = pinnedRef.current.size;

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
          {pinnedCount > 0 && (
            <button
              onClick={unpinAll}
              title="모든 핀을 뽑고 전체 보기로 복귀"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: "var(--fs-11)",
                padding: "3px 8px",
                borderRadius: 6,
                border: `1px solid ${accent}`,
                background: accent + (isDark ? "26" : "1a"),
                color: "var(--color-text)",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              <PinOff size={11} />핀 {pinnedCount}개 모두 해제
            </button>
          )}
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
            ref={fgRef}
            width={size.w}
            height={size.h}
            graphData={graphData}
            backgroundColor={bg}
            // CR-21: 빠른 정착 — 흐물거림 최소화
            d3AlphaDecay={0.05}
            d3VelocityDecay={0.5}
            cooldownTime={2500}
            onEngineStop={() => {
              if (!didFitRef.current) {
                didFitRef.current = true;
                fgRef.current?.zoomToFit(400, 60);
                // 노드가 적을 때 zoomToFit이 과도하게 확대하는 것 방지
                setTimeout(() => {
                  const fg = fgRef.current;
                  if (fg && fg.zoom() > 2.2) fg.zoom(2.2, 300);
                }, 500);
              }
            }}
            nodeRelSize={1}
            nodeLabel={() => ""}
            onNodeHover={(node) => {
              const id = node ? (node as RFNode).id : null;
              setHoveredNodeId(id);
              if (wrapRef.current) wrapRef.current.style.cursor = id ? "pointer" : "default";
            }}
            onNodeClick={handleNodeClick}
            // 드래그로 옮겨 놓으면 그 자리에 핀 고정 (게시판 핀 UX)
            onNodeDragEnd={(raw) => {
              const n = raw as RFNode;
              pinNode(n);
            }}
            linkColor={(l) => {
              const link = l as RFLink;
              const s = typeof link.source === "string" ? link.source : link.source.id;
              const t = typeof link.target === "string" ? link.target : link.target.id;
              const active = isActive(s) && isActive(t);
              if (!active) return isDark ? "rgba(120,125,140,0.05)" : "rgba(180,185,195,0.10)";
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
              const pinned = isPinned(n.id);
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

              // CR-21: 핀 표시 — 액센트 링 + 우상단 핀헤드
              if (pinned) {
                ctx.beginPath();
                ctx.arc(n.x, n.y, r + 3, 0, Math.PI * 2);
                ctx.lineWidth = 1.8;
                ctx.strokeStyle = accent;
                ctx.stroke();
                const px = n.x + r * 0.95;
                const py = n.y - r * 0.95;
                ctx.beginPath();
                ctx.arc(px, py, 3, 0, Math.PI * 2);
                ctx.fillStyle = accent;
                ctx.fill();
                ctx.lineWidth = 1;
                ctx.strokeStyle = bg;
                ctx.stroke();
              }

              // 라벨 — 문서·노트는 상시, 엔티티는 확대/활성 시. 딤 노드는 생략.
              if (!active) return;
              if (n.kind === "entity" && scale < 0.9 && hoveredNodeId !== n.id && !pinned) return;

              const isDocLike = n.kind !== "entity";
              // 화면 픽셀 기준 고정 크기 — 줌 수준과 무관하게 항상 같은 크기로 보인다
              const screenPx = isDocLike ? 12.5 : 11;
              const fontSize = screenPx / scale;
              const weight = pinned || hoveredNodeId === n.id || isDocLike ? 600 : 400;
              ctx.font = `${weight} ${fontSize}px -apple-system, "Pretendard", "Apple SD Gothic Neo", sans-serif`;
              ctx.textAlign = "center";
              ctx.textBaseline = "top";
              const label = n.label.length > 18 ? n.label.slice(0, 17) + "…" : n.label;
              // 헤일로(배경색 테두리)로 겹침 가독성 확보
              ctx.lineWidth = 3 / scale;
              ctx.strokeStyle = bg;
              ctx.strokeText(label, n.x, n.y + r + 4);
              ctx.fillStyle = isDark ? "#d5d9df" : "#2e3138";
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

        {/* CR-21: 상세 패널 (문서=다운로드, 노트=열기, 공통=연결 칩) */}
        {selected && (
          <div
            style={{
              position: "absolute",
              right: 10,
              top: 10,
              width: 250,
              maxHeight: "calc(100% - 60px)",
              overflowY: "auto",
              background: isDark ? "rgba(22,24,28,0.94)" : "rgba(255,255,255,0.96)",
              border: "1px solid var(--color-border)",
              borderRadius: 10,
              padding: "12px 14px",
              fontSize: "var(--fs-12)",
              backdropFilter: "blur(4px)",
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start", gap: 6, marginBottom: 6 }}>
              <span
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: selected.kind === "document" ? 2 : "50%",
                  background: nodeColor(selected),
                  flexShrink: 0,
                  marginTop: 3,
                }}
              />
              <strong style={{ flex: 1, lineHeight: 1.4, wordBreak: "break-all" }}>
                {selected.label}
              </strong>
              <button
                onClick={() => setSelected(null)}
                title="패널 닫기"
                style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--color-text-muted)", flexShrink: 0 }}
              >
                <X size={12} />
              </button>
            </div>

            <div style={{ color: "var(--color-text-muted)", marginBottom: 8 }}>
              {selected.kind === "document"
                ? "문서"
                : selected.kind === "note"
                  ? "업무 노트"
                  : `엔티티 · ${selected.type || "기타"}`}
              {" · 연결 "}
              {selected.degree}건
              {isPinned(selected.id) && (
                <span style={{ color: accent }}> · 📌 고정됨</span>
              )}
            </div>

            {/* 액션 버튼 */}
            <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
              {selected.kind === "document" && (
                <>
                  <PanelBtn
                    onClick={() => openDocument(selected.id, selected.label)}
                    accent={accent}
                    primary
                  >
                    <Download size={11} /> 다운로드
                  </PanelBtn>
                  <PanelBtn onClick={() => setChatTab("documents")} accent={accent}>
                    <ExternalLink size={11} /> 문서 탭
                  </PanelBtn>
                </>
              )}
              {selected.kind === "note" && (
                <PanelBtn
                  onClick={() => {
                    setSelectedNoteSlug(selected.id);
                    setChatTab("notes");
                  }}
                  accent={accent}
                  primary
                >
                  <ExternalLink size={11} /> 노트 열기
                </PanelBtn>
              )}
              <PanelBtn
                onClick={() =>
                  isPinned(selected.id) ? unpinNode(selected) : pinNode(selected)
                }
                accent={accent}
              >
                {isPinned(selected.id) ? (
                  <>
                    <PinOff size={11} /> 핀 뽑기
                  </>
                ) : (
                  <>
                    <Pin size={11} /> 핀 꽂기
                  </>
                )}
              </PanelBtn>
            </div>

            {/* 연결 항목 — 클릭 시 해당 노드로 이동 + 핀 */}
            {selectedConnections.length > 0 && (
              <>
                <div style={{ fontWeight: 600, marginBottom: 5, fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
                  연결된 항목 (클릭 = 이동·핀)
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {selectedConnections.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => focusNodeById(c.id)}
                      title={c.label}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        border: "1px solid var(--color-border)",
                        background: "transparent",
                        borderRadius: 6,
                        padding: "4px 8px",
                        cursor: "pointer",
                        color: "var(--color-text)",
                        fontSize: "var(--fs-11)",
                        fontFamily: "inherit",
                        textAlign: "left",
                      }}
                    >
                      <span
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: c.kind === "document" ? 2 : "50%",
                          background: nodeColor(c),
                          flexShrink: 0,
                        }}
                      />
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {c.label}
                      </span>
                      {isPinned(c.id) && <Pin size={9} style={{ color: accent, flexShrink: 0 }} />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* 좌하단 범례 + 조작 힌트 */}
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
            flexDirection: "column",
            gap: 3,
            backdropFilter: "blur(4px)",
            pointerEvents: "none",
          }}
        >
          <div style={{ display: "flex", gap: 10 }}>
            <span>● 엔티티</span>
            <span>■ 문서</span>
            <span>◆ 노트</span>
            <span>— 관계</span>
            <span>┄ 언급</span>
          </div>
          <div style={{ opacity: 0.8 }}>
            클릭 = 핀 고정·해제 · 드래그 = 원하는 위치에 고정 · 핀 있으면 연계 항목만 강조
          </div>
        </div>
      </div>
    </div>
  );
}

function PanelBtn({
  onClick,
  accent,
  primary = false,
  children,
}: {
  onClick: () => void;
  accent: string;
  primary?: boolean;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: "var(--fs-11)",
        padding: "4px 9px",
        borderRadius: 6,
        border: `1px solid ${primary ? accent : "var(--color-border)"}`,
        background: primary ? accent : "transparent",
        color: primary ? "#fff" : "var(--color-text)",
        cursor: "pointer",
        fontFamily: "inherit",
        fontWeight: primary ? 600 : 400,
      }}
    >
      {children}
    </button>
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

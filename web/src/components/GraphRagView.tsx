// M_19 GraphRAG 지식그래프 탭 (CR-18) + CR-21 실용화 개편.
// 문서·엔티티·노트 통합 그래프 + 채팅 답변 근거(evidence) 하이라이트.
// CR-21: 클릭=핀 고정/해제(게시판 핀 UX), 핀 포커스(연계 문서 위주 보기),
//        문서 상세/다운로드 패널, 시뮬레이션 안정화.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import {
  Download,
  ExternalLink,
  Network,
  Pin,
  PinOff,
  RefreshCw,
  Search,
  Telescope,
  X,
} from "lucide-react";
import type { GraphRagData, GraphRagEvidence, GraphRagStatus, GraphRagNode } from "../types";
import {
  cancelGraphIndexing,
  clearGraph,
  fetchGraphEvidence,
  fetchGraphRag,
  fetchGraphRagStatus,
  openDocument,
  requestGraphNormalize,
  requestGraphReindex,
  searchGraphDocs,
  type GraphDocMatch,
} from "../services/api";
import { useStore } from "../store";

// CR-30: 키워드 역할별 팔레트 (다크/라이트 공용)
const TYPE_COLORS: Record<string, string> = {
  research_target: "#56b380", // 연구대상
  technology: "#5f8fe0", // 기술
  problem: "#e07a5f", // 문제
  outcome: "#d8a44f", // 산출물
};

const ROLE_LABELS: Record<string, string> = {
  research_target: "연구대상",
  technology: "기술",
  problem: "문제",
  outcome: "산출물",
};

const ENTITY_TYPES = ["research_target", "technology", "problem", "outcome"];
const FALLBACK_COLOR = "#9aa0ab";

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

// hex(#rrggbb) 색을 흰색과 amt(0~1)만큼 혼합 — 노드 그라디언트용
function lighten(hex: string, amt: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const v = parseInt(m[1], 16);
  const r = Math.round(((v >> 16) & 255) + (255 - ((v >> 16) & 255)) * amt);
  const g = Math.round(((v >> 8) & 255) + (255 - ((v >> 8) & 255)) * amt);
  const b = Math.round((v & 255) + (255 - (v & 255)) * amt);
  return `rgb(${r},${g},${b})`;
}

// hex 색을 검정과 amt만큼 혼합
function darken(hex: string, amt: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const v = parseInt(m[1], 16);
  const r = Math.round(((v >> 16) & 255) * (1 - amt));
  const g = Math.round(((v >> 8) & 255) * (1 - amt));
  const b = Math.round((v & 255) * (1 - amt));
  return `rgb(${r},${g},${b})`;
}

export default function GraphRagView(): React.ReactElement {
  const theme = useStore((s) => s.theme);
  const chatTab = useStore((s) => s.chatTab);
  const evidenceReq = useStore((s) => s.graphEvidenceReq);
  const setChatTab = useStore((s) => s.setChatTab);
  const setSelectedNoteSlug = useStore((s) => s.setSelectedNoteSlug);
  const graphPinDocs = useStore((s) => s.graphPinDocs);
  const clearGraphPinDocs = useStore((s) => s.clearGraphPinDocs);
  const setResearchScope = useStore((s) => s.setResearchScope);

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
  const [search, setSearch] = useState("");
  const [docMatches, setDocMatches] = useState<GraphDocMatch[]>([]);
  const [normalizing, setNormalizing] = useState(false);
  const [normResult, setNormResult] = useState<string>("");
  // CR-26: 초기화 확인 모달
  const [clearModalOpen, setClearModalOpen] = useState(false);
  const [clearConfirmText, setClearConfirmText] = useState("");
  const [clearing, setClearing] = useState(false);
  const CLEAR_PHRASE = "그래프를 초기화 합니다";

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
        fetchGraphRag(2000, typeFilter), // CR-32: 전체 문서 로드 (검색·핀 대상이 항상 그래프에 존재)
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
        if (nbNode?.kind === "entity" || nbNode?.kind === "keyword") {
          for (const nb2 of neighbors.get(nb) ?? []) {
            const n2 = byId.get(nb2);
            if (n2 && n2.kind !== "entity" && n2.kind !== "keyword") set.add(nb2); // 연계 문서·노트
          }
        }
      }
    }
    return set;
  }, [pinnedVersion, neighbors, byId]);

  // CR-32/33: 검색 중이면 매칭 "문서만" 선명 (키워드는 배경 유지 —
  // 연관 키워드·문서는 핀으로 고정했을 때만 드러난다).
  const searchSet = useMemo(() => {
    if (search.trim().length < 2 || docMatches.length === 0) return null;
    const set = new Set<string>();
    for (const d of docMatches) {
      if (byId.has(d.doc_id)) set.add(d.doc_id);
    }
    return set.size > 0 ? set : null;
  }, [search, docMatches, byId]);

  // CR-32: 표시 대상 집합 — 우선순위 근거 > 핀 > 검색. 없으면 null(개요 모드).
  const activeSet = useMemo(
    () => evidenceIds ?? focusSet ?? searchSet,
    [evidenceIds, focusSet, searchSet]
  );

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

  // 핀 시 이웃을 방사형으로 정렬 — 문서·노트를 먼저(위쪽부터 시계방향) 배치
  const arrangeNeighbors = useCallback(
    (hub: RFNode) => {
      if (hub.x === undefined || hub.y === undefined) return;
      const nbs = [...(neighbors.get(hub.id) ?? [])]
        .map((id) => byId.get(id))
        .filter((m): m is RFNode => m !== undefined && !pinnedRef.current.has(m.id));
      if (nbs.length === 0) return;
      nbs.sort(
        (a, b) =>
          (a.kind === "entity" || a.kind === "keyword" ? 1 : 0) - (b.kind === "entity" || b.kind === "keyword" ? 1 : 0) ||
          b.degree - a.degree
      );
      const radius = 46 + Math.sqrt(nbs.length) * 14;
      nbs.forEach((m, i) => {
        const ang = (2 * Math.PI * i) / nbs.length - Math.PI / 2;
        m.x = hub.x! + radius * Math.cos(ang);
        m.y = hub.y! + radius * Math.sin(ang);
        (m as { vx?: number; vy?: number }).vx = 0;
        (m as { vx?: number; vy?: number }).vy = 0;
      });
    },
    [neighbors, byId]
  );

  const pinNode = useCallback(
    (n: RFNode) => {
      if (n.x === undefined || n.y === undefined) return;
      n.fx = n.x;
      n.fy = n.y;
      pinnedRef.current.set(n.id, { x: n.x, y: n.y });
      arrangeNeighbors(n);
      setPinnedVersion((v) => v + 1);
    },
    [arrangeNeighbors]
  );

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

  // ── 노드 활성 판정: 표시 집합(근거·핀·검색) 있으면 그 집합, 없으면 호버 ─────
  // CR-32: 개요 모드(집합 없음)에서는 호버한 노드+이웃만 활성 — 라벨 폭주 방지.
  const isActive = useCallback(
    (id: string): boolean => {
      if (activeSet) return activeSet.has(id);
      if (!hoveredNodeId) return false;
      if (id === hoveredNodeId) return true;
      return neighbors.get(hoveredNodeId)?.has(id) ?? false;
    },
    [activeSet, hoveredNodeId, neighbors]
  );

  // CR-32: 라벨을 그릴지 — 표시 집합 있거나 호버 중일 때만 (개요는 라벨 0).
  const labelsOn = activeSet !== null || hoveredNodeId !== null;

  // CR-32: 표시 집합(검색·핀)이 바뀌면 그 대상만 화면에 맞춘다.
  useEffect(() => {
    const set = searchSet ?? focusSet;
    if (!set || set.size === 0) return;
    const t = setTimeout(() => {
      fgRef.current?.zoomToFit(500, 90, (n) => set.has((n as RFNode).id));
    }, 260);
    return () => clearTimeout(t);
  }, [searchSet, focusSet]);

  const nodeColor = useCallback(
    (n: RFNode): string => {
      if (n.kind === "document") return isDark ? "#9fb3d1" : "#5b7396";
      if (n.kind === "note") return accent;
      return TYPE_COLORS[n.type] ?? FALLBACK_COLOR;
    },
    [accent, isDark]
  );

  // 문서·노트는 탐색의 주 대상 — 엔티티보다 크게
  const radiusFor = useCallback((n: RFNode): number => {
    const base = n.kind === "entity" || n.kind === "keyword" ? 4 : 6;
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
      // 카메라는 focusSet effect가 핀 대상에 맞춘다 (CR-32)
    },
    [byId, pinNode]
  );

  // CR-21: 딥 리서치 근거 → 그래프 핀 요청 소비 (좌표가 잡힐 때까지 재시도)
  useEffect(() => {
    if (!graphPinDocs || chatTab !== "graph") return;
    let tries = 0;
    const t = setInterval(() => {
      tries += 1;
      const nodes = graphPinDocs
        .map((id) => byId.get(id))
        .filter((n): n is RFNode => n !== undefined);
      const ready = nodes.filter((n) => n.x !== undefined);
      if ((nodes.length > 0 && ready.length === nodes.length) || tries > 20) {
        clearInterval(t);
        for (const n of ready) {
          if (!pinnedRef.current.has(n.id)) pinNode(n);
        }
        if (ready[0]) {
          setSelected(ready[0]);
          fgRef.current?.centerAt(ready[0].x!, ready[0].y!, 600);
        }
        clearGraphPinDocs();
      }
    }, 250);
    return () => clearInterval(t);
  }, [graphPinDocs, byId, chatTab, pinNode, clearGraphPinDocs]);

  // 핀 꽂힌 문서·노트 (범위 리서치 대상)
  const pinnedDocNodes = useMemo(() => {
    void pinnedVersion;
    return [...pinnedRef.current.keys()]
      .map((id) => byId.get(id))
      .filter((n): n is RFNode => n !== undefined && n.kind !== "entity" && n.kind !== "keyword");
  }, [pinnedVersion, byId]);

  // CR-31: 과제(문서) 검색 — 제목·키워드 신호로 문서만 찾는다.
  // 키워드 노드는 결과에 노출하지 않는다 ("디지털트윈"으로 검색하면 그 키워드를
  // 가진 과제 문서가 나온다 — 제목에 그 용어가 없어도).
  useEffect(() => {
    const q = search.trim();
    if (q.length < 2) {
      setDocMatches([]);
      return;
    }
    const t = setTimeout(() => {
      void searchGraphDocs(q, 12)
        .then(setDocMatches)
        .catch(() => setDocMatches([]));
    }, 300); // 디바운스
    return () => clearTimeout(t);
  }, [search]);

  // CR-22: 엔티티 정규화 실행
  const handleNormalize = useCallback(() => {
    if (!window.confirm("같은 대상의 표기 변형(정식명/약칭 등) 엔티티를 AI 판단으로 병합합니다.\n병합은 되돌릴 수 없습니다 (재인덱싱으로 재구축은 가능). 진행할까요?")) {
      return;
    }
    setNormalizing(true);
    setNormResult("");
    void requestGraphNormalize()
      .then((r) => {
        setNormResult(
          r.merged === 0
            ? "병합할 표기 변형이 없습니다."
            : `${r.groups.length}개 그룹, ${r.merged}개 병합: ${r.groups.map((g) => g.join("=")).join(", ")}`
        );
        return load();
      })
      .catch((e) => setNormResult(`정규화 실패: ${e instanceof Error ? e.message : String(e)}`))
      .finally(() => setNormalizing(false));
  }, [load]);

  // 선택 노드의 연결 목록 (패널용) — degree 높은 순, 문서·노트 우선
  const selectedConnections = useMemo(() => {
    if (!selected) return [];
    const ids = [...(neighbors.get(selected.id) ?? [])];
    const nodes = ids
      .map((id) => byId.get(id))
      .filter((n): n is RFNode => n !== undefined);
    nodes.sort((a, b) => {
      const ka = a.kind === "entity" || a.kind === "keyword" ? 1 : 0;
      const kb = b.kind === "entity" || b.kind === "keyword" ? 1 : 0;
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
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0, position: "relative" }}>
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
          과제 {stats.documents ?? 0} · 키워드 {stats.keywords ?? 0} · 노트 {stats.notes ?? 0}
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
              {ROLE_LABELS[t] ?? t}
            </button>
          );
        })}

        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {/* 노드 검색 — 선택 시 해당 노드로 이동+핀 */}
          <div style={{ position: "relative" }}>
            <Search
              size={12}
              style={{
                position: "absolute",
                left: 7,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--color-text-muted)",
                pointerEvents: "none",
              }}
            />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onClick={() => window.electronAPI?.restoreFocus()}
              onMouseDown={(e) => e.stopPropagation()}
              placeholder="노드 검색…"
              spellCheck={false}
              style={{
                width: 150,
                padding: "4px 8px 4px 24px",
                fontSize: "var(--fs-11)",
                borderRadius: 6,
                border: "1px solid var(--color-border)",
                background: "var(--color-bg)",
                color: "var(--color-text)",
                outline: "none",
                fontFamily: "inherit",
              }}
            />
            {search.trim().length >= 2 && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  right: 0,
                  width: 300,
                  zIndex: 20,
                  background: isDark ? "rgba(22,24,28,0.97)" : "rgba(255,255,255,0.98)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  padding: 4,
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
                  maxHeight: 360,
                  overflowY: "auto",
                }}
              >
                <div style={{ fontSize: "var(--fs-10)", fontWeight: 700, color: "var(--color-text-muted)", padding: "3px 7px 2px" }}>
                  과제 검색 (제목·키워드)
                </div>
                {docMatches.length === 0 && (
                  <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", padding: "6px 7px" }}>
                    일치하는 과제가 없습니다.
                  </div>
                )}
                {docMatches.map((d) => (
                  <button
                    key={d.doc_id}
                    onClick={() => {
                      // 로드된 그래프에 있으면 이동+핀, 없으면(스냅샷 상한) 원본 열기
                      if (byId.has(d.doc_id)) focusNodeById(d.doc_id);
                      else openDocument(d.doc_id, d.title);
                      setSearch("");
                    }}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 2,
                      border: "none",
                      background: "transparent",
                      borderRadius: 5,
                      padding: "5px 7px",
                      cursor: "pointer",
                      color: "var(--color-text)",
                      fontSize: "var(--fs-11)",
                      fontFamily: "inherit",
                      textAlign: "left",
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: nodeColor({ kind: "document", type: "" } as RFNode), flexShrink: 0 }} />
                      <span style={{ fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.title}
                      </span>
                      {d.title_match && (
                        <span style={{ fontSize: "var(--fs-10)", color: "var(--color-accent)", flexShrink: 0 }}>제목</span>
                      )}
                    </span>
                    {d.matched_keywords.length > 0 && (
                      <span style={{ color: "var(--color-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", width: "100%", paddingLeft: 14 }}>
                        키워드: {d.matched_keywords.join(", ")}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          {pinnedDocNodes.length > 0 && (
            <button
              onClick={() =>
                setResearchScope(
                  pinnedDocNodes.map((n) => ({
                    id: n.kind === "note" ? `__knowledge__:${n.id}` : n.id,
                    label: n.label,
                  }))
                )
              }
              title="핀 꽂은 문서·노트 범위로 딥 리서치 실행"
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
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              <Telescope size={11} />핀 문서로 리서치 ({pinnedDocNodes.length})
            </button>
          )}
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
            <>
              <span style={{ fontSize: "var(--fs-11)", color: "var(--color-accent)" }}>
                인덱싱 중…{" "}
                {status?.indexing
                  .filter((i) => i.state === "running")
                  .map((i) => `${i.done_chunks}/${i.total_chunks}`)
                  .join(" ")}
              </span>
              <button
                onClick={() => {
                  void cancelGraphIndexing()
                    .then((r) => {
                      setNormResult(`인덱싱 ${r.cancelled}건 중단됨`);
                      return load();
                    })
                    .catch(() => undefined);
                }}
                title="진행·대기 중인 그래프 인덱싱 모두 중단"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "var(--fs-11)",
                  padding: "3px 8px",
                  borderRadius: 6,
                  border: "1px solid #c0392b",
                  background: "rgba(192,57,43,0.1)",
                  color: "#c0392b",
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                <X size={11} />
                중단
              </button>
            </>
          )}
          <button
            onClick={handleNormalize}
            disabled={normalizing || notConnected}
            title="같은 대상의 표기 변형(정식명·약칭 등) 엔티티를 AI 판단으로 병합"
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
              cursor: notConnected || normalizing ? "not-allowed" : "pointer",
              fontFamily: "inherit",
              opacity: notConnected || normalizing ? 0.5 : 1,
            }}
          >
            <Network size={11} />
            {normalizing ? "정규화 중…" : "정규화"}
          </button>
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
          <button
            onClick={() => {
              setClearConfirmText("");
              setClearModalOpen(true);
            }}
            disabled={notConnected || clearing}
            title="그래프 전체 초기화 (확인 문구 입력 필요)"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--fs-11)",
              padding: "3px 8px",
              borderRadius: 6,
              border: "1px solid #c0392b",
              background: "transparent",
              color: "#c0392b",
              cursor: notConnected || clearing ? "not-allowed" : "pointer",
              fontFamily: "inherit",
              opacity: notConnected || clearing ? 0.5 : 1,
            }}
          >
            초기화
          </button>
        </div>
      </div>

      {/* CR-26: 그래프 초기화 확인 모달 — 정확한 문구 입력 필요 (GitHub 저장소 삭제 방식) */}
      {clearModalOpen && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 100,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          onMouseDown={() => setClearModalOpen(false)}
        >
          <div
            onMouseDown={(e) => e.stopPropagation()}
            style={{
              width: 380,
              background: "var(--color-panel, var(--color-bg))",
              border: "1px solid var(--color-border)",
              borderRadius: 12,
              padding: "18px 20px",
              boxShadow: "0 12px 40px rgba(0,0,0,0.4)",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: "var(--fs-15)", color: "#c0392b", marginBottom: 8 }}>
              그래프 전체 초기화
            </div>
            <p style={{ fontSize: "var(--fs-12)", color: "var(--color-text-muted)", lineHeight: 1.6, marginBottom: 10 }}>
              엔티티 {stats.entities ?? 0} · 관계 {stats.relations ?? 0} · 문서 {stats.documents ?? 0} ·
              노트 {stats.notes ?? 0}개 노드가 <strong>모두 삭제</strong>됩니다. 되돌릴 수 없으며,
              문서·노트 원본과 벡터 검색(임베딩)에는 영향이 없습니다. 재구축은 "재인덱싱"으로 가능합니다.
            </p>
            <p style={{ fontSize: "var(--fs-12)", marginBottom: 6 }}>
              계속하려면 <strong style={{ color: "#c0392b" }}>{CLEAR_PHRASE}</strong> 를 정확히 입력하세요:
            </p>
            <input
              value={clearConfirmText}
              onChange={(e) => setClearConfirmText(e.target.value)}
              onClick={() => window.electronAPI?.restoreFocus()}
              placeholder={CLEAR_PHRASE}
              autoFocus
              spellCheck={false}
              style={{
                width: "100%",
                boxSizing: "border-box",
                background: "var(--color-bg)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                color: "var(--color-text)",
                padding: "8px 10px",
                fontSize: "var(--fs-13)",
                outline: "none",
                marginBottom: 12,
              }}
            />
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                onClick={() => setClearModalOpen(false)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  color: "var(--color-text)",
                  cursor: "pointer",
                  padding: "7px 14px",
                  fontSize: "var(--fs-12)",
                  fontFamily: "inherit",
                }}
              >
                취소
              </button>
              <button
                disabled={clearConfirmText.trim() !== CLEAR_PHRASE || clearing}
                onClick={() => {
                  setClearing(true);
                  void clearGraph(clearConfirmText.trim())
                    .then((r) => {
                      setClearModalOpen(false);
                      setNormResult(
                        `그래프 초기화 완료 — 엔티티 ${r.before.entities ?? 0} · 관계 ${r.before.relations ?? 0} 삭제됨`
                      );
                      pinnedRef.current.clear();
                      setPinnedVersion((v) => v + 1);
                      setSelected(null);
                      return load();
                    })
                    .catch((e) => setNormResult(`초기화 실패: ${e instanceof Error ? e.message : String(e)}`))
                    .finally(() => setClearing(false));
                }}
                style={{
                  background: clearConfirmText.trim() === CLEAR_PHRASE ? "#c0392b" : "transparent",
                  border: "1px solid #c0392b",
                  borderRadius: 8,
                  color: clearConfirmText.trim() === CLEAR_PHRASE ? "#fff" : "#c0392b",
                  cursor: clearConfirmText.trim() === CLEAR_PHRASE && !clearing ? "pointer" : "not-allowed",
                  padding: "7px 14px",
                  fontSize: "var(--fs-12)",
                  fontWeight: 700,
                  fontFamily: "inherit",
                  opacity: clearing ? 0.6 : 1,
                }}
              >
                {clearing ? "초기화 중…" : "그래프 초기화"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 정규화 결과 배너 */}
      {normResult && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            background: "rgba(86,179,128,0.1)",
            borderBottom: "1px solid var(--color-border)",
            fontSize: "var(--fs-12)",
            flexShrink: 0,
          }}
        >
          <span style={{ color: "#3d9668", fontWeight: 600, flexShrink: 0 }}>정규화</span>
          <span style={{ color: "var(--color-text-muted)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {normResult}
          </span>
          <button
            onClick={() => setNormResult("")}
            style={{ border: "none", background: "transparent", color: "var(--color-text-muted)", cursor: "pointer", display: "flex" }}
          >
            <X size={12} />
          </button>
        </div>
      )}

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
                // CR-33: 노드가 너무 많아 과축소되면 하한(0.9)으로 — 배경 노드가
                // 보이는 크기 유지. 적을 때 과확대는 상한(2.2)으로.
                setTimeout(() => {
                  const fg = fgRef.current;
                  if (!fg) return;
                  const z = fg.zoom();
                  if (z > 2.2) fg.zoom(2.2, 300);
                  else if (z < 0.9) fg.zoom(0.9, 300);
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
              const color = nodeColor(n);
              const isDocLikeKind = n.kind === "document" || n.kind === "note";

              // CR-33: 배경 노드(비활성) = 원래 역할 색을 유지하되 반투명 + 축소.
              // 라벨은 없고, 문서는 배경에서도 페이지 형태로 문서임이 드러난다.
              if (!active) {
                const br = radiusFor(n) * (isDocLikeKind ? 0.8 : 0.5);
                const alpha = isDark ? "6e" : "8c"; // 톤업 (다크 0.43 / 라이트 0.55)
                ctx.fillStyle = color + alpha;
                if (n.kind === "document") {
                  const w = br * 1.7;
                  const h = br * 2.0;
                  const x0 = n.x - w / 2;
                  const y0 = n.y - h / 2;
                  const fold = w * 0.34;
                  ctx.beginPath();
                  ctx.moveTo(x0, y0);
                  ctx.lineTo(x0 + w - fold, y0);
                  ctx.lineTo(x0 + w, y0 + fold);
                  ctx.lineTo(x0 + w, y0 + h);
                  ctx.lineTo(x0, y0 + h);
                  ctx.closePath();
                  ctx.fill();
                } else if (n.kind === "note") {
                  const s2 = br * 1.8;
                  ctx.fillRect(n.x - s2 / 2, n.y - s2 / 2, s2, s2);
                } else {
                  ctx.beginPath();
                  ctx.arc(n.x, n.y, br, 0, Math.PI * 2);
                  ctx.fill();
                }
                return;
              }
              const dimColor = isDark ? "#2a2d33" : "#dde1e7";
              const r = radiusFor(n);

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

              // 호버 글로우 — 부드러운 강조
              const hovered = hoveredNodeId === n.id;
              if (active && (hovered || pinned)) {
                ctx.shadowColor = color + "aa";
                ctx.shadowBlur = 10;
              }

              if (n.kind === "document") {
                // 문서 = 접힌 귀가 있는 페이지
                const w = r * 1.75;
                const h = r * 2.1;
                const x0 = n.x - w / 2;
                const y0 = n.y - h / 2;
                const fold = w * 0.34;
                ctx.beginPath();
                ctx.moveTo(x0, y0);
                ctx.lineTo(x0 + w - fold, y0);
                ctx.lineTo(x0 + w, y0 + fold);
                ctx.lineTo(x0 + w, y0 + h);
                ctx.lineTo(x0, y0 + h);
                ctx.closePath();
                if (active) {
                  const g = ctx.createLinearGradient(x0, y0, x0, y0 + h);
                  g.addColorStop(0, lighten(color, 0.25));
                  g.addColorStop(1, color);
                  ctx.fillStyle = g;
                } else {
                  ctx.fillStyle = dimColor;
                }
                ctx.fill();
                ctx.shadowBlur = 0;
                ctx.lineWidth = 1;
                ctx.strokeStyle = bg;
                ctx.stroke();
                // 접힌 귀
                ctx.beginPath();
                ctx.moveTo(x0 + w - fold, y0);
                ctx.lineTo(x0 + w - fold, y0 + fold);
                ctx.lineTo(x0 + w, y0 + fold);
                ctx.closePath();
                ctx.fillStyle = active ? darken(color, 0.25) : darken("#888e99", 0.35);
                ctx.fill();
                // 본문 줄 암시
                if (active) {
                  ctx.strokeStyle = isDark ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.75)";
                  ctx.lineWidth = Math.max(0.7, h * 0.05);
                  for (const fy of [0.45, 0.6, 0.75]) {
                    ctx.beginPath();
                    ctx.moveTo(x0 + w * 0.18, y0 + h * fy);
                    ctx.lineTo(x0 + w * (fy === 0.75 ? 0.6 : 0.82), y0 + h * fy);
                    ctx.stroke();
                  }
                }
              } else if (n.kind === "note") {
                // 노트 = 스티키 노트 (모서리 접힘)
                const s2 = r * 1.9;
                const x0 = n.x - s2 / 2;
                const y0 = n.y - s2 / 2;
                const fold = s2 * 0.3;
                ctx.beginPath();
                ctx.moveTo(x0, y0);
                ctx.lineTo(x0 + s2, y0);
                ctx.lineTo(x0 + s2, y0 + s2 - fold);
                ctx.lineTo(x0 + s2 - fold, y0 + s2);
                ctx.lineTo(x0, y0 + s2);
                ctx.closePath();
                if (active) {
                  const g = ctx.createLinearGradient(x0, y0, x0 + s2, y0 + s2);
                  g.addColorStop(0, lighten(color, 0.3));
                  g.addColorStop(1, color);
                  ctx.fillStyle = g;
                } else {
                  ctx.fillStyle = dimColor;
                }
                ctx.fill();
                ctx.shadowBlur = 0;
                ctx.lineWidth = 1;
                ctx.strokeStyle = bg;
                ctx.stroke();
                // 접힌 모서리
                ctx.beginPath();
                ctx.moveTo(x0 + s2 - fold, y0 + s2);
                ctx.lineTo(x0 + s2 - fold, y0 + s2 - fold);
                ctx.lineTo(x0 + s2, y0 + s2 - fold);
                ctx.closePath();
                ctx.fillStyle = active ? darken(color, 0.3) : darken("#888e99", 0.35);
                ctx.fill();
              } else {
                // 엔티티 = 그라디언트 구슬
                ctx.beginPath();
                ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
                if (active) {
                  const g = ctx.createRadialGradient(
                    n.x - r * 0.35,
                    n.y - r * 0.35,
                    r * 0.15,
                    n.x,
                    n.y,
                    r
                  );
                  g.addColorStop(0, lighten(color, 0.45));
                  g.addColorStop(1, color);
                  ctx.fillStyle = g;
                } else {
                  ctx.fillStyle = dimColor;
                }
                ctx.fill();
                ctx.shadowBlur = 0;
                ctx.lineWidth = 1.1;
                ctx.strokeStyle = active ? darken(color, 0.2) : bg;
                ctx.stroke();
              }
              ctx.shadowBlur = 0;

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

              // CR-32: 라벨 — 표시 집합/호버로 활성인 노드만. 개요(집합 없음)에서
              // 호버 안 하면 라벨을 아예 안 그린다 (495개 라벨 폭주 방지).
              if (!active || !labelsOn) return;
              // 집합 지정 상태에서는 키워드도 항상 라벨 (검색·핀 대상은 다 읽혀야 함).
              // 개요 호버 상태에서만 축소 시 키워드 라벨 생략.
              if (
                !activeSet &&
                (n.kind === "entity" || n.kind === "keyword") &&
                scale < 0.9 &&
                hoveredNodeId !== n.id
              )
                return;

              const isDocLike = n.kind !== "entity" && n.kind !== "keyword";
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
                  : `키워드 · ${ROLE_LABELS[selected.type] ?? selected.type ?? ""}`}
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
            <span>● 키워드</span>
            <span>▤ 과제(문서)</span>
            <span>◪ 노트</span>
            <span>— 관계</span>
            <span>┄ 언급</span>
          </div>
          <div style={{ opacity: 0.8 }}>
            검색·핀 하면 그 과제와 키워드만 표시 · 클릭 = 핀 고정·해제 · 마우스 올리면 라벨
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

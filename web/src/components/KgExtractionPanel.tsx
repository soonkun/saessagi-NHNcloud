// M_23 지식그래프 추출 패널 (CR-60).
//
// 기존 그래프 탭에 재인덱싱·중단 버튼이 있는데 새 파이프라인만 CLI로 두면 사용자가
// 터미널을 열어야 한다(사용자 지적). 폴더를 고르고 시작·중단하고 진행을 보는 것까지
// 화면에서 끝나게 한다.
//
// 중단은 **강제 종료가 아니다.** 진행 중인 청크가 끝나면 멈추므로 이미 뽑은 후보가
// 보존되고, 다시 시작하면 끝난 문서는 건너뛴다.

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Play, RefreshCw, Square } from "lucide-react";
import {
  fetchKgBuildStatus,
  fetchKgFolders,
  fetchKgStatus,
  startKgBuild,
  startKgExtraction,
  stopKgBuild,
  stopKgExtraction,
  type KgBuildProgress,
  type KgFolder,
  type KgStatus,
} from "../services/api";

function fmtDuration(sec: number): string {
  if (!sec || sec < 1) return "-";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  if (m > 0) return `${m}분`;
  return `${Math.round(sec)}초`;
}

// CR-61: 구축 단계 이름 — 사용자에게 "지금 뭐 하는 중인지" 보이는 유일한 단서다.
const BUILD_STAGE_LABEL: Record<string, string> = {
  consolidate: "문서 단위 통합",
  normalize: "전역 정규화",
  "normalize:fuzzy": "전역 정규화 · 유사 병합",
  derive: "관계 유도",
  load: "그래프 적재",
  "load:documents": "그래프 적재 · 문서",
  "load:canonicals": "그래프 적재 · 엔티티",
  "load:chunks": "그래프 적재 · 청크",
  "load:mentions": "그래프 적재 · 근거",
};

// 드롭다운에서 "모든 폴더"를 나타내는 특수 값. 실제 folder_id와 겹치지 않는다.
const ALL_FOLDERS = "__all__";

const STATE_LABEL: Record<string, string> = {
  IDLE: "대기",
  RUNNING: "진행 중",
  STOPPING: "중단하는 중…",
  COMPLETED: "완료",
  CANCELLED: "중단됨",
  FAILED: "실패",
};

export function KgExtractionPanel({
  isDark,
  onSummaryChange,
}: {
  isDark: boolean;
  /** 접힌 관리 패널 헤더에 띄울 한 줄 요약을 부모로 올린다 (CR-61). */
  onSummaryChange?: (summary: string) => void;
}): React.ReactElement {
  // 1·2단계를 각각 접는다. 추출이 25시간짜리라 대개는 진행률 한 줄만 보면 된다.
  const [step1Open, setStep1Open] = useState(true);
  // 추출이 끝나면 구축까지 자동으로. 기본 켬 — 추출만 하고 두면 그래프에 반영이 안 되는데,
  // 10시간짜리 작업이라 끝나는 시점을 지켜보고 있을 수 없다.
  const [buildAfter, setBuildAfter] = useState(true);
  const [step2Open, setStep2Open] = useState(true);
  const [folders, setFolders] = useState<KgFolder[]>([]);
  const [folderId, setFolderId] = useState("");
  const [chunks, setChunks] = useState(8);
  const [status, setStatus] = useState<KgStatus | null>(null);
  const [build, setBuild] = useState<{ running: boolean; progress: KgBuildProgress } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const [s, b] = await Promise.all([fetchKgStatus(), fetchKgBuildStatus()]);
    if (s) setStatus(s);
    if (b) setBuild(b);
  }, []);

  useEffect(() => {
    void fetchKgFolders().then((f) => {
      setFolders(f);
      // 미추출이 여러 폴더에 걸쳐 있으면 "모든 폴더"를, 한 폴더뿐이면 그 폴더를 고른다.
      const pending = f.filter((x) => x.extracted < x.documents);
      if (pending.length > 1) setFolderId(ALL_FOLDERS);
      else if (pending.length === 1) setFolderId(pending[0].folder_id);
    });
    void refresh();
  }, [refresh]);

  // 진행 중일 때만 자주 갱신한다. 멈춰 있을 때 초당 폴링은 낭비다.
  useEffect(() => {
    const running =
      status?.running ||
      status?.progress.state === "STOPPING" ||
      build?.running ||
      build?.progress.state === "STOPPING";
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => void refresh(), running ? 3000 : 15000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [status?.running, status?.progress.state, build?.running, build?.progress.state, refresh]);

  const p = status?.progress;
  const running = Boolean(status?.running);
  const allSelected = folderId === ALL_FOLDERS;
  const selected = folders.find((f) => f.folder_id === folderId);
  const totalRemaining = folders.reduce((n, f) => n + (f.documents - f.extracted), 0);
  const remaining = allSelected ? totalRemaining : selected ? selected.documents - selected.extracted : 0;

  async function handleStart(): Promise<void> {
    setBusy(true);
    setMessage("");
    const r = await startKgExtraction({
      folder_id: allSelected ? "" : folderId,
      all_folders: allSelected,
      build_after: buildAfter,
      resume: true,
      chunks_per_document: chunks,
    });
    setBusy(false);
    setMessage(r.started ? "" : (r.reason ?? "시작하지 못했습니다."));
    await refresh();
  }

  async function handleStop(): Promise<void> {
    setBusy(true);
    const r = await stopKgExtraction();
    setBusy(false);
    setMessage(r.stopped ? "중단 요청됨 — 진행 중인 청크가 끝나면 멈춥니다." : (r.reason ?? ""));
    await refresh();
  }

  const bp = build?.progress;
  const building = Boolean(build?.running);
  const st = status?.stats;
  // 추출은 끝났는데 그래프로 안 올라간 후보가 있는지 — 구축 버튼을 눌러야 할 이유.
  const pendingBuild = Boolean(st && st.entity_candidates > 0 && st.canonical_entities === 0);

  async function handleBuild(): Promise<void> {
    setBusy(true);
    setMessage("");
    const r = await startKgBuild({ folder_id: "" });
    setBusy(false);
    setMessage(r.started ? "" : (r.reason ?? "구축을 시작하지 못했습니다."));
    await refresh();
  }

  async function handleBuildStop(): Promise<void> {
    setBusy(true);
    const r = await stopKgBuild();
    setBusy(false);
    setMessage(r.stopped ? "중단 요청됨 — 현재 단계가 끝나면 멈춥니다." : (r.reason ?? ""));
    await refresh();
  }

  const pct = p && p.total_documents > 0 ? (p.processed / p.total_documents) * 100 : 0;
  const bpct = bp && bp.total > 0 ? (bp.done / bp.total) * 100 : 0;

  // 접어둔 상태에서도 "돌아가고 있다"를 알 수 있어야 한다.
  const summary = running
    ? `1단계 ${p ? `${p.processed.toLocaleString()}/${p.total_documents.toLocaleString()}` : ""}` +
      (p && p.eta_sec > 0 ? ` · 남은 ${fmtDuration(p.eta_sec)}` : "")
    : building
      ? `2단계 ${bp?.stage ? (BUILD_STAGE_LABEL[bp.stage] ?? bp.stage) : "구축 중"}`
      : pendingBuild
        ? "구축 필요"
        : "";
  useEffect(() => {
    onSummaryChange?.(summary);
  }, [summary, onSummaryChange]);

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: 10,
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <button
        onClick={() => setStep1Open((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          textAlign: "left",
          color: "var(--color-text)",
          fontFamily: "inherit",
        }}
      >
        {step1Open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span style={{ fontWeight: 700, fontSize: "var(--fs-13)" }}>1단계 · 엔티티 추출</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
          문서에서 후보를 뽑습니다 (LLM 사용 · 오래 걸림) · 근거·계획/실적 구분 포함
        </span>
      </button>

      <div
        style={{
          display: step1Open ? "flex" : "none",
          flexDirection: "column",
          gap: 10,
        }}
      >
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select
          value={folderId}
          onChange={(e) => setFolderId(e.target.value)}
          disabled={running}
          style={{
            flex: "1 1 240px",
            minWidth: 200,
            padding: "6px 8px",
            borderRadius: 6,
            border: "1px solid var(--color-border)",
            background: "var(--color-bg)",
            color: "var(--color-text)",
            fontSize: "var(--fs-12)",
          }}
        >
          <option value="">폴더 선택</option>
          {/* 전체 추출 — 폴더를 하나씩 골라 여러 번 누르지 않아도 되게 한다 (CR-61). */}
          {totalRemaining > 0 && (
            <option value={ALL_FOLDERS}>
              모든 폴더 — 미추출 {totalRemaining.toLocaleString()}건
            </option>
          )}
          {folders.map((f) => (
            <option key={f.folder_id} value={f.folder_id}>
              {f.name} — {f.documents}건 (추출 완료 {f.extracted})
            </option>
          ))}
        </select>

        <label
          title={
            "문서 하나에서 LLM에게 읽힐 조각 수입니다.\n" +
            "문서는 임베딩 때 이미 조각으로 나뉘어 있고(완결보고서는 문서당 100조각 안팎), " +
            "그중 연구목표·내용·결과가 있을 법한 것만 골라 이 개수만큼 보냅니다.\n\n" +
            "실측 확보율(큰 완결보고서 기준)\n" +
            "  8개 → 엔티티의 22~28%\n" +
            " 12개 → 35~40%\n" +
            " 16개 → 42~50%\n" +
            " 32개 → 81~88%\n\n" +
            "많이 볼수록 정확하지만 그만큼 오래 걸립니다. " +
            "RFP는 문서당 조각이 3개 안팎이라 이 값과 무관하게 전량 처리됩니다."
          }
          style={{
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            whiteSpace: "nowrap",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            cursor: "help",
            borderBottom: "1px dotted var(--color-border)",
          }}
        >
          문서당 읽을 조각
          <input
            type="number"
            min={1}
            max={64}
            value={chunks}
            disabled={running}
            onChange={(e) => setChunks(Math.max(1, Number(e.target.value) || 8))}
            style={{
              width: 56,
              padding: "4px 6px",
              borderRadius: 6,
              border: "1px solid var(--color-border)",
              background: "var(--color-bg)",
              color: "var(--color-text)",
              fontSize: "var(--fs-12)",
            }}
          />
          <span style={{ color: "var(--color-text-muted)" }}>개</span>
        </label>

        {running ? (
          <button
            onClick={() => void handleStop()}
            disabled={busy || p?.state === "STOPPING"}
            title="진행 중인 청크가 끝나면 멈춥니다 (결과는 보존)"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid #c0392b",
              background: "rgba(192,57,43,0.1)",
              color: "#c0392b",
              fontSize: "var(--fs-12)",
              fontWeight: 600,
              cursor: busy ? "default" : "pointer",
            }}
          >
            <Square size={12} />
            {p?.state === "STOPPING" ? "멈추는 중…" : "중단"}
          </button>
        ) : (
          <button
            onClick={() => void handleStart()}
            disabled={busy || !folderId || remaining <= 0}
            title={remaining <= 0 ? "이 폴더는 이미 모두 추출되었습니다" : "선택한 폴더의 남은 문서를 추출합니다"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--color-accent)",
              background: !folderId || remaining <= 0 ? "transparent" : "var(--color-accent)",
              color: !folderId || remaining <= 0 ? "var(--color-text-muted)" : "#fff",
              fontSize: "var(--fs-12)",
              fontWeight: 600,
              cursor: !folderId || remaining <= 0 ? "default" : "pointer",
            }}
          >
            <Play size={12} />
            추출 시작
            {remaining > 0 && folderId ? ` (${remaining}건)` : ""}
          </button>
        )}

        <label
          title="추출이 정상 완료되면 2단계 그래프 구축을 자동으로 이어서 실행합니다 (약 8분, LLM 미사용)"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            cursor: running ? "default" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          <input
            type="checkbox"
            checked={buildAfter}
            disabled={running}
            onChange={(e) => setBuildAfter(e.target.checked)}
            style={{ cursor: running ? "default" : "pointer" }}
          />
          끝나면 구축까지 자동
        </label>

        <button
          onClick={() => void refresh()}
          title="상태 새로고침"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "6px 8px",
            borderRadius: 6,
            border: "1px solid var(--color-border)",
            background: "transparent",
            color: "var(--color-text-muted)",
            cursor: "pointer",
          }}
        >
          <RefreshCw size={12} />
        </button>
      </div>

      {!running && selected && remaining > 0 && (
        <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
          {/* 실측 17.4청크/분 기준 대략치. 정확한 값보다 "몇 시간짜리인지" 감이 중요하다. */}
          남은 {remaining}건 · 예상 약 {fmtDuration((remaining * chunks * 60) / 17.4)}
          {chunks <= 8
            ? " · 핵심(과제명·목표·대상)은 앞쪽 조각에 몰려 있어 8개로도 잡힙니다"
            : chunks >= 24
              ? " · 확보율은 높지만 그만큼 오래 걸립니다"
              : ""}
        </div>
      )}

      {p && p.state !== "IDLE" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: "var(--fs-11)",
              color: "var(--color-text-muted)",
              flexWrap: "wrap",
              gap: 6,
            }}
          >
            <span>
              <strong style={{ color: running ? "var(--color-accent)" : "var(--color-text)" }}>
                {STATE_LABEL[p.state] ?? p.state}
              </strong>
              {p.scope ? ` · ${p.scope}` : ""} · 문서 {p.processed}/{p.total_documents}
            </span>
            <span style={{ whiteSpace: "nowrap" }}>
              후보 {p.accepted.toLocaleString()}건
              {p.failed > 0 ? ` · 실패 ${p.failed}` : ""}
              {" · "}경과 {fmtDuration(p.elapsed_sec)}
              {p.eta_sec > 0 ? ` · 남은 시간 약 ${fmtDuration(p.eta_sec)}` : ""}
            </span>
          </div>

          <div
            style={{
              height: 6,
              borderRadius: 3,
              background: isDark ? "#2a2a2a" : "#eee",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.min(100, pct)}%`,
                height: "100%",
                background: "var(--color-accent)",
                transition: "width 0.4s",
              }}
            />
          </div>

          {/* 처리한 문서 목록은 보여주지 않는다 — 쌓일수록 패널이 하염없이 길어져
              정작 봐야 할 진행바가 밀린다(사용자 지적). 진행 상황은 위 한 줄로 충분하고,
              개별 문서 결과는 어차피 후보 저장소에 남는다. */}
          {p.error && (
            <div style={{ fontSize: "var(--fs-11)", color: "#e57373" }}>{p.error}</div>
          )}
        </div>
      )}

      </div>
      {/* ── 2단계: 그래프 구축 (CR-61) ─────────────────────────────────────
          추출만으로는 그래프가 생기지 않는다. 후보를 통합·정규화해서 Neo4j에 올리는
          단계가 따로 있는데, 그게 화면에 없어서 "기능이 분리된 것 같다"는 지적을 받았다.
          같은 패널에 두어 추출 → 구축 → 그래프가 한 흐름으로 보이게 한다. */}
      <div
        style={{
          borderTop: "1px solid var(--color-border)",
          paddingTop: 10,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <button
          onClick={() => setStep2Open((v) => !v)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            background: "transparent",
            border: "none",
            padding: 0,
            cursor: "pointer",
            textAlign: "left",
            color: "var(--color-text)",
            fontFamily: "inherit",
          }}
        >
          {step2Open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <span style={{ fontWeight: 700, fontSize: "var(--fs-13)" }}>2단계 · 그래프 구축</span>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
            뽑아 둔 후보를 정규 엔티티로 올립니다 · LLM을 쓰지 않아 GPU를 잡지 않습니다
          </span>
        </button>

        <div
          style={{ display: step2Open ? "flex" : "none", flexDirection: "column", gap: 8 }}
        >
        {st && (
          <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
            후보 {st.entity_candidates.toLocaleString()}건 → 정규 엔티티{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {st.canonical_entities.toLocaleString()}
            </strong>
            건 · 관계 {st.relation_candidates.toLocaleString()}건
            {pendingBuild && (
              <span style={{ color: "var(--color-accent)" }}>
                {" · 아직 그래프로 올라가지 않았습니다"}
              </span>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {building ? (
            <button
              onClick={() => void handleBuildStop()}
              disabled={busy || bp?.state === "STOPPING"}
              title="현재 단계가 끝나면 멈춥니다 (끝난 단계 결과는 보존)"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid #c0392b",
                background: "rgba(192,57,43,0.1)",
                color: "#c0392b",
                fontSize: "var(--fs-12)",
                fontWeight: 600,
                cursor: busy ? "default" : "pointer",
              }}
            >
              <Square size={12} />
              {bp?.state === "STOPPING" ? "멈추는 중…" : "구축 중단"}
            </button>
          ) : (
            <button
              onClick={() => void handleBuild()}
              disabled={busy || running || !st || st.entity_candidates === 0}
              title={
                running
                  ? "추출이 끝난 뒤에 구축하세요"
                  : "후보를 통합·정규화해 그래프에 올립니다 (전체 기준 약 8분)"
              }
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid var(--color-accent)",
                background: running || !st?.entity_candidates ? "transparent" : "var(--color-accent)",
                color: running || !st?.entity_candidates ? "var(--color-text-muted)" : "#fff",
                fontSize: "var(--fs-12)",
                fontWeight: 600,
                cursor: running || !st?.entity_candidates ? "default" : "pointer",
              }}
            >
              <Play size={12} />
              그래프 구축
            </button>
          )}
          <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
            다시 눌러도 후보는 그대로입니다 — 그래프만 새로 만듭니다
          </span>
        </div>

        {bp && bp.state !== "IDLE" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "var(--fs-11)",
                color: "var(--color-text-muted)",
                flexWrap: "wrap",
                gap: 6,
              }}
            >
              <span>
                <strong style={{ color: building ? "var(--color-accent)" : "var(--color-text)" }}>
                  {STATE_LABEL[bp.state] ?? bp.state}
                </strong>
                {bp.stage ? ` · ${BUILD_STAGE_LABEL[bp.stage] ?? bp.stage}` : ""}
                {bp.total > 0 ? ` · ${bp.done.toLocaleString()}/${bp.total.toLocaleString()}` : ""}
              </span>
              <span style={{ whiteSpace: "nowrap" }}>경과 {fmtDuration(bp.elapsed_sec)}</span>
            </div>
            <div
              style={{
                height: 6,
                borderRadius: 3,
                background: isDark ? "#2a2a2a" : "#eee",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${Math.min(100, bpct)}%`,
                  height: "100%",
                  background: "var(--color-accent)",
                  transition: "width 0.4s",
                }}
              />
            </div>
            {bp.error && (
              <div style={{ fontSize: "var(--fs-11)", color: "#e57373" }}>{bp.error}</div>
            )}
          </div>
        )}
        </div>
      </div>

      {message && (
        <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>{message}</div>
      )}
    </div>
  );
}

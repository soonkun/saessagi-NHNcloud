// M_23 지식그래프 추출 패널 (CR-60).
//
// 기존 그래프 탭에 재인덱싱·중단 버튼이 있는데 새 파이프라인만 CLI로 두면 사용자가
// 터미널을 열어야 한다(사용자 지적). 폴더를 고르고 시작·중단하고 진행을 보는 것까지
// 화면에서 끝나게 한다.
//
// 중단은 **강제 종료가 아니다.** 진행 중인 청크가 끝나면 멈추므로 이미 뽑은 후보가
// 보존되고, 다시 시작하면 끝난 문서는 건너뛴다.

import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import {
  fetchKgFolders,
  fetchKgStatus,
  startKgExtraction,
  stopKgExtraction,
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

const STATE_LABEL: Record<string, string> = {
  IDLE: "대기",
  RUNNING: "진행 중",
  STOPPING: "중단하는 중…",
  COMPLETED: "완료",
  CANCELLED: "중단됨",
  FAILED: "실패",
};

export function KgExtractionPanel({ isDark }: { isDark: boolean }): React.ReactElement {
  const [folders, setFolders] = useState<KgFolder[]>([]);
  const [folderId, setFolderId] = useState("");
  const [chunks, setChunks] = useState(8);
  const [status, setStatus] = useState<KgStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const s = await fetchKgStatus();
    if (s) setStatus(s);
  }, []);

  useEffect(() => {
    void fetchKgFolders().then((f) => {
      setFolders(f);
      // 아직 추출이 덜 된 폴더를 기본으로 고른다 — 대개 그걸 하려고 들어온다.
      const pending = f.find((x) => x.extracted < x.documents);
      if (pending) setFolderId(pending.folder_id);
    });
    void refresh();
  }, [refresh]);

  // 진행 중일 때만 자주 갱신한다. 멈춰 있을 때 초당 폴링은 낭비다.
  useEffect(() => {
    const running = status?.running || status?.progress.state === "STOPPING";
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => void refresh(), running ? 3000 : 15000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [status?.running, status?.progress.state, refresh]);

  const p = status?.progress;
  const running = Boolean(status?.running);
  const selected = folders.find((f) => f.folder_id === folderId);
  const remaining = selected ? selected.documents - selected.extracted : 0;

  async function handleStart(): Promise<void> {
    setBusy(true);
    setMessage("");
    const r = await startKgExtraction({
      folder_id: folderId,
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

  const pct = p && p.total_documents > 0 ? (p.processed / p.total_documents) * 100 : 0;

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
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: "var(--fs-13)" }}>엔티티 추출 (신규 파이프라인)</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
          근거·계획/실적 구분 포함 · 기존 그래프는 건드리지 않습니다
        </span>
      </div>

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

      {message && (
        <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>{message}</div>
      )}
    </div>
  );
}

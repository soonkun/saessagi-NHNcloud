// M_20 딥 리서치 (CR-20) — 사내 지식 기반(GraphRAG+벡터) 심층 검토·보고서 생성.
// 인터넷 검색 없음. 회의록과 동일한 SSE 진행률 패턴.
import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Copy,
  FileDown,
  FileSearch,
  Lightbulb,
  FileText,
  Network,
  Paperclip,
  Play,
  X,
} from "lucide-react";
import { API_BASE, createNote } from "../services/api";
import { readSseStream } from "../services/sse";
import { useStore } from "../store";

type ResearchMode = "duplication" | "discovery" | "proposal";

interface SseEvent {
  stage: string;
  message?: string;
  report?: string;
  sources?: { n: number; doc_id: string; doc_name: string; page: number | null; score: number }[];
  sub_queries?: string[];
}

const MODES: {
  id: ResearchMode;
  label: string;
  desc: string;
  Icon: React.ElementType;
  placeholder: string;
}[] = [
  {
    id: "duplication",
    label: "과제 중복성 검토",
    desc: "제출할 과제 내용과 유사한 기존 연구를 찾아 차별성을 냉정하게 판정",
    Icon: FileSearch,
    placeholder: "검토할 과제 내용을 입력하거나 계획서 파일을 첨부하세요.",
  },
  {
    id: "discovery",
    label: "신규과제 발굴",
    desc: "과거 연구·동향을 근거로 새로운 과제를 제안",
    Icon: Lightbulb,
    placeholder: "관심 분야나 방향을 입력하세요. (예: 가축 방역 분야에서 후속 과제 발굴)",
  },
  {
    id: "proposal",
    label: "과제 계획서 초안",
    desc: "RFP를 바탕으로 기존 연구를 참고한 계획서 초안 + 실험방법 제시",
    Icon: FileText,
    placeholder: "RFP 핵심 내용을 입력하거나 RFP 파일을 첨부하세요.",
  },
];

const ACCEPT = ".pdf,.docx,.pptx,.hwpx,.txt,.md";

function ResultBtn({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        background: "transparent",
        border: "1px solid var(--color-border)",
        borderRadius: 6,
        color: "var(--color-text-muted)",
        cursor: "pointer",
        padding: "4px 10px",
        fontSize: "var(--fs-11)",
        fontFamily: "inherit",
      }}
    >
      {children}
    </button>
  );
}

export function DeepResearchView({ desktop }: { desktop?: boolean }): React.ReactElement {
  const researchScope = useStore((s) => s.researchScope);
  const setResearchScope = useStore((s) => s.setResearchScope);
  const requestGraphPins = useStore((s) => s.requestGraphPins);
  const bumpNotesRevision = useStore((s) => s.bumpNotesRevision);

  const [mode, setMode] = useState<ResearchMode>("duplication");
  const [prompt, setPrompt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [report, setReport] = useState("");
  const [sources, setSources] = useState<NonNullable<SseEvent["sources"]>>([]);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [noteSaved, setNoteSaved] = useState(false);
  const [noteSaving, setNoteSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const activeMode = MODES.find((m) => m.id === mode) ?? MODES[0];
  const canRun = !running && (prompt.trim().length > 0 || file !== null);

  async function handleRun(): Promise<void> {
    if (!canRun) return;
    setRunning(true);
    setSteps([]);
    setReport("");
    setSources([]);
    setError("");
    try {
      const form = new FormData();
      form.append("mode", mode);
      form.append("prompt", prompt);
      if (researchScope && researchScope.length > 0) {
        form.append("scope_doc_ids", JSON.stringify(researchScope.map((s) => s.id)));
      }
      if (file) form.append("file", file);
      const res = await fetch(API_BASE + "/api/deep-research/run-stream", {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(`서버 오류 (${res.status})`);
      await readSseStream<SseEvent>(res, (evt) => {
        if (evt.stage === "error") {
          setError(evt.message ?? "알 수 없는 오류");
          return;
        }
        if (evt.stage === "done") {
          setReport(evt.report ?? "");
          setSources(evt.sources ?? []);
          setSteps((prev) => [...prev, "완료"]);
          return;
        }
        if (evt.message) setSteps((prev) => [...prev, evt.message as string]);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(report);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }

  function reportWithSources(): string {
    const src = sources.length
      ? "\n\n## 참고 자료\n" +
        sources.map((s) => `- [${s.n}] ${s.doc_name}${s.page ? ` p.${s.page}` : ""}`).join("\n")
      : "";
    return report + src;
  }

  // 근거 문서를 그래프 탭에 핀으로 표시 — 노트 doc_id(__knowledge__:slug)는 slug로 매핑
  function handlePinInGraph(): void {
    const ids = [...new Set(sources.map((s) => s.doc_id))].map((id) =>
      id.startsWith("__knowledge__:") ? id.slice("__knowledge__:".length) : id
    );
    if (ids.length > 0) requestGraphPins(ids);
  }

  async function handleSaveNote(): Promise<void> {
    if (noteSaving || !report) return;
    setNoteSaving(true);
    try {
      const title = `[딥리서치·${activeMode.label}] ${(prompt.trim() || "첨부 기반").slice(0, 40)}`;
      await createNote({
        title,
        content: reportWithSources(),
        tags: ["딥리서치"],
        related_docs: [...new Set(sources.map((s) => s.doc_id))],
      });
      bumpNotesRevision();
      setNoteSaved(true);
      setTimeout(() => setNoteSaved(false), 2500);
    } catch (e) {
      setError(`노트 저장 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setNoteSaving(false);
    }
  }

  function handleDownloadMd(): void {
    const blob = new Blob([reportWithSources()], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `딥리서치_${activeMode.label}_${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        padding: desktop ? "24px 32px" : 16,
        display: "flex",
        flexDirection: "column",
        gap: 14,
        maxWidth: desktop ? 1400 : undefined,
      }}
    >
      <div>
        <h2 style={{ fontWeight: 700, fontSize: "var(--fs-18)", margin: 0 }}>딥 리서치</h2>
        <p style={{ fontSize: "var(--fs-12)", color: "var(--color-text-muted)", margin: "4px 0 0", lineHeight: 1.5 }}>
          사내 지식 기반(문서·노트·지식그래프)을 충분히 검토한 뒤 보고서를 작성합니다.
          인터넷 검색은 하지 않습니다.
        </p>
      </div>

      {/* 모드 선택 카드 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: desktop ? "repeat(3, 1fr)" : "1fr",
          gap: 8,
        }}
      >
        {MODES.map(({ id, label, desc, Icon }) => (
          <button
            key={id}
            onMouseDown={(e) => {
              e.stopPropagation();
              setMode(id);
            }}
            disabled={running}
            style={{
              textAlign: "left",
              padding: "10px 12px",
              background: mode === id ? "rgba(100,140,220,0.15)" : "var(--color-panel)",
              border: `1px solid ${mode === id ? "var(--color-accent)" : "var(--color-border)"}`,
              borderRadius: 10,
              color: "var(--color-text)",
              cursor: running ? "not-allowed" : "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: mode === id ? 700 : 600, fontSize: "var(--fs-13)" }}>
              <Icon size={14} style={{ color: mode === id ? "var(--color-accent)" : "var(--color-text-muted)" }} />
              {label}
            </div>
            <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", marginTop: 4, lineHeight: 1.4 }}>
              {desc}
            </div>
          </button>
        ))}
      </div>

      {/* 그래프 핀 문서 범위 (CR-21 연동) */}
      {researchScope && researchScope.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            padding: "8px 12px",
            background: "rgba(100,140,220,0.08)",
            border: "1px solid rgba(100,140,220,0.3)",
            borderRadius: 10,
            fontSize: "var(--fs-12)",
          }}
        >
          <Network size={13} style={{ color: "var(--color-accent)", flexShrink: 0 }} />
          <span style={{ fontWeight: 600 }}>검색 범위: 그래프 핀 문서 {researchScope.length}건</span>
          <span
            style={{
              color: "var(--color-text-muted)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
              minWidth: 0,
            }}
          >
            {researchScope.map((s) => s.label).join(" · ")}
          </span>
          <button
            onClick={() => setResearchScope(null)}
            title="범위 해제 — 전체 지식 기반 검색"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 3,
              border: "none",
              background: "transparent",
              color: "var(--color-text-muted)",
              cursor: "pointer",
              fontSize: "var(--fs-11)",
              fontFamily: "inherit",
              flexShrink: 0,
            }}
          >
            <X size={12} /> 해제
          </button>
        </div>
      )}

      {/* 입력 */}
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => window.electronAPI?.restoreFocus()}
        rows={desktop ? 6 : 4}
        disabled={running}
        placeholder={activeMode.placeholder}
        style={{
          background: "var(--color-bg)",
          border: "1px solid var(--color-border)",
          borderRadius: 10,
          color: "var(--color-text)",
          padding: "10px 12px",
          fontSize: "var(--fs-13)",
          lineHeight: 1.6,
          outline: "none",
          resize: "vertical",
          fontFamily: "inherit",
        }}
      />

      {/* 첨부 + 실행 */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT}
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            e.target.value = "";
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={running}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "7px 12px",
            background: "transparent",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text-muted)",
            cursor: running ? "not-allowed" : "pointer",
            fontSize: "var(--fs-12)",
          }}
        >
          <Paperclip size={13} />
          {file ? "파일 변경" : "파일 첨부 (PDF·DOCX·HWPX 등)"}
        </button>
        {file && (
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: "var(--fs-12)",
              color: "var(--color-text)",
              background: "var(--color-panel)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              padding: "4px 8px",
            }}
          >
            {file.name}
            <button
              onClick={() => setFile(null)}
              disabled={running}
              title="첨부 제거"
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", display: "flex", padding: 0 }}
            >
              <X size={12} />
            </button>
          </span>
        )}
        <button
          onClick={() => void handleRun()}
          disabled={!canRun}
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 18px",
            background: canRun ? "var(--color-accent)" : "transparent",
            border: `1px solid ${canRun ? "var(--color-accent)" : "var(--color-border)"}`,
            borderRadius: 8,
            color: canRun ? "#fff" : "var(--color-text-muted)",
            cursor: canRun ? "pointer" : "not-allowed",
            fontSize: "var(--fs-13)",
            fontWeight: 600,
          }}
        >
          <Play size={13} />
          {running ? "리서치 진행 중..." : "딥 리서치 시작"}
        </button>
      </div>

      {/* 진행 로그 */}
      {steps.length > 0 && (
        <div
          style={{
            background: "rgba(100,140,220,0.06)",
            border: "1px solid rgba(100,140,220,0.2)",
            borderRadius: 8,
            padding: "8px 12px",
            display: "flex",
            flexDirection: "column",
            gap: 4,
            maxHeight: 180,
            overflowY: "auto",
          }}
        >
          {steps.map((s, i) => (
            <div key={i} style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", lineHeight: 1.4 }}>
              {i === steps.length - 1 && running ? "⏳ " : "✓ "}
              {s}
            </div>
          ))}
        </div>
      )}

      {error && (
        <div
          style={{
            background: "rgba(229,57,53,0.08)",
            border: "1px solid rgba(229,57,53,0.35)",
            borderRadius: 8,
            padding: "8px 12px",
            fontSize: "var(--fs-12)",
            color: "#e57373",
            lineHeight: 1.5,
          }}
        >
          {error}
        </div>
      )}

      {/* 결과 보고서 */}
      {report && (
        <div
          style={{
            background: "var(--color-panel)",
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            padding: desktop ? "18px 22px" : 14,
          }}
        >
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
            {sources.length > 0 && (
              <ResultBtn onClick={handlePinInGraph} title="근거 문서를 그래프 탭에 핀으로 표시">
                <Network size={11} /> 그래프에 핀
              </ResultBtn>
            )}
            <ResultBtn onClick={() => void handleSaveNote()} title="보고서를 업무 노트로 저장">
              <BookOpen size={11} />
              {noteSaving ? "저장 중..." : noteSaved ? "노트 저장됨 ✓" : "업무노트로 저장"}
            </ResultBtn>
            <ResultBtn onClick={handleDownloadMd} title="마크다운 파일로 다운로드">
              <FileDown size={11} /> MD 저장
            </ResultBtn>
            <ResultBtn onClick={() => void handleCopy()} title="보고서 본문 복사">
              <Copy size={11} />
              {copied ? "복사됨 ✓" : "복사"}
            </ResultBtn>
          </div>
          <div className="md-body" style={{ fontSize: "var(--fs-13)", lineHeight: 1.7 }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // 넓은 표는 가로 스크롤 컨테이너로 감싸 레이아웃을 깨지 않게 한다
                // eslint-disable-next-line @typescript-eslint/no-unused-vars
                table: ({ node, ...props }) => (
                  <div className="md-table-wrap">
                    <table {...props} />
                  </div>
                ),
              }}
            >
              {report}
            </ReactMarkdown>
          </div>

          {sources.length > 0 && (
            <div style={{ marginTop: 14, borderTop: "1px solid var(--color-border)", paddingTop: 10 }}>
              <div style={{ fontSize: "var(--fs-12)", fontWeight: 700, marginBottom: 6 }}>
                참고 자료 ({sources.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {sources.map((s) => (
                  <div key={s.n} style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
                    [{s.n}] {s.doc_name}
                    {s.page ? ` p.${s.page}` : ""} · 유사도 {s.score}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// M_20 딥 리서치 (CR-20) — 사내 지식 기반(GraphRAG+벡터) 심층 검토·보고서 생성.
// 인터넷 검색 없음. 회의록과 동일한 SSE 진행률 패턴.
import { useEffect, useRef, useState } from "react";
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
  Printer,
  X,
} from "lucide-react";
import { API_BASE, createNote } from "../services/api";
import { readSseStream } from "../services/sse";
import { useStore } from "../store";
import { ModelBadge } from "./ModelBadge";

type ResearchMode = "duplication" | "discovery" | "proposal";

/** 리서치가 끝나기 전에 스트림이 끊겼을 때 보여줄 문구 (CR-57). */
const DISCONNECTED_MSG =
  "리서치가 도중에 끊겼습니다 — 새싹이 서버가 멈춘 것 같아요. " +
  "잠시 후 새로고침하고 다시 시도해 주세요. 같은 증상이 반복되면 관리자에게 알려주세요.";

interface SseEvent {
  stage: string;
  message?: string;
  report?: string;
  sources?: { n: number; doc_id: string; doc_name: string; page: number | null; score: number }[];
  sub_queries?: string[];
  /** 보고서 생성 중 "살아 있음" 신호 (CR-57). */
  elapsed_seconds?: number;
}

/** 인쇄 문서에 넣을 텍스트 이스케이프 — 요청 문구에 <, & 가 있어도 문서가 깨지지 않게. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 인쇄 문서 스타일 (CR-58). 앱 CSS와 완전히 분리된 독립 문서라 여기에 다 적는다.
 * 화면 테마(어두운 배경 등)를 끌고 오면 종이에서 읽을 수 없으므로 흑백으로 고정한다.
 */
const PRINT_CSS = `
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  margin: 0;
  color: #000;
  background: #fff;
  font-family: "Noto Sans KR", "Malgun Gothic", "AppleGothic", sans-serif;
  font-size: 10.5pt;
  line-height: 1.65;
}
.doc-title { font-size: 16pt; margin: 0 0 2mm; }
.doc-meta {
  font-size: 9pt;
  color: #333;
  border-bottom: 1px solid #000;
  padding-bottom: 2mm;
  margin-bottom: 5mm;
}
h1, h2, h3, h4 { page-break-after: avoid; margin: 4mm 0 2mm; line-height: 1.35; }
h1 { font-size: 14pt; } h2 { font-size: 12.5pt; } h3 { font-size: 11pt; }
p, li { orphans: 2; widows: 2; }
ul, ol { padding-left: 6mm; margin: 2mm 0; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0; font-size: 9.5pt; }
th, td { border: 1px solid #666; padding: 1.5mm 2mm; vertical-align: top; word-break: break-word; }
th { background: #eee; font-weight: 700; }
tr { page-break-inside: avoid; }
/* 화면에서는 표를 가로 스크롤 상자에 담지만 종이에서는 잘리면 안 된다 */
.md-table-wrap { overflow: visible !important; }
code, pre { font-family: "D2Coding", Consolas, monospace; font-size: 9pt; }
pre { background: #f4f4f4; padding: 2mm; page-break-inside: avoid; white-space: pre-wrap; }
blockquote { margin: 2mm 0; padding-left: 3mm; border-left: 2px solid #999; color: #333; }
img { max-width: 100%; }
`;

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
  // 진행 중 "살아 있는지" 표시 (CR-57). 서버가 죽으면 스트림이 조용히 끊겨서, 예전에는
  // 화면이 진행 중인지 끝난 것인지 알 수 없는 상태로 멈췄다(사용자 지적).
  const [elapsed, setElapsed] = useState(0);
  const [stalled, setStalled] = useState(false);
  const lastEventRef = useRef(Date.now());
  const [noteSaved, setNoteSaved] = useState(false);
  const [noteSaving, setNoteSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 인쇄(PDF)용 원본 — 보고서 카드 전체를 복제해 독립 문서로 만든다 (CR-58).
  const printRef = useRef<HTMLDivElement | null>(null);

  const activeMode = MODES.find((m) => m.id === mode) ?? MODES[0];
  const canRun = !running && (prompt.trim().length > 0 || file !== null);

  // 아무 신호도 없이 오래 지나면 알린다. 서버가 죽어도 브라우저는 한동안 조용히
  // 기다리기만 해서, 사용자는 진행 중인지 멈춘 건지 구분할 수 없다 (CR-57).
  // 임계값은 계획 단계의 LLM 타임아웃(90초)보다 넉넉히 잡아 오탐을 피한다.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      setStalled(Date.now() - lastEventRef.current > 120_000);
    }, 5_000);
    return () => clearInterval(t);
  }, [running]);

  async function handleRun(): Promise<void> {
    if (!canRun) return;
    setRunning(true);
    setSteps([]);
    setReport("");
    setSources([]);
    setError("");
    setElapsed(0);
    setStalled(false);
    lastEventRef.current = Date.now();
    let finished = false;
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
        lastEventRef.current = Date.now();
        setStalled(false);
        if (evt.stage === "error") {
          finished = true;
          setError(evt.message ?? "알 수 없는 오류");
          return;
        }
        if (evt.stage === "done") {
          finished = true;
          setReport(evt.report ?? "");
          setSources(evt.sources ?? []);
          setSteps((prev) => [...prev, "완료"]);
          return;
        }
        // 보고서 생성 중 주기 신호 — 목록을 늘리지 않고 경과 시간만 갱신한다.
        if (evt.stage === "synthesis_tick") {
          setElapsed(evt.elapsed_seconds ?? 0);
          return;
        }
        if (evt.message) setSteps((prev) => [...prev, evt.message as string]);
      });
      // 스트림이 done/error 없이 끝났다 = 서버가 도중에 사라졌다는 뜻이다.
      // 예전에는 이 경우 아무 말 없이 멈춰서, 진행 중인지 끝난 건지 알 수 없었다.
      if (!finished) setError(DISCONNECTED_MSG);
    } catch (e) {
      // 브라우저가 주는 "network error" 같은 문구는 사용자에게 아무 의미가 없다.
      // 리서치가 끝나기 전에 끊긴 것이면 무슨 일이 났는지 사람 말로 알린다.
      const raw = e instanceof Error ? e.message : String(e);
      setError(finished ? raw : `${DISCONNECTED_MSG} (${raw})`);
    } finally {
      setRunning(false);
      setStalled(false);
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

  /**
   * PDF로 저장 (CR-58).
   *
   * 서버에서 PDF를 만들려면 렌더링 엔진과 한글 폰트를 새로 들여야 하는데, 이미 화면에
   * 잘 그려진 보고서가 있다. 브라우저 인쇄(→ "PDF로 저장")를 쓰면 표·제목·각주가
   * 보이는 그대로 나오고 의존성이 하나도 늘지 않는다.
   *
   * 다만 **현재 페이지를 그대로 인쇄하면 안 된다.** 앱은 화면을 꽉 채우는 고정 레이아웃
   * (`position: fixed` body, `overflow: hidden` 컨테이너)이라, 인쇄 CSS로 다른 요소를
   * 숨겨도 보고서가 그 컨테이너에 잘려 **빈 페이지가 나온다**(실제로 그랬다).
   * 그래서 숨은 iframe에 보고서만 담은 독립 문서를 만들어 그걸 인쇄한다.
   */
  function handlePrintPdf(): void {
    const src = printRef.current;
    if (!src) return;

    // 화면용 버튼 줄은 종이에 남길 이유가 없다 — 복제본에서 걷어낸다.
    const clone = src.cloneNode(true) as HTMLElement;
    clone.querySelectorAll(".dr-print-hide").forEach((el) => el.remove());

    const stamp = new Date().toISOString().slice(0, 10);
    // 인쇄 대화상자의 기본 파일 이름은 문서 제목에서 온다.
    const title = `딥리서치_${activeMode.label}_${stamp}`;
    const head = `${activeMode.label} · ${new Date().toLocaleString("ko-KR")}${
      prompt.trim() ? ` · 요청: ${escapeHtml(prompt.trim().slice(0, 80))}` : ""
    }`;

    const iframe = document.createElement("iframe");
    iframe.setAttribute("aria-hidden", "true");
    iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
    document.body.appendChild(iframe);

    const doc = iframe.contentDocument;
    if (!doc) {
      iframe.remove();
      return;
    }
    doc.open();
    doc.write(
      `<!doctype html><html lang="ko"><head><meta charset="utf-8">` +
        `<title>${escapeHtml(title)}</title><style>${PRINT_CSS}</style></head><body>` +
        `<h1 class="doc-title">딥 리서치 보고서</h1>` +
        `<div class="doc-meta">${head}</div>` +
        clone.innerHTML +
        `</body></html>`
    );
    doc.close();

    const done = (): void => {
      // 인쇄 대화상자가 닫힌 뒤에 지운다 — 먼저 지우면 인쇄가 취소된다.
      window.setTimeout(() => iframe.remove(), 500);
    };
    const win = iframe.contentWindow;
    if (!win) {
      iframe.remove();
      return;
    }
    win.addEventListener("afterprint", done);
    // 폰트·레이아웃이 자리를 잡은 뒤 띄운다.
    window.setTimeout(() => {
      win.focus();
      win.print();
    }, 120);
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
      // 세로 flex 컨테이너 안에서 자식은 기본적으로 줄어든다(flex-shrink:1). 보고서가
      // 길어지면 위쪽 입력창·진행 목록이 한 줄로 찌부러졌다(사용자 지적). CSS에서
      // 직계 자식의 축소를 막는다 — 스크롤은 이 컨테이너가 담당한다 (CR-58).
      className="dr-column"
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
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h2 style={{ fontWeight: 700, fontSize: "var(--fs-18)", margin: 0 }}>딥 리서치</h2>
          {/* 어떤 모델이 보고서를 쓰는지 제목 옆에 밝힌다 (CR-57) */}
          <ModelBadge modelKey="deep_research" />
        </div>
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

      {running && (elapsed > 0 || stalled) && (
        <div
          style={{
            background: stalled ? "rgba(255,167,38,0.10)" : "var(--color-panel)",
            border: `1px solid ${stalled ? "rgba(255,167,38,0.45)" : "var(--color-border)"}`,
            borderRadius: 8,
            padding: "8px 12px",
            fontSize: "var(--fs-12)",
            color: stalled ? "#ffb74d" : "var(--color-text-muted)",
            lineHeight: 1.5,
          }}
        >
          {stalled
            ? "2분 넘게 아무 소식이 없어요. 서버가 멈췄을 수 있습니다 — 잠시 더 기다려 보고, 계속 이 상태면 새로고침 후 다시 시도해 주세요."
            : `보고서 작성 중… ${elapsed}초 경과 (새싹이는 살아 있어요)`}
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
          ref={printRef}
          style={{
            background: "var(--color-panel)",
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            padding: desktop ? "18px 22px" : 14,
          }}
        >
          <div
            className="dr-print-hide"
            style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginBottom: 4, flexWrap: "wrap" }}
          >
            {sources.length > 0 && (
              <ResultBtn onClick={handlePinInGraph} title="근거 문서를 그래프 탭에 핀으로 표시">
                <Network size={11} /> 그래프에 핀
              </ResultBtn>
            )}
            <ResultBtn onClick={() => void handleSaveNote()} title="보고서를 업무 노트로 저장">
              <BookOpen size={11} />
              {noteSaving ? "저장 중..." : noteSaved ? "노트 저장됨 ✓" : "업무노트로 저장"}
            </ResultBtn>
            <ResultBtn
              onClick={handlePrintPdf}
              title="인쇄 창에서 '대상'을 'PDF로 저장'으로 고르면 PDF 파일이 됩니다"
            >
              <Printer size={11} /> PDF 저장
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

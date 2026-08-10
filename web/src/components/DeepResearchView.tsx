// M_20 딥 리서치 (CR-20 → CR-62) — 사내 지식 기반(GraphRAG+벡터) 심층 검토·보고서 생성.
// 인터넷 검색 없음.
//
// CR-62: 모드 3개 고정을 없애고 **방(프로젝트)** 체계로 바꿨다. 화면은 3단이다.
//   방 목록 → 방(대화) → 지침 설정
// 지침은 방마다 다르고 버전으로 관리되므로 설정 화면이 아니라 방 안에 있다.
//
// **대화창은 ChatContent를 재사용하지 않는다.** 모양은 같게 맞추되 별도로 둔다 —
// ChatContent는 전역 store·WS 싱글턴에 묶여 있어 같은 대화 세션을 공유하게 되고,
// 무엇보다 **첨부 의미가 정반대다**: 채팅 첨부는 RAG 벡터 스토어에 영구 등록되지만
// 딥 리서치 첨부는 텍스트만 뽑고 등록하지 않는다. 섞으면 리서치용 임시 RFP가
// 사내 문서 저장소에 영구 등록된다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Copy,
  FileDown,
  FileSearch,
  FileText,
  History,
  Network,
  Paperclip,
  Plus,
  RotateCcw,
  Save,
  Send,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import {
  API_BASE,
  createNote,
  createResearchProject,
  clearResearchTurns,
  deleteResearchProject,
  deleteInstructionVersion,
  deleteResearchRun,
  downloadReportPdf,
  fetchInstructions,
  fetchResearchProjects,
  fetchResearchTurns,
  restoreInstructions,
  saveInstructions,
  updateResearchProject,
  type InstructionVersionInfo,
  type ResearchProject,
  type ResearchTurn,
} from "../services/api";
import { readSseStream } from "../services/sse";
import { useStore } from "../store";
import {
  cleanReportMarkdown,
  researchTitle,
  safeFileStem,
} from "../reportDoc";
import { ModelBadge } from "./ModelBadge";

/** 리서치가 끝나기 전에 스트림이 끊겼을 때 보여줄 문구 (CR-57). */
const DISCONNECTED_MSG =
  "리서치가 도중에 끊겼습니다 — 새싹이 서버가 멈춘 것 같아요. " +
  "잠시 후 새로고침하고 다시 시도해 주세요. 같은 증상이 반복되면 관리자에게 알려주세요.";

const ACCEPT = ".pdf,.docx,.pptx,.hwpx,.txt,.md";
const STALL_SEC = 120;

interface SseEvent {
  stage: string;
  message?: string;
  report?: string;
  sources?: { n: number; doc_id: string; doc_name: string; page: number | null; score: number }[];
  sub_queries?: string[];
  elapsed_seconds?: number;
  position?: number;
}

type Screen = "list" | "room" | "instructions";

function btn(active = false): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    background: active ? "var(--color-accent)" : "transparent",
    border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
    borderRadius: 6,
    color: active ? "#fff" : "var(--color-text-muted)",
    cursor: "pointer",
    padding: "5px 11px",
    fontSize: "var(--fs-12)",
    fontFamily: "inherit",
  };
}

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
    <button onClick={onClick} title={title} style={{ ...btn(), padding: "4px 10px", fontSize: "var(--fs-11)" }}>
      {children}
    </button>
  );
}

// ── 보고서 카드 (assistant 턴) ────────────────────────────────────────────────

function ReportCard({
  report,
  sources,
  steps,
  projectName,
  prompt,
  fileName,
  onPin,
  onNote,
}: {
  report: string;
  sources: ResearchTurn["sources"];
  steps: string[];
  projectName: string;
  prompt: string;
  fileName: string;
  onPin: (ids: string[]) => void;
  onNote: (title: string, content: string, docIds: string[]) => void;
}): React.ReactElement {
  const printRef = useRef<HTMLDivElement>(null);
  const [stepsOpen, setStepsOpen] = useState(false);
  const clean = cleanReportMarkdown(report);
  const docIds = Array.from(new Set(sources.map((s) => s.doc_id).filter(Boolean)));

  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfErr, setPdfErr] = useState("");

  // CR-67: 인쇄 대화상자가 아니라 **PDF 파일 내려받기**. 화면이 아니라 보고서 본문을
  // 서버가 PDF로 만든다 — iOS Safari는 숨은 iframe 대신 화면 전체를 인쇄했다.
  async function handlePdf(): Promise<void> {
    if (pdfBusy) return;
    setPdfBusy(true);
    setPdfErr("");
    const title = researchTitle(projectName, report, prompt, fileName);
    try {
      await downloadReportPdf(
        title,
        clean,
        `Deep Research Report · ${new Date().toLocaleDateString("ko-KR")}`,
        safeFileStem(title),
        projectName  // 제목 위 분류줄 — 어느 방에서 나온 보고서인지
      );
    } catch (e) {
      setPdfErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPdfBusy(false);
    }
  }

  function handleDownload(): void {
    const blob = new Blob([clean], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeFileStem(researchTitle(projectName, report, prompt, fileName))}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      ref={printRef}
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: 10,
        padding: "12px 14px",
        background: "var(--color-bg)",
      }}
    >
      <div
        className="dr-print-hide"
        style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}
      >
        {docIds.length > 0 && (
          <ResultBtn onClick={() => onPin(docIds)} title="근거 문서를 그래프 탭에 핀으로 표시">
            <Network size={12} />
            그래프에 핀
          </ResultBtn>
        )}
        <ResultBtn
          onClick={() =>
            onNote(researchTitle(projectName, report, prompt, fileName), clean, docIds)
          }
          title="업무노트로 저장"
        >
          <BookOpen size={12} />
          업무노트로 저장
        </ResultBtn>
        <ResultBtn onClick={() => void handlePdf()} title="PDF 파일로 내려받기">
          <FileDown size={12} />
          {pdfBusy ? "PDF 만드는 중…" : "PDF"}
        </ResultBtn>
        <ResultBtn onClick={handleDownload} title="마크다운 파일로 저장">
          <FileDown size={12} />
          MD
        </ResultBtn>
        <ResultBtn onClick={() => void navigator.clipboard.writeText(clean)} title="본문 복사">
          <Copy size={12} />
          복사
        </ResultBtn>
      </div>

      {pdfErr && (
        <div className="dr-print-hide" style={{ color: "#e57373", fontSize: "var(--fs-11)", marginBottom: 8 }}>
          {pdfErr}
        </div>
      )}

      <div className="md-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            table: ({ children }) => <div className="md-table-wrap">{children}</div>,
          }}
        >
          {clean}
        </ReactMarkdown>
      </div>

      {/* CR-65: 그때의 진행 과정. 기본은 접혀 있고 꺽쇠로 편다 — 보고서가 주인공이다.
          인쇄·PDF에는 넣지 않는다(dr-print-hide). */}
      {steps.length > 0 && (
        <div className="dr-print-hide" style={{ marginTop: 10 }}>
          <button
            onClick={() => setStepsOpen((v) => !v)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              background: "transparent",
              border: "none",
              padding: 0,
              cursor: "pointer",
              color: "var(--color-text-muted)",
              fontSize: "var(--fs-11)",
              fontFamily: "inherit",
            }}
          >
            {stepsOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            진행 과정 {steps.length}단계
          </button>
          {stepsOpen && (
            <div
              style={{
                marginTop: 6,
                maxHeight: 220,
                overflowY: "auto",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                padding: "8px 10px",
                fontSize: "var(--fs-11)",
                color: "var(--color-text-muted)",
                lineHeight: 1.7,
              }}
            >
              {steps.map((s, i) => (
                <div key={i}>{s}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {sources.length > 0 && (
        <div
          style={{
            marginTop: 10,
            paddingTop: 10,
            borderTop: "1px solid var(--color-border)",
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
          }}
        >
          <strong>참고 자료</strong>
          {sources.map((s) => (
            <div key={s.n}>
              [{s.n}] {s.doc_name}
              {s.page != null ? ` p.${s.page}` : ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 본체 ──────────────────────────────────────────────────────────────────────

export function DeepResearchView({ desktop }: { desktop?: boolean }): React.ReactElement {
  const researchScope = useStore((s) => s.researchScope);
  const setResearchScope = useStore((s) => s.setResearchScope);
  const requestGraphPins = useStore((s) => s.requestGraphPins);
  const bumpNotesRevision = useStore((s) => s.bumpNotesRevision);

  const [screen, setScreen] = useState<Screen>("list");
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [current, setCurrent] = useState<ResearchProject | null>(null);
  const [turns, setTurns] = useState<ResearchTurn[]>([]);
  const [error, setError] = useState("");

  // 실행 상태
  const [prompt, setPrompt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  // 도는 동안은 펴 두고, 끝나면 접는다 — 보고서가 화면을 차지해야 한다.
  const [stepsOpen, setStepsOpen] = useState(true);
  const stepsBoxRef = useRef<HTMLDivElement | null>(null);
  const [queuePos, setQueuePos] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [lastEventAt, setLastEventAt] = useState(0);
  const [now, setNow] = useState(Date.now());

  const fileRef = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  // CR-66: 방 안의 대화를 **실행 단위(질문+보고서)**로 묶어 목록으로 보여준다.
  // 예전에는 전부 한 줄로 이어 붙여서, 지난 리서치를 골라 보거나 하나만 지울 수 없었다.
  const [openRun, setOpenRun] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const list = await fetchResearchProjects();
    setProjects(list);
    return list;
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // stall 감지 (CR-57) — 진행 중일 때만 시계를 돌린다.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  const stalled = running && lastEventAt > 0 && (now - lastEventAt) / 1000 > STALL_SEC;

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [turns.length, steps.length]);

  // 진행 과정 상자는 **자기 안에서** 최신 줄로 따라간다. 상자에 스크롤을 두면
  // 전체를 되짚어 볼 수 있고, 도는 동안에는 마지막 줄이 보여야 한다.
  useEffect(() => {
    if (!stepsOpen || !running) return;
    const el = stepsBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [steps.length, stepsOpen, running]);

  // 리서치가 끝나면 접는다 — 보고서가 주인공이고, 과정은 꺽쇠로 다시 편다.
  const prevRunning = useRef(running);
  useEffect(() => {
    if (prevRunning.current && !running) setStepsOpen(false);
    if (!prevRunning.current && running) setStepsOpen(true);
    prevRunning.current = running;
  }, [running]);

  // 턴 목록을 **실행 단위**로 묶는다 — 사용자가 보는 단위는 "질문 하나 + 그 보고서"다.
  const runs = useMemo(() => {
    const out: {
      id: string;
      title: string;
      when: string;
      file: string;
      ask: ResearchTurn | null;
      report: ResearchTurn | null;
    }[] = [];
    for (let i = 0; i < turns.length; i++) {
      const t = turns[i];
      if (t.role !== "user") {
        // 짝 없는 보고서(옛 기록·중간 실패)도 한 건으로 세운다 — 안 보이면 지울 수도 없다.
        if (!out.length || out[out.length - 1].report) {
          out.push({
            id: t.turn_id,
            title: researchTitle(current?.name ?? "", t.content, "", ""),
            when: new Date(t.created_at).toLocaleString("ko-KR"),
            file: "",
            ask: null,
            report: t,
          });
        }
        continue;
      }
      const next = turns[i + 1]?.role === "assistant" ? turns[i + 1] : null;
      out.push({
        id: t.turn_id,
        title: (t.content || "").trim() || t.attachments[0] || "(첨부만)",
        when: new Date(t.created_at).toLocaleString("ko-KR"),
        file: t.attachments[0] ?? "",
        ask: t,
        report: next,
      });
      if (next) i += 1;
    }
    return out.reverse(); // 최신이 위
  }, [turns, current?.name]);

  const activeRun = useMemo(
    () => runs.find((r) => r.id === openRun) ?? null,
    [runs, openRun]
  );

  async function handleDeleteRun(r: { id: string; title: string }): Promise<void> {
    if (!current) return;
    if (!window.confirm(`이 리서치 기록을 삭제할까요?\n\n${r.title}\n\n되돌릴 수 없습니다.`))
      return;
    try {
      await deleteResearchRun(current.project_id, r.id);
      if (openRun === r.id) setOpenRun(null);
      setTurns(await fetchResearchTurns(current.project_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function openRoom(p: ResearchProject): Promise<void> {
    setCurrent(p);
    setScreen("room");
    setError("");
    setSteps([]);
    setOpenRun(null);
    setTurns(await fetchResearchTurns(p.project_id));
  }

  async function handleCreate(): Promise<void> {
    const name = window.prompt("새 리서치 방 이름을 입력하세요.\n예: 사내 교육자료 정리");
    if (!name?.trim()) return;
    try {
      const p = await createResearchProject({ name: name.trim(), instructions: "" });
      await reload();
      await openRoom(p);
      setScreen("instructions"); // 새 방은 지침부터 쓰게 한다
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(p: ResearchProject): Promise<void> {
    if (
      !window.confirm(
        `'${p.name}' 방을 삭제합니다.\n지침 이력과 대화 기록도 함께 지워집니다. 계속할까요?`
      )
    )
      return;
    await deleteResearchProject(p.project_id);
    if (current?.project_id === p.project_id) {
      setCurrent(null);
      setScreen("list");
    }
    await reload();
  }

  async function handleRun(): Promise<void> {
    if (!current || running) return;
    const text = prompt.trim();
    if (!text && !file) return;

    setRunning(true);
    setError("");
    setSteps([]);
    setOpenRun(null);
    setQueuePos(0);
    setElapsed(0);
    setLastEventAt(Date.now());
    const askedFile = file?.name ?? "";
    // 낙관적 표시 — 서버가 기록하기 전에도 내가 뭘 물었는지 보여야 한다.
    setTurns((prev) => [
      ...prev,
      {
        turn_id: `local-${Date.now()}`,
        role: "user",
        content: text,
        sources: [],
        attachments: askedFile ? [askedFile] : [],
        created_at: new Date().toISOString(),
      },
    ]);
    setPrompt("");
    setFile(null);

    try {
      const form = new FormData();
      form.append("project_id", current.project_id);
      form.append("prompt", text);
      if (researchScope?.length)
        form.append("scope_doc_ids", JSON.stringify(researchScope.map((s) => s.id)));
      if (askedFile && fileRef.current?.files?.[0]) form.append("file", fileRef.current.files[0]);

      const res = await fetch(API_BASE + "/api/deep-research/run-stream", {
        method: "POST",
        body: form,
      });
      let finished = false;
      await readSseStream<SseEvent>(res, (evt) => {
        setLastEventAt(Date.now());
        if (evt.stage === "error") {
          setError(evt.message ?? "알 수 없는 오류");
          finished = true;
        } else if (evt.stage === "done") {
          finished = true;
          setTurns((prev) => [
            ...prev,
            {
              turn_id: `local-a-${Date.now()}`,
              role: "assistant",
              content: evt.report ?? "",
              sources: evt.sources ?? [],
              attachments: [],
              created_at: new Date().toISOString(),
            },
          ]);
        } else if (evt.stage === "queued") {
          setQueuePos(evt.position ?? 0);
          if (evt.message) setSteps((s) => [...s.slice(-300), evt.message!]);
        } else if (evt.stage === "synthesis_tick") {
          setElapsed(evt.elapsed_seconds ?? 0);
        } else if (evt.message) {
          setSteps((s) => [...s.slice(-300), evt.message!]);
        }
      });
      if (!finished) setError(DISCONNECTED_MSG);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleNote(title: string, content: string, docIds: string[]): Promise<void> {
    try {
      await createNote({ title, content, tags: ["딥리서치"], related_docs: docIds });
      bumpNotesRevision();
      setError("");
    } catch (e) {
      setError(`노트 저장 실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  // ── 화면: 방 목록 ──────────────────────────────────────────────────────────

  if (screen === "list") {
    return (
      <div className="dr-column" style={{ overflowY: "auto", padding: 16, maxWidth: 1400 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <h2 style={{ margin: 0, fontSize: "var(--fs-18)" }}>딥 리서치</h2>
          <ModelBadge modelKey="deep_research" />
        </div>
        <p style={{ color: "var(--color-text-muted)", fontSize: "var(--fs-12)", marginTop: 4 }}>
          방을 만들어 지침을 정하고, 사내 자료를 근거로 조사합니다. 분야는 제한이 없습니다.
        </p>

        {error && (
          <div style={{ color: "#e57373", fontSize: "var(--fs-12)", marginBottom: 8 }}>{error}</div>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: desktop ? "repeat(auto-fill, minmax(260px, 1fr))" : "1fr",
            gap: 10,
            marginTop: 12,
          }}
        >
          {projects.map((p) => (
            <div
              key={p.project_id}
              style={{
                border: "1px solid var(--color-border)",
                borderRadius: 10,
                padding: "12px 14px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <button
                onClick={() => void openRoom(p)}
                style={{
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  textAlign: "left",
                  cursor: "pointer",
                  color: "var(--color-text)",
                  fontFamily: "inherit",
                }}
              >
                <div style={{ fontWeight: 700, fontSize: "var(--fs-14)" }}>
                  {p.icon ? `${p.icon} ` : ""}
                  {p.name}
                </div>
                <div
                  style={{
                    fontSize: "var(--fs-11)",
                    color: "var(--color-text-muted)",
                    marginTop: 4,
                    minHeight: 30,
                  }}
                >
                  {p.description || "설명 없음"}
                </div>
              </button>
              <div
                style={{
                  display: "flex",
                  gap: 6,
                  alignItems: "center",
                  fontSize: "var(--fs-11)",
                  color: "var(--color-text-muted)",
                }}
              >
                <span>지침 v{p.version_no}</span>
                <span>· 질의 {p.sub_queries}</span>
                <button
                  onClick={() => void handleDelete(p)}
                  title="이 방을 삭제합니다"
                  style={{ ...btn(), marginLeft: "auto", padding: "3px 8px", color: "#c0392b", borderColor: "#c0392b" }}
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
          ))}

          <button
            onClick={() => void handleCreate()}
            style={{
              border: "1px dashed var(--color-border)",
              borderRadius: 10,
              padding: "20px 14px",
              background: "transparent",
              color: "var(--color-text-muted)",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: "var(--fs-13)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              minHeight: 96,
            }}
          >
            <Plus size={16} />새 방 만들기
          </button>
        </div>
      </div>
    );
  }

  if (!current) return <div />;

  // ── 화면: 지침 설정 ────────────────────────────────────────────────────────

  if (screen === "instructions") {
    return (
      <InstructionsScreen
        project={current}
        onBack={async () => {
          const list = await reload();
          const fresh = list.find((x) => x.project_id === current.project_id);
          if (fresh) setCurrent(fresh);
          setScreen("room");
        }}
      />
    );
  }

  // ── 화면: 방 (대화) ────────────────────────────────────────────────────────

  return (
    <div className="dr-column" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 14px",
          borderBottom: "1px solid var(--color-border)",
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        <button onClick={() => setScreen("list")} style={btn()} title="방 목록으로">
          <ArrowLeft size={12} />
        </button>
        <strong style={{ fontSize: "var(--fs-14)" }}>
          {current.icon ? `${current.icon} ` : ""}
          {current.name}
        </strong>
        <ModelBadge modelKey="deep_research" />
        <button
          onClick={() => setScreen("instructions")}
          style={{ ...btn(), marginLeft: "auto" }}
          title="이 방의 지침과 검색 설정을 편집합니다"
        >
          <Settings2 size={12} />
          지침
        </button>
        {turns.length > 0 && (
          <button
            onClick={async () => {
              if (!window.confirm("이 방의 대화 기록을 비웁니다. 지침은 그대로입니다.")) return;
              await clearResearchTurns(current.project_id);
              setTurns([]);
            }}
            style={btn()}
            title="대화 기록만 비웁니다 (지침은 유지)"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>

      {researchScope && researchScope.length > 0 && (
        <div
          style={{
            padding: "6px 14px",
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          <FileSearch size={12} />
          검색 범위: 그래프 핀 문서 {researchScope.length}건
          <button onClick={() => setResearchScope([])} style={{ ...btn(), padding: "2px 7px" }}>
            해제
          </button>
        </div>
      )}

      <div ref={threadRef} style={{ flex: 1, overflowY: "auto", padding: 14, minHeight: 0 }}>
        {runs.length === 0 && !running && (
          <div
            style={{
              color: "var(--color-text-muted)",
              fontSize: "var(--fs-12)",
              textAlign: "center",
              marginTop: 40,
            }}
          >
            무엇을 조사할지 적거나 파일을 첨부해 시작하세요.
            <br />이 방의 지침에 따라 보고서를 만듭니다.
          </div>
        )}

        {/* 상세: 고른 리서치 한 건만 보여준다 */}
        {activeRun && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <button onClick={() => setOpenRun(null)} style={{ ...btn(), alignSelf: "flex-start" }}>
              <ArrowLeft size={12} />
              목록으로
            </button>
            <div
              style={{
                alignSelf: "flex-end",
                maxWidth: "78%",
                background: "var(--color-accent)",
                color: "#fff",
                borderRadius: 12,
                padding: "8px 12px",
                fontSize: "var(--fs-13)",
                whiteSpace: "pre-wrap",
              }}
            >
              {activeRun.ask?.content || "(첨부만)"}
              {activeRun.ask?.attachments.map((a) => (
                <div key={a} style={{ fontSize: "var(--fs-11)", opacity: 0.85, marginTop: 4 }}>
                  <Paperclip size={10} /> {a}
                </div>
              ))}
            </div>
            {activeRun.report && (
              <ReportCard
                report={activeRun.report.content}
                sources={activeRun.report.sources}
                steps={activeRun.report.steps ?? []}
                projectName={current.name}
                prompt={activeRun.ask?.content ?? ""}
                fileName={activeRun.ask?.attachments?.[0] ?? ""}
                onPin={requestGraphPins}
                onNote={(title, content, ids) => void handleNote(title, content, ids)}
              />
            )}
          </div>
        )}

        {/* 목록: 지난 리서치를 한 건씩 슬롯으로 */}
        {!activeRun && runs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
              지난 리서치 {runs.length}건 — 눌러서 보고서를 봅니다.
            </div>
            {runs.map((r) => (
              <div
                key={r.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  border: "1px solid var(--color-border)",
                  borderRadius: 10,
                  padding: "10px 12px",
                  background: "var(--color-bg)",
                }}
              >
                <button
                  onClick={() => setOpenRun(r.id)}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    textAlign: "left",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    padding: 0,
                    fontFamily: "inherit",
                    color: "var(--color-text)",
                  }}
                >
                  <div
                    style={{
                      fontSize: "var(--fs-13)",
                      fontWeight: 600,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.title}
                  </div>
                  <div
                    style={{
                      fontSize: "var(--fs-11)",
                      color: "var(--color-text-muted)",
                      marginTop: 2,
                    }}
                  >
                    {r.when}
                    {r.report ? "" : " · 보고서 없음"}
                    {r.file ? ` · 📎 ${r.file}` : ""}
                  </div>
                </button>
                <button
                  onClick={() => void handleDeleteRun(r)}
                  title="이 리서치 기록 삭제"
                  style={{
                    ...btn(),
                    padding: "4px 8px",
                    color: "#c0392b",
                    borderColor: "#c0392b55",
                    flexShrink: 0,
                  }}
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 진행 과정 (사용자 요청 2026-08-09).
            예전에는 `running &&`이라 **끝나면 통째로 사라졌고**, `slice(-6)`으로 최근
            6줄만 보여 준 데다 스크롤도 없어 지나간 내용을 볼 방법이 없었다.
            이제 (1) 도는 동안 전체를 스크롤로 보고 (2) 끝나면 접힌 채 남아 꺽쇠로 편다. */}
        {steps.length > 0 && (
          <div
            style={{
              marginTop: 12,
              border: "1px solid var(--color-border)",
              borderRadius: 10,
              fontSize: "var(--fs-11)",
              color: "var(--color-text-muted)",
              overflow: "hidden",
            }}
          >
            <button
              onClick={() => setStepsOpen((v) => !v)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
                background: "transparent",
                border: "none",
                padding: "8px 12px",
                cursor: "pointer",
                color: "var(--color-text-muted)",
                fontSize: "var(--fs-11)",
                fontFamily: "inherit",
                textAlign: "left",
              }}
            >
              {stepsOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span>
                진행 과정 {steps.length}단계
                {running ? " · 진행 중…" : " · 완료"}
              </span>
            </button>
            {stepsOpen && (
              <div
                ref={stepsBoxRef}
                style={{
                  maxHeight: 220,
                  overflowY: "auto",
                  padding: "0 12px 10px",
                  lineHeight: 1.7,
                }}
              >
                {queuePos > 0 && <div>대기 {queuePos}번째 — 차례가 되면 자동 시작합니다.</div>}
                {steps.map((s, i) => (
                  <div key={i}>{s}</div>
                ))}
                {running && elapsed > 0 && <div>보고서 작성 중… {elapsed}초 경과</div>}
                {stalled && (
                  <div style={{ color: "#e0a75f", marginTop: 4 }}>
                    {STALL_SEC}초 넘게 응답이 없습니다. 서버가 바쁘거나 멈췄을 수 있습니다.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {error && (
          <div
            style={{
              marginTop: 12,
              color: "#e57373",
              fontSize: "var(--fs-12)",
              border: "1px solid #e5737355",
              borderRadius: 8,
              padding: "8px 12px",
            }}
          >
            {error}
          </div>
        )}
      </div>

      {/* 입력 알약 — 새싹이 대화창과 같은 모양. 첨부는 **RAG에 등록하지 않는다.** */}
      <div style={{ padding: "10px 14px", borderTop: "1px solid var(--color-border)", flexShrink: 0 }}>
        {file && (
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: "var(--fs-11)",
              border: "1px solid var(--color-border)",
              borderRadius: 999,
              padding: "3px 10px",
              marginBottom: 6,
              color: "var(--color-text-muted)",
            }}
          >
            <FileText size={11} />
            {file.name}
            <button
              onClick={() => {
                setFile(null);
                if (fileRef.current) fileRef.current.value = "";
              }}
              style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0 }}
            >
              <X size={11} />
            </button>
          </div>
        )}
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            gap: 8,
            border: "1px solid var(--color-border)",
            borderRadius: 22,
            padding: "6px 8px 6px 12px",
            background: "var(--color-bg)",
          }}
        >
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            style={{ display: "none" }}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={running}
            title="파일 첨부 (텍스트만 읽고 문서 저장소에 등록하지 않습니다)"
            style={{
              background: "none",
              border: "none",
              cursor: running ? "default" : "pointer",
              color: "var(--color-text-muted)",
              padding: 4,
            }}
          >
            <Paperclip size={16} />
          </button>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void handleRun();
              }
            }}
            disabled={running}
            rows={1}
            placeholder={running ? "리서치 진행 중…" : "무엇을 조사할까요? (Shift+Enter 줄바꿈)"}
            style={{
              flex: 1,
              resize: "none",
              border: "none",
              outline: "none",
              background: "transparent",
              color: "var(--color-text)",
              fontSize: "var(--fs-13)",
              fontFamily: "inherit",
              maxHeight: 140,
              padding: "6px 0",
            }}
          />
          <button
            onClick={() => void handleRun()}
            disabled={running || (!prompt.trim() && !file)}
            title="딥 리서치 시작"
            style={{
              background: running || (!prompt.trim() && !file) ? "transparent" : "var(--color-accent)",
              border: "none",
              borderRadius: "50%",
              width: 32,
              height: 32,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: running || (!prompt.trim() && !file) ? "default" : "pointer",
              color: running || (!prompt.trim() && !file) ? "var(--color-text-muted)" : "#fff",
            }}
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 지침 설정 화면 ────────────────────────────────────────────────────────────

function InstructionsScreen({
  project,
  onBack,
}: {
  project: ResearchProject;
  onBack: () => void | Promise<void>;
}): React.ReactElement {
  const [text, setText] = useState(project.instructions);
  const [versions, setVersions] = useState<InstructionVersionInfo[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [err, setErr] = useState("");
  const [settings, setSettings] = useState({
    sub_queries: project.sub_queries,
    top_k_per_query: project.top_k_per_query,
    gap_rounds: project.gap_rounds,
    max_evidence_chunks: project.max_evidence_chunks,
    planner_hint: project.planner_hint,
    description: project.description,
  });

  const load = useCallback(async () => {
    try {
      const data = await fetchInstructions(project.project_id);
      setText(data.instructions);
      setVersions(data.versions);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [project.project_id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSave(): Promise<void> {
    setSaving(true);
    setErr("");
    try {
      const r = await saveInstructions(project.project_id, text);
      await updateResearchProject(project.project_id, settings);
      setSaved(`v${r.version_no} 저장됨`);
      await load();
      setTimeout(() => setSaved(""), 3000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleRestore(versionNo: number): Promise<void> {
    if (!window.confirm(`v${versionNo}을 사용합니다.\n편집 중인 내용은 사라집니다.`)) return;
    try {
      const r = await restoreInstructions(project.project_id, versionNo);
      setText(r.content);
      setSaved(`v${versionNo} 사용 중`);
      await load();
      setTimeout(() => setSaved(""), 4000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDeleteVersion(versionNo: number): Promise<void> {
    if (!window.confirm(`v${versionNo}을 이력에서 지웁니다.\n되돌릴 수 없습니다.`)) return;
    try {
      await deleteInstructionVersion(project.project_id, versionNo);
      await load();
      setSaved(`v${versionNo} 삭제됨`);
      setTimeout(() => setSaved(""), 4000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  // 숫자 설정 하나. 예전에는 라벨 4개가 한 줄에 붙어 있고 설명은 title 속성(마우스를
  // 올려야 보임)뿐이라, 모바일에서는 각 숫자가 무슨 뜻인지 알 방법이 없었다.
  // 라벨 아래 설명을 항상 보이게 두고 세로로 쌓는다.
  const num = (
    label: string,
    key: "sub_queries" | "top_k_per_query" | "gap_rounds" | "max_evidence_chunks",
    min: number,
    max: number,
    hint: string
  ): React.ReactElement => (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <input
        type="number"
        min={min}
        max={max}
        value={settings[key]}
        aria-label={label}
        onChange={(e) =>
          setSettings((s) => ({ ...s, [key]: Math.max(min, Math.min(max, Number(e.target.value) || min)) }))
        }
        style={{
          width: 64,
          flexShrink: 0,
          padding: "6px 8px",
          borderRadius: 6,
          border: "1px solid var(--color-border)",
          background: "var(--color-bg)",
          color: "var(--color-text)",
          fontSize: "var(--fs-13)",
          fontFamily: "inherit",
          textAlign: "center",
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, color: "var(--color-text)" }}>
          {label}{" "}
          <span style={{ fontWeight: 400, color: "var(--color-text-muted)" }}>
            ({min}~{max})
          </span>
        </div>
        <p
          style={{
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            lineHeight: 1.65,
            margin: "3px 0 0",
          }}
        >
          {hint}
        </p>
      </div>
    </div>
  );

  // 여러 줄 텍스트. 예전에는 한 줄 input이라 긴 문장이 잘려 앞부분만 보였다 —
  // 지침 성격의 문장을 넣는 칸인데 내용을 읽을 수가 없었다.
  const longText = (
    label: string,
    hint: string,
    key: "planner_hint" | "description",
    placeholder: string,
    rows: number
  ): React.ReactElement => (
    <div>
      <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <p
        style={{
          fontSize: "var(--fs-11)",
          color: "var(--color-text-muted)",
          lineHeight: 1.65,
          margin: "0 0 6px",
        }}
      >
        {hint}
      </p>
      <textarea
        value={settings[key]}
        rows={rows}
        onChange={(e) => setSettings((s) => ({ ...s, [key]: e.target.value }))}
        placeholder={placeholder}
        style={{
          width: "100%",
          padding: "8px 10px",
          borderRadius: 8,
          border: "1px solid var(--color-border)",
          background: "var(--color-bg)",
          color: "var(--color-text)",
          fontSize: "var(--fs-12)",
          fontFamily: "inherit",
          lineHeight: 1.7,
          resize: "vertical",
          boxSizing: "border-box",
        }}
      />
    </div>
  );

  return (
    <div className="dr-column" style={{ overflowY: "auto", padding: 14, maxWidth: 1000 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        <button onClick={() => void onBack()} style={btn()}>
          <ArrowLeft size={12} />방으로
        </button>
        <strong style={{ fontSize: "var(--fs-14)" }}>{project.name} — 지침</strong>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
          현재 v{project.version_no}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button onClick={() => setShowHistory((v) => !v)} style={btn(showHistory)} title="지침 버전 이력">
            <History size={12} />
            되돌리기
          </button>
          <button onClick={() => void handleSave()} disabled={saving} style={btn(true)}>
            <Save size={12} />
            {saving ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>

      {saved && (
        <div style={{ fontSize: "var(--fs-11)", color: "var(--color-accent)", marginBottom: 8 }}>
          {saved}
        </div>
      )}
      {err && (
        <div style={{ fontSize: "var(--fs-11)", color: "#e57373", marginBottom: 8 }}>{err}</div>
      )}

      {showHistory && (
        <div
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            padding: 10,
            marginBottom: 10,
            maxHeight: 240,
            overflowY: "auto",
          }}
        >
          <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", marginBottom: 6 }}>
            버전을 고르면 그 버전을 씁니다 — 이력은 그대로 남고 번호도 늘지 않습니다.
          </div>
          {versions.map((v) => (
            <div
              key={v.version_id}
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                padding: "5px 0",
                borderTop: "1px solid var(--color-border)",
                fontSize: "var(--fs-11)",
              }}
            >
              <strong style={{ minWidth: 34 }}>v{v.version_no}</strong>
              <span style={{ color: "var(--color-text-muted)", minWidth: 130 }}>{v.created_at}</span>
              <span
                style={{
                  color: "var(--color-text-muted)",
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {v.note ? `[${v.note}] ` : ""}
                {v.preview}
              </span>
              <span style={{ color: "var(--color-text-muted)" }}>{v.chars}자</span>
              {v.version_no === project.version_no && (
                <span
                  style={{
                    fontSize: "var(--fs-11)",
                    color: "var(--color-accent)",
                    fontWeight: 600,
                    flexShrink: 0,
                  }}
                >
                  사용 중
                </span>
              )}
              {/* CR-71: 현재 버전에도 버튼을 둔다. 편집 중인 내용을 버리고 저장된
                  상태로 되돌리는 유일한 경로다 — 예전에는 이 버튼을 숨겨서
                  **v1만 있는 방에서는 되돌릴 방법이 아예 없었다.** */}
              <button
                onClick={() => void handleRestore(v.version_no)}
                title={
                  v.version_no === project.version_no
                    ? "편집 중인 내용을 버리고 이 버전으로 되돌리기"
                    : "이 버전을 사용"
                }
                style={{ ...btn(), padding: "2px 8px", flexShrink: 0 }}
              >
                <RotateCcw size={10} />
                {v.version_no === project.version_no ? "되돌리기" : "이 버전 사용"}
              </button>
              {versions.length > 1 && (
                <button
                  onClick={() => void handleDeleteVersion(v.version_no)}
                  title="이 버전 삭제"
                  style={{
                    ...btn(),
                    padding: "2px 6px",
                    color: "#c0392b",
                    borderColor: "#c0392b55",
                    flexShrink: 0,
                  }}
                >
                  <Trash2 size={10} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={18}
        placeholder={
          "이 방이 어떤 보고서를 써야 하는지 적으세요.\n" +
          "예) 당신은 사내 교육자료를 정리하는 담당자입니다. 아래 구성으로 작성하세요...\n\n" +
          "비워 두면 분야를 가정하지 않는 범용 형식으로 씁니다.\n" +
          "근거 인용 규칙과 마크다운 출력 형식은 항상 자동으로 붙습니다(환각 억제)."
        }
        style={{
          width: "100%",
          padding: 10,
          borderRadius: 8,
          border: "1px solid var(--color-border)",
          background: "var(--color-bg)",
          color: "var(--color-text)",
          // 고정폭 글꼴을 쓰면 **한글이 글자마다 벌어져** 읽기가 어렵다. 여기 들어가는
          // 내용은 코드가 아니라 사람이 쓴 문장이므로 본문 글꼴을 쓴다.
          fontFamily: "inherit",
          fontSize: "var(--fs-13)",
          lineHeight: 1.8,
          resize: "vertical",
          boxSizing: "border-box",
        }}
      />

      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <strong style={{ fontSize: "var(--fs-12)" }}>검색 설정</strong>
        <p
          style={{
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            lineHeight: 1.7,
            margin: 0,
          }}
        >
          리서치는 <b>질문을 여러 갈래로 쪼개 각각 검색</b>한 뒤 모아서 보고서를 씁니다.
          아래 숫자가 그 규모를 정합니다. 이 값은 <b>이 방에만</b> 적용됩니다.
        </p>
        {/* 계산식을 보여 주면 어느 숫자를 만져야 하는지 바로 안다.
            예전에는 라벨 4개만 있어서 k를 올려도 왜 효과가 없는지 알 수 없었다. */}
        <div
          style={{
            fontSize: "var(--fs-11)",
            color: "var(--color-text-muted)",
            background: "var(--color-bg)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            padding: "8px 10px",
            lineHeight: 1.7,
          }}
        >
          지금 설정: 질의 <b>{settings.sub_queries}</b>개 × 질의당{" "}
          <b>{settings.top_k_per_query}</b>건 = 최대{" "}
          <b>{settings.sub_queries * settings.top_k_per_query}</b>건을 모으고, 그중{" "}
          <b>{settings.max_evidence_chunks}</b>건까지 보고서에 씁니다.
          {settings.sub_queries * settings.top_k_per_query <= settings.max_evidence_chunks ? (
            <>
              {" "}
              — 모으는 양이 상한보다 적으니 <b>넓히려면 &lsquo;질문을 쪼갤 개수&rsquo;</b>를 올리세요.
            </>
          ) : (
            <>
              {" "}
              — <b>근거 상한에서 잘립니다.</b> 더 많이 쓰려면 상한을 함께 올리세요.
            </>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {num(
            "질문을 쪼갤 개수",
            "sub_queries",
            1,
            12,
            "하나의 질문을 몇 갈래로 나눠 검색할지. 조사 범위를 넓히는 데 가장 효과가 큽니다. 올리면 그만큼 시간도 늘어납니다."
          )}
          {num(
            "갈래마다 찾을 자료 수",
            "top_k_per_query",
            1,
            15,
            "쪼갠 질문 하나당 가져올 자료 건수. 갈래가 서로 다른 각도라 자료가 잘 겹치지 않습니다 — 실측에서 근거 24건이 서로 다른 문서 24건이었습니다."
          )}
          {num(
            "빈틈 보완 횟수",
            "gap_rounds",
            0,
            3,
            "1차 조사에서 빠진 관점을 찾아 다시 검색하는 횟수. 0이면 건너뜁니다. 늘리면 꼼꼼해지지만 그만큼 오래 걸립니다."
          )}
          {num(
            "보고서에 쓸 근거 상한",
            "max_evidence_chunks",
            5,
            40,
            "위에서 모은 자료 중 실제로 보고서 작성에 넣을 최대 건수. 여기서 잘리므로, 앞 숫자만 올려도 결과가 안 늘어날 수 있습니다."
          )}
        </div>
        {longText(
          "검색 관점 예시",
          "질문을 쪼갤 때 어떤 각도로 나눌지 알려 줍니다. 비워 두면 스스로 정합니다. " +
            "이 방이 다루는 분야에서 늘 확인해야 하는 항목을 적어 두면 좋습니다.",
          "planner_hint",
          "예: 핵심 주제어, 유사한 기술과 방법, 같은 대상(작물·축종), 사업 기간과 예산 규모",
          3
        )}
        {longText(
          "방 설명",
          "방 목록에서 이 방 이름 아래에 보이는 한 줄 소개입니다. 조사 내용에는 영향을 주지 않습니다.",
          "description",
          "예: 제출 과제와 기존 연구의 중복·차별성을 판정합니다",
          2
        )}
      </div>
    </div>
  );
}

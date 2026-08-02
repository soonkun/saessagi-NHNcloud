import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import ReactMarkdown from "react-markdown";
import { cleanReportMarkdown, printHtmlDocument, safeFileStem } from "../reportDoc";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Plus,
  Trash2,
  Save,
  Pencil,
  Eye,
  Network,
  Search,
  Paperclip,
  ExternalLink,
  Sparkles,
  X,
  Printer,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import type {
  KnowledgeNote,
  KnowledgeNoteMeta,
  KnowledgeGraphData,
} from "../types";
import {
  fetchNotes,
  fetchNote,
  createNote,
  updateNote,
  deleteNote,
  fetchKnowledgeGraph,
  openDocument,
  aiEditNote,
} from "../services/api";
import { useStore } from "../store";
import { invalidateNotesCache } from "../services/websocket";

// 그래프 라이브러리는 노트 탭에 진입한 후 그래프 sub-탭을 처음 클릭할 때만 로드
const NotesGraph = lazy(() => import("./NotesGraph"));
// Notion 스타일 블록 에디터 — 데스크톱 모드 편집 탭에서만 로드 (CR-16)
const NoteRichEditor = lazy(() => import("./NoteRichEditor"));

type SubTab = "edit" | "preview" | "graph";

// "2026-06-12T18:42:05" → "2026.06.12 18:42"
function fmtDateTime(iso?: string): string {
  if (!iso) return "";
  const date = iso.slice(0, 10).replace(/-/g, ".");
  const time = iso.slice(11, 16);
  return time ? `${date} ${time}` : date;
}

export function NotesView({ desktop = false }: { desktop?: boolean }): React.ReactElement {
  const externalSelectedSlug = useStore((s) => s.selectedNoteSlug);
  const setExternalSelectedSlug = useStore((s) => s.setSelectedNoteSlug);
  const theme = useStore((s) => s.theme);
  // 채팅으로 노트가 생성되면 bump됨 → 목록 자동 새로고침 트리거
  const notesRevision = useStore((s) => s.notesRevision);

  const [notes, setNotes] = useState<KnowledgeNoteMeta[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [current, setCurrent] = useState<KnowledgeNote | null>(null);

  // 외부(채팅 노트 칩 클릭)에서 slug 지정 시 동기화
  useEffect(() => {
    if (externalSelectedSlug && externalSelectedSlug !== selectedSlug) {
      setSelectedSlug(externalSelectedSlug);
      // 한 번 사용 후 리셋 (중복 트리거 방지)
      setExternalSelectedSlug(null);
    }
  }, [externalSelectedSlug, selectedSlug, setExternalSelectedSlug]);
  // 펫 모드는 간단 확인 위주 → 기본 '미리보기', 데스크톱은 바로 편집
  const [subTab, setSubTab] = useState<SubTab>(desktop ? "edit" : "preview");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // 편집 buffer
  const [editTitle, setEditTitle] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editContent, setEditContent] = useState("");
  const [dirty, setDirty] = useState(false);

  // ── CR-23: 노트 AI 편집 ────────────────────────────────────────────────────
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiFile, setAiFile] = useState<File | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  // 데스크톱 BlockNote는 마운트 시 1회만 마크다운을 파싱하므로, AI가 editContent를
  // 바꾸면 key를 바꿔 강제 remount한다
  const [aiEditVersion, setAiEditVersion] = useState(0);
  // 선택 영역 스냅샷 — 프롬프트 입력 클릭 시 DOM 선택이 풀리므로 미리 캡처해 둔다
  const selectedTextRef = useRef("");
  const [selectedPreview, setSelectedPreview] = useState("");
  const aiFileInputRef = useRef<HTMLInputElement | null>(null);
  // 인쇄(PDF)용 — 미리보기에 렌더된 본문을 그대로 복제해 독립 문서로 만든다 (CR-59).
  const previewRef = useRef<HTMLDivElement | null>(null);

  // BlockNote(데스크톱)·미리보기의 DOM 선택을 추적
  useEffect(() => {
    function onSelectionChange(): void {
      const sel = window.getSelection()?.toString() ?? "";
      if (sel.trim()) {
        selectedTextRef.current = sel;
        setSelectedPreview(sel.trim().slice(0, 40));
      }
    }
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, []);

  function clearAiSelection(): void {
    selectedTextRef.current = "";
    setSelectedPreview("");
  }

  function renderAiBar(desktopPad = false): React.ReactElement {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
          padding: desktopPad ? "10px 46px 16px" : "8px 0 0",
          flexShrink: 0,
        }}
      >
        {(selectedPreview || aiFile || aiError) && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            {selectedPreview && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "var(--fs-11)",
                  background: "rgba(100,140,220,0.12)",
                  border: "1px solid rgba(100,140,220,0.35)",
                  borderRadius: 6,
                  padding: "2px 8px",
                  color: "var(--color-text)",
                }}
              >
                선택 부분만: “{selectedPreview}…”
                <button
                  onClick={clearAiSelection}
                  title="선택 해제 — 노트 전체 대상으로"
                  style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", display: "flex", padding: 0 }}
                >
                  <X size={11} />
                </button>
              </span>
            )}
            {aiFile && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "var(--fs-11)",
                  background: "var(--color-panel, var(--color-bg))",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  padding: "2px 8px",
                  color: "var(--color-text)",
                }}
              >
                {aiFile.name}
                <button
                  onClick={() => setAiFile(null)}
                  style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", display: "flex", padding: 0 }}
                >
                  <X size={11} />
                </button>
              </span>
            )}
            {aiError && (
              <span style={{ fontSize: "var(--fs-11)", color: "#e05050" }}>{aiError}</span>
            )}
          </div>
        )}
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            ref={aiFileInputRef}
            type="file"
            accept=".pdf,.docx,.pptx,.hwpx,.txt,.md"
            style={{ display: "none" }}
            onChange={(e) => {
              setAiFile(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => aiFileInputRef.current?.click()}
            disabled={aiBusy}
            title="참고 자료 첨부 — 내용을 읽어 노트에 반영"
            style={{
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: aiBusy ? "not-allowed" : "pointer",
              padding: "7px 9px",
              display: "flex",
              alignItems: "center",
              flexShrink: 0,
            }}
          >
            <Paperclip size={13} />
          </button>
          <input
            value={aiInstruction}
            onChange={(e) => setAiInstruction(e.target.value)}
            onClick={() => window.electronAPI?.restoreFocus()}
            onMouseDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) void handleAiEdit();
            }}
            disabled={aiBusy}
            placeholder={
              selectedPreview
                ? "선택한 부분을 어떻게 바꿀까요? (예: 이 부분을 개조식으로)"
                : "AI에게 지시 (예: 첨부 내용으로 회의 결과 정리해줘 / 전체를 개조식으로)"
            }
            style={{
              flex: 1,
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text)",
              padding: "8px 12px",
              fontSize: "var(--fs-13)",
              outline: "none",
            }}
          />
          <button
            onClick={() => void handleAiEdit()}
            disabled={aiBusy || !aiInstruction.trim()}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              background: aiBusy || !aiInstruction.trim() ? "transparent" : "var(--color-accent)",
              border: `1px solid ${aiBusy || !aiInstruction.trim() ? "var(--color-border)" : "var(--color-accent)"}`,
              borderRadius: 8,
              color: aiBusy || !aiInstruction.trim() ? "var(--color-text-muted)" : "#fff",
              cursor: aiBusy || !aiInstruction.trim() ? "not-allowed" : "pointer",
              padding: "7px 14px",
              fontSize: "var(--fs-12)",
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            <Sparkles size={13} />
            {aiBusy ? "편집 중…" : "AI 적용"}
          </button>
        </div>
      </div>
    );
  }

  async function handleAiEdit(): Promise<void> {
    const instruction = aiInstruction.trim();
    if (!instruction || aiBusy) return;
    setAiBusy(true);
    setAiError("");
    // 선택 영역이 실제 본문에 존재할 때만 부분 편집 (미리보기 등 다른 곳 선택 방지)
    const sel = selectedTextRef.current.trim();
    const useSelection = sel.length >= 2 && editContent.includes(sel);
    try {
      const r = await aiEditNote({
        instruction,
        content: editContent,
        title: editTitle,
        selection: useSelection ? sel : undefined,
        file: aiFile,
      });
      if (r.mode === "selection" && useSelection) {
        setEditContent(editContent.replace(sel, r.result));
      } else {
        setEditContent(r.result);
      }
      setDirty(true);
      setAiEditVersion((v) => v + 1);
      setAiInstruction("");
      setAiFile(null);
      clearAiSelection();
    } catch (e) {
      setAiError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiBusy(false);
    }
  }

  // 목록 새로고침
  const refreshList = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchNotes();
      setNotes(list);
    } catch (err) {
      console.warn("[notes] list 실패:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList, notesRevision]);

  // 선택된 노트 로드
  useEffect(() => {
    if (!selectedSlug) {
      setCurrent(null);
      return;
    }
    let cancelled = false;
    void fetchNote(selectedSlug).then((n) => {
      if (cancelled) return;
      setCurrent(n);
      setEditTitle(n.title);
      setEditTags(n.tags.join(", "));
      setEditContent(n.content);
      setDirty(false);
    }).catch((err) => console.warn("[notes] fetch 실패:", err));
    return () => { cancelled = true; };
  }, [selectedSlug]);

  // 필터링
  const filtered = useMemo(() => {
    if (!query.trim()) return notes;
    const q = query.toLowerCase();
    return notes.filter(
      (n) =>
        n.title.toLowerCase().includes(q) ||
        n.slug.toLowerCase().includes(q) ||
        n.tags.some((t) => t.toLowerCase().includes(q))
    );
  }, [notes, query]);

  // 저장
  /**
   * 노트를 PDF로 저장 (CR-59). 딥 리서치 보고서를 노트로 옮겨 둔 뒤 배포할 때 쓰라는
   * 요청. 인쇄 규격은 딥 리서치와 같은 것을 쓴다(reportDoc).
   *
   * 미리보기에 이미 렌더된 HTML을 복제해 넣는다 — 편집 탭에서 눌렀다면 미리보기 DOM이
   * 없으므로 먼저 미리보기로 전환하고, 그려진 뒤에 인쇄한다.
   */
  function handlePrintNote(): void {
    if (!current) return;
    const note = current;
    const run = (): void => {
      const src = previewRef.current;
      if (!src) return;
      const clone = src.cloneNode(true) as HTMLElement;
      // 미리보기 머리말(제목·태그·관련 자료)은 빼낸다 — 인쇄 문서가 자체 머리말을 만든다.
      clone.querySelectorAll(".note-print-hide").forEach((el) => el.remove());
      const meta =
        (note.tags.length ? `${note.tags.join(" · ")} · ` : "") +
        `작성 ${fmtDateTime(note.created)} · 마지막 수정 ${fmtDateTime(note.updated)}`;
      printHtmlDocument(safeFileStem(note.title || note.slug), meta, clone.innerHTML);
    };
    if (subTab === "preview" && previewRef.current) {
      run();
    } else {
      setSubTab("preview");
      window.setTimeout(run, 250); // 미리보기가 그려진 뒤에 복제해야 한다
    }
  }

  const saveCurrent = useCallback(async () => {
    if (!current || !dirty) return;
    const tags = editTags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    try {
      const updated = await updateNote(current.slug, {
        title: editTitle.trim() || current.title,
        content: editContent,
        tags,
      });
      setCurrent(updated);
      setDirty(false);
      setSavedAt(Date.now());
      invalidateNotesCache();
      void refreshList();
    } catch (err) {
      alert(err instanceof Error ? err.message : "저장 실패");
    }
  }, [current, dirty, editTitle, editTags, editContent, refreshList]);

  // 새 노트 만들기 — Electron 투명창에서 window.prompt가 동작 안 함.
  // 빈 제목으로 즉시 생성 후 편집 탭에서 제목 입력 받는다.
  const createNew = useCallback(async () => {
    try {
      const stamp = new Date().toLocaleString("ko-KR", { hour12: false });
      const n = await createNote({
        title: `새 노트 (${stamp})`,
        content: "",
      });
      invalidateNotesCache();
      await refreshList();
      setSelectedSlug(n.slug);
      setSubTab("edit");
      // 제목 input으로 자동 포커스 (다음 tick 후 DOM 렌더링 보장)
      setTimeout(() => {
        const titleInput = document.querySelector<HTMLInputElement>("[data-note-title-input]");
        titleInput?.focus();
        titleInput?.select();
      }, 80);
    } catch (err) {
      alert(err instanceof Error ? err.message : "생성 실패");
    }
  }, [refreshList]);

  // 삭제 — confirm 대신 인라인 확인 (Electron 호환)
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteCurrent = useCallback(async () => {
    if (!current) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      setTimeout(() => setConfirmingDelete(false), 3000);
      return;
    }
    try {
      await deleteNote(current.slug);
      setSelectedSlug(null);
      setCurrent(null);
      setConfirmingDelete(false);
      invalidateNotesCache();
      void refreshList();
    } catch (err) {
      alert(err instanceof Error ? err.message : "삭제 실패");
    }
  }, [current, refreshList, confirmingDelete]);

  // Cmd/Ctrl+S 저장 단축키
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        void saveCurrent();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveCurrent]);

  // 그래프
  const [graph, setGraph] = useState<KnowledgeGraphData | null>(null);
  const graphLoadedRef = useRef(false);
  useEffect(() => {
    if (subTab === "graph" && !graphLoadedRef.current) {
      graphLoadedRef.current = true;
      void fetchKnowledgeGraph().then(setGraph).catch((err) => {
        console.warn("[notes] graph 실패:", err);
      });
    }
  }, [subTab]);
  // 노트 목록이 갱신되면 그래프도 무효화
  useEffect(() => {
    graphLoadedRef.current = false;
    setGraph(null);
  }, [notes]);

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      {/* 좌측: 노트 목록 — 좁은 화면에서는 폭을 줄여 본문(에디터)을 확보한다 (CR-43) */}
      <div
        style={{
          width: 240,
          maxWidth: "38vw",
          minWidth: 150,
          flexShrink: 0,
          borderRight: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <div style={{ padding: "10px 10px 6px", position: "relative" }}>
          <Search
            size={11}
            style={{
              position: "absolute",
              left: 18,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--color-text-muted)",
              pointerEvents: "none",
            }}
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="검색"
            onClick={() => window.electronAPI?.restoreFocus()}
            style={{
              width: "100%",
              boxSizing: "border-box",
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              color: "var(--color-text)",
              padding: "5px 8px 5px 22px",
              fontSize: "var(--fs-12)",
              outline: "none",
            }}
          />
        </div>
        {/* 새 노트 작성 — 점선 박스 */}
        <div style={{ padding: "0 10px 8px" }}>
          <button
            onClick={() => void createNew()}
            title="빈 노트 직접 만들기 (보통은 채팅으로 자동 생성됨)"
            style={{
              width: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              background: "transparent",
              border: "1.5px dashed var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: "8px 10px",
              fontSize: "var(--fs-12)",
              fontFamily: "inherit",
            }}
          >
            <Plus size={13} />새 노트 작성
          </button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 6px 8px" }}>
          {loading && notes.length === 0 && (
            <div style={{ padding: 12, fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
              로딩 중...
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div style={{ padding: 12, fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
              {notes.length === 0 ? "노트가 없습니다." : "검색 결과 없음"}
            </div>
          )}
          {filtered.map((n) => (
            <div
              key={n.slug}
              onClick={() => setSelectedSlug(n.slug)}
              style={{
                padding: "8px 8px",
                borderRadius: 4,
                cursor: "pointer",
                background:
                  selectedSlug === n.slug ? "rgba(100,140,220,0.18)" : "transparent",
                marginBottom: 2,
              }}
            >
              <div
                style={{
                  fontSize: "var(--fs-12)",
                  fontWeight: 600,
                  color: "var(--color-text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {n.title}
              </div>
              <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", marginTop: 2 }}>
                {n.tags.length > 0 ? n.tags.join(" · ") : "태그 없음"}
              </div>
              <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
                {n.updated ? n.updated.slice(0, 10) : ""}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 우측: 편집/미리보기/그래프 */}
      {/* minWidth: 0 필수 — 가로 flex 자식의 기본 min-width:auto 때문에 미리보기의
          긴 코드 토큰이 페인을 부모 폭 밖으로 밀어내 프레임을 벗어난다 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
        {/* sub-tabs */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            borderBottom: "1px solid var(--color-border)",
            paddingRight: 8,
            flexShrink: 0,
          }}
        >
          {([
            { id: "edit", label: "편집", Icon: Pencil },
            { id: "preview", label: "미리보기", Icon: Eye },
            { id: "graph", label: "그래프", Icon: Network },
          ] as { id: SubTab; label: string; Icon: React.ElementType }[]).map(
            ({ id, label, Icon }) => (
              <button
                key={id}
                onClick={() => setSubTab(id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "10px 12px",
                  border: "none",
                  borderBottom:
                    subTab === id
                      ? "2px solid var(--color-accent)"
                      : "2px solid transparent",
                  background: "transparent",
                  color: subTab === id ? "var(--color-accent)" : "var(--color-text-muted)",
                  cursor: "pointer",
                  fontSize: "var(--fs-12)",
                  fontWeight: subTab === id ? 600 : 400,
                }}
              >
                <Icon size={13} />
                {label}
              </button>
            )
          )}
          {current && subTab !== "graph" && (
            <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
              {savedAt && Date.now() - savedAt < 2500 && (
                <span style={{ fontSize: "var(--fs-11)", color: "var(--color-accent)" }}>저장됨 ✓</span>
              )}
              <button
                onClick={handlePrintNote}
                title="인쇄 창에서 '대상'을 'PDF로 저장'으로 고르면 PDF 파일이 됩니다"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  background: "transparent",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: "var(--color-text-muted)",
                  cursor: "pointer",
                  padding: "4px 10px",
                  fontSize: "var(--fs-11)",
                }}
              >
                <Printer size={11} />
                PDF
              </button>
              <button
                onClick={() => void saveCurrent()}
                disabled={!dirty}
                title="저장 (⌘S)"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  background: dirty ? "var(--color-accent)" : "transparent",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: dirty ? "#fff" : "var(--color-text-muted)",
                  cursor: dirty ? "pointer" : "default",
                  padding: "4px 10px",
                  fontSize: "var(--fs-11)",
                  fontWeight: 600,
                }}
              >
                <Save size={11} />
                저장
              </button>
              <button
                onClick={() => void deleteCurrent()}
                title={confirmingDelete ? "한 번 더 클릭하면 삭제됩니다" : "삭제"}
                style={{
                  background: confirmingDelete ? "#c93b3b" : "transparent",
                  border: `1px solid ${confirmingDelete ? "#c93b3b" : "var(--color-border)"}`,
                  borderRadius: 6,
                  color: confirmingDelete ? "#fff" : "var(--color-text-muted)",
                  cursor: "pointer",
                  padding: "4px 8px",
                  fontSize: "var(--fs-11)",
                  fontWeight: confirmingDelete ? 700 : 400,
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                <Trash2 size={11} />
                {confirmingDelete ? "정말 삭제?" : ""}
              </button>
            </div>
          )}
        </div>

        {/* 본문 */}
        <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
          {!current && subTab !== "graph" && (
            <EmptyHint isEmptyAtAll={notes.length === 0} />
          )}
          {current && subTab === "edit" && !desktop && (
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10, flex: 1, overflow: "auto" }}>
              <input
                data-note-title-input
                value={editTitle}
                onChange={(e) => { setEditTitle(e.target.value); setDirty(true); }}
                onClick={() => window.electronAPI?.restoreFocus()}
                style={{
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: "var(--color-text)",
                  padding: "7px 10px",
                  fontSize: "var(--fs-14)",
                  fontWeight: 600,
                  outline: "none",
                }}
                placeholder="제목"
              />
              <input
                value={editTags}
                onChange={(e) => { setEditTags(e.target.value); setDirty(true); }}
                onClick={() => window.electronAPI?.restoreFocus()}
                style={{
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: "var(--color-text)",
                  padding: "6px 10px",
                  fontSize: "var(--fs-12)",
                  outline: "none",
                }}
                placeholder="태그 (쉼표 구분): 회계, 출장"
              />
              <textarea
                value={editContent}
                onChange={(e) => { setEditContent(e.target.value); setDirty(true); }}
                onClick={() => window.electronAPI?.restoreFocus()}
                onSelect={(e) => {
                  // CR-23: textarea 선택은 window.getSelection에 안 잡힘 — 직접 캡처
                  const el = e.currentTarget;
                  const sel = el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0);
                  if (sel.trim()) {
                    selectedTextRef.current = sel;
                    setSelectedPreview(sel.trim().slice(0, 40));
                  }
                }}
                placeholder={"## 상황\n...\n\n## 절차\n...\n\n## 사용 자료\n- [[doc:파일명_xxx]]\n\n## 관련 업무\n- [[다른-슬러그]]"}
                style={{
                  flex: 1,
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: "var(--color-text)",
                  padding: "10px 12px",
                  fontSize: "var(--fs-13)",
                  lineHeight: 1.6,
                  fontFamily: "monospace",
                  outline: "none",
                  resize: "vertical",
                  minHeight: 250,
                }}
              />
              {renderAiBar()}
              <RelatedDocsSection note={current} />
              <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
                slug: <code>{current.slug}</code> · 작성 {fmtDateTime(current.created)} · 마지막 수정 {fmtDateTime(current.updated)}
              </div>
            </div>
          )}
          {current && subTab === "edit" && desktop && (
            <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, overflow: "auto" }}>
              {/* Notion 스타일 헤더 — 테두리 없는 큰 제목 + 옅은 태그 줄 */}
              <div style={{ padding: "24px 46px 0" }}>
                <input
                  data-note-title-input
                  value={editTitle}
                  onChange={(e) => { setEditTitle(e.target.value); setDirty(true); }}
                  onClick={() => window.electronAPI?.restoreFocus()}
                  placeholder="제목 없음"
                  style={{
                    width: "100%",
                    background: "transparent",
                    border: "none",
                    color: "var(--color-text)",
                    padding: 0,
                    fontSize: "var(--fs-28)",
                    fontWeight: 700,
                    outline: "none",
                  }}
                />
                <input
                  value={editTags}
                  onChange={(e) => { setEditTags(e.target.value); setDirty(true); }}
                  onClick={() => window.electronAPI?.restoreFocus()}
                  placeholder="태그 추가 (쉼표 구분)"
                  style={{
                    width: "100%",
                    background: "transparent",
                    border: "none",
                    color: "var(--color-text-muted)",
                    padding: "6px 0 2px",
                    fontSize: "var(--fs-12)",
                    outline: "none",
                  }}
                />
                <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", padding: "2px 0 8px" }}>
                  slug: <code>{current.slug}</code> · 작성 {fmtDateTime(current.created)} · 마지막 수정 {fmtDateTime(current.updated)}
                </div>
                <RelatedDocsSection note={current} />
              </div>
              <Suspense
                fallback={
                  <div style={{ padding: "12px 46px", fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>
                    에디터 로딩 중...
                  </div>
                }
              >
                <NoteRichEditor
                  key={`${current.slug}:${aiEditVersion}`}
                  markdown={editContent}
                  theme={theme === "dark" ? "dark" : "light"}
                  onChangeMarkdown={(md) => { setEditContent(md); setDirty(true); }}
                />
              </Suspense>
              {renderAiBar(true)}
            </div>
          )}
          {current && subTab === "preview" && (
            <div
              ref={previewRef}
              style={{ padding: 20, overflow: "auto", flex: 1, minWidth: 0, fontSize: "var(--fs-13)", lineHeight: 1.6, overflowWrap: "anywhere" }}
            >
              <h2 className="note-print-hide" style={{ fontSize: "var(--fs-18)", fontWeight: 700, marginBottom: 6 }}>{current.title}</h2>
              <div className="note-print-hide" style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", marginBottom: 4 }}>
                {current.tags.join(" · ") || "태그 없음"}
              </div>
              <div className="note-print-hide" style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", marginBottom: 14 }}>
                작성 {fmtDateTime(current.created)} · 마지막 수정 {fmtDateTime(current.updated)}
              </div>
              <div className="note-print-hide">
                <RelatedDocsSection note={current} />
              </div>

              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // 코드 블록·표는 페인 폭을 넘지 않고 내부 가로 스크롤로 격리
                  pre: ({ children }) => (
                    <pre style={{ overflowX: "auto", maxWidth: "100%" }}>{children}</pre>
                  ),
                  table: ({ children }) => (
                    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
                      <table>{children}</table>
                    </div>
                  ),
                  a: ({ children, href }) => {
                    const isWikilink = typeof href === "string" && href.startsWith("#note:");
                    return (
                      <a
                        href={href}
                        onClick={(e) => {
                          if (!isWikilink) return;
                          e.preventDefault();
                          const slug = decodeURIComponent((href as string).slice("#note:".length));
                          setSelectedSlug(slug);
                          setSubTab("edit");
                        }}
                        style={{
                          color: "var(--color-accent)",
                          cursor: isWikilink ? "pointer" : "auto",
                          textDecoration: isWikilink ? "none" : undefined,
                          borderBottom: isWikilink ? "1px dashed var(--color-accent)" : undefined,
                        }}
                      >
                        {children}
                      </a>
                    );
                  },
                }}
              >
                {renderWikilinks(cleanReportMarkdown(current.content))}
              </ReactMarkdown>
            </div>
          )}
          {subTab === "graph" && (
            <Suspense fallback={<div style={{ padding: 20, fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>그래프 로딩 중...</div>}>
              {graph ? (
                <NotesGraph
                  data={graph}
                  onNodeClick={(slug) => {
                    setSelectedSlug(slug);
                    setSubTab("edit");
                  }}
                />
              ) : (
                <div style={{ padding: 20, fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>
                  그래프 데이터 로딩 중...
                </div>
              )}
            </Suspense>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyHint({ isEmptyAtAll }: { isEmptyAtAll: boolean }): React.ReactElement {
  if (isEmptyAtAll) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--color-text-muted)",
          flexDirection: "column",
          gap: 14,
          padding: "0 40px",
          textAlign: "center",
        }}
      >
        <BookOpen size={44} style={{ opacity: 0.35 }} />
        <div style={{ fontSize: "var(--fs-15)", fontWeight: 600, color: "var(--color-text)" }}>
          노트가 비어 있습니다
        </div>
        <div style={{ fontSize: "var(--fs-13)", lineHeight: 1.6 }}>
          새싹이와 채팅하면서 자료를 첨부하고<br />
          <span style={{ color: "var(--color-accent)" }}>"오늘 ⟨이 자료⟩로 ⟨이 업무⟩ 처리했어요"</span><br />
          라고 말해보세요. AI가 알아서 정리해 저장합니다.
        </div>
        <div style={{ fontSize: "var(--fs-11)", opacity: 0.7 }}>
          채팅 입력 영역의 📎 버튼으로 자료를 첨부할 수 있습니다.
        </div>
      </div>
    );
  }
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-text-muted)", flexDirection: "column", gap: 8 }}>
      <BookOpen size={36} style={{ opacity: 0.4 }} />
      <div style={{ fontSize: "var(--fs-13)" }}>왼쪽에서 노트를 선택해 주세요</div>
    </div>
  );
}

// `[[slug]]` 위키링크를 ReactMarkdown이 처리할 수 있도록 마크다운 링크로 변환.
// 관련 자료(첨부 파일) 섹션 — 노트와 연결된 doc_id를 다운로드 가능한 칩으로 표시
/**
 * 몇 건부터 접은 채로 시작할지 (CR-59).
 *
 * 딥 리서치 보고서를 노트로 옮기면 근거 문서가 20건 넘게 붙는다. 그게 다 펼쳐져 있으면
 * 정작 본문이 화면 밖으로 밀린다("참조문서 링크가 너무 많다보니 문서공간을 너무
 * 잡아먹는데" — 사용자). 서너 건은 한눈에 보이는 편이 나으므로 그때는 펼쳐 둔다.
 */
const RELATED_DOCS_COLLAPSE_FROM = 5;

function RelatedDocsSection({ note }: { note: KnowledgeNote }): React.ReactElement | null {
  const docs = note.related_docs_info ?? [];
  // 노트를 옮겨 다닐 때마다 접힘 상태를 새로 정한다 — slug를 key로 삼아 초기화한다.
  const [open, setOpen] = useState(docs.length < RELATED_DOCS_COLLAPSE_FROM);
  const lastSlug = useRef(note.slug);
  if (lastSlug.current !== note.slug) {
    lastSlug.current = note.slug;
    // 렌더 중 상태 갱신이지만 같은 커밋에서 정리된다(React 권장 패턴).
    setOpen(docs.length < RELATED_DOCS_COLLAPSE_FROM);
  }

  if (docs.length === 0) return null;
  return (
    <div
      style={{
        marginBottom: 14,
        padding: "10px 12px",
        borderRadius: 8,
        border: "1px solid var(--color-border)",
        background: "var(--color-bg)",
      }}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        title={open ? "관련 자료 접기" : "관련 자료 펼치기"}
        style={{
          width: "100%",
          fontSize: "var(--fs-11)",
          fontWeight: 600,
          color: "var(--color-text-muted)",
          marginBottom: open ? 6 : 0,
          display: "flex",
          alignItems: "center",
          gap: 4,
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Paperclip size={11} />
        관련 자료 · {docs.length}건
        {!open && <span style={{ fontWeight: 400 }}>(눌러서 펼치기)</span>}
      </button>
      <div style={{ display: open ? "flex" : "none", flexWrap: "wrap", gap: 6 }}>
        {docs.map((d) => {
          const label = d.filename ?? d.id;
          if (!d.filename) {
            return (
              <span
                key={d.id}
                title={`원본 없음: ${d.id}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 8px",
                  fontSize: "var(--fs-11)",
                  borderRadius: 8,
                  background: "transparent",
                  border: "1px dashed var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
              >
                <Paperclip size={11} />
                {label} (원본 없음)
              </span>
            );
          }
          return (
            <button
              key={d.id}
              onClick={(e) => {
                e.stopPropagation();
                openDocument(d.id, d.filename as string);
              }}
              title={`원본 열기: ${label}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "3px 8px",
                fontSize: "var(--fs-11)",
                borderRadius: 8,
                background: "rgba(100,140,220,0.18)",
                border: "1px solid rgba(100,140,220,0.4)",
                color: "#7aa8ff",
                cursor: "pointer",
                fontFamily: "inherit",
                maxWidth: 240,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              <ExternalLink size={11} />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// `doc:xxx`는 다운로드 안내 텍스트로만 표시 (실제 doc id로 다운로드 처리는 추후 Phase에).
function renderWikilinks(text: string): string {
  return text.replace(/\[\[([^\]\|#]+)(?:\|([^\]]*))?\]\]/g, (_, target: string, label?: string) => {
    const t = target.trim();
    if (t.startsWith("doc:")) {
      return `\`📎 ${label?.trim() || t.slice(4)}\``;
    }
    return `[${label?.trim() || t}](#note:${encodeURIComponent(t)})`;
  });
}

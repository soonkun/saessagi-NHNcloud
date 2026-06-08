import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import ReactMarkdown from "react-markdown";
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
  Download,
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
  getDocumentDownloadUrl,
} from "../services/api";
import { useStore } from "../store";
import { invalidateNotesCache } from "../services/websocket";

// 그래프 라이브러리는 노트 탭에 진입한 후 그래프 sub-탭을 처음 클릭할 때만 로드
const NotesGraph = lazy(() => import("./NotesGraph"));

type SubTab = "edit" | "preview" | "graph";

export function NotesView(): React.ReactElement {
  const externalSelectedSlug = useStore((s) => s.selectedNoteSlug);
  const setExternalSelectedSlug = useStore((s) => s.setSelectedNoteSlug);
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
  const [subTab, setSubTab] = useState<SubTab>("edit");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // 편집 buffer
  const [editTitle, setEditTitle] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editContent, setEditContent] = useState("");
  const [dirty, setDirty] = useState(false);

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
      {/* 좌측: 노트 목록 */}
      <div
        style={{
          width: 240,
          borderRight: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <div style={{ padding: "10px 10px 8px", display: "flex", gap: 6, position: "relative" }}>
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
              flex: 1,
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              color: "var(--color-text)",
              padding: "5px 8px 5px 22px",
              fontSize: 12,
              outline: "none",
              minWidth: 0,
            }}
          />
          <button
            onClick={() => void createNew()}
            title="빈 노트 직접 만들기 (보통은 채팅으로 자동 생성됨)"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 6,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: "4px 6px",
              flexShrink: 0,
            }}
          >
            <Plus size={12} />
          </button>
        </div>
        {/* 기존 검색 입력은 위쪽 row에 통합됐으므로 여기는 빈 자리 (호환 유지용 0px) */}
        <div style={{ display: "none", padding: "0 10px 8px", position: "relative" }}>
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
              fontSize: 12,
              outline: "none",
            }}
          />
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "0 6px 8px" }}>
          {loading && notes.length === 0 && (
            <div style={{ padding: 12, fontSize: 11, color: "var(--color-text-muted)" }}>
              로딩 중...
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div style={{ padding: 12, fontSize: 11, color: "var(--color-text-muted)" }}>
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
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--color-text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {n.title}
              </div>
              <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginTop: 2 }}>
                {n.tags.length > 0 ? n.tags.join(" · ") : "태그 없음"}
              </div>
              <div style={{ fontSize: 10, color: "var(--color-text-muted)" }}>
                {n.updated ? n.updated.slice(0, 10) : ""}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 우측: 편집/미리보기/그래프 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
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
                  fontSize: 12,
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
                <span style={{ fontSize: 11, color: "var(--color-accent)" }}>저장됨 ✓</span>
              )}
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
                  fontSize: 11,
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
                  fontSize: 11,
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
          {current && subTab === "edit" && (
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
                  fontSize: 14,
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
                  fontSize: 12,
                  outline: "none",
                }}
                placeholder="태그 (쉼표 구분): 회계, 출장"
              />
              <textarea
                value={editContent}
                onChange={(e) => { setEditContent(e.target.value); setDirty(true); }}
                onClick={() => window.electronAPI?.restoreFocus()}
                placeholder={"## 상황\n...\n\n## 절차\n...\n\n## 사용 자료\n- [[doc:파일명_xxx]]\n\n## 관련 업무\n- [[다른-슬러그]]"}
                style={{
                  flex: 1,
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 6,
                  color: "var(--color-text)",
                  padding: "10px 12px",
                  fontSize: 13,
                  lineHeight: 1.6,
                  fontFamily: "monospace",
                  outline: "none",
                  resize: "vertical",
                  minHeight: 250,
                }}
              />
              <RelatedDocsSection note={current} />
              <div style={{ fontSize: 10, color: "var(--color-text-muted)" }}>
                slug: <code>{current.slug}</code> · 작성 {current.created?.slice(0, 10)} · 수정 {current.updated?.slice(0, 10)}
              </div>
            </div>
          )}
          {current && subTab === "preview" && (
            <div style={{ padding: 20, overflow: "auto", flex: 1, fontSize: 13, lineHeight: 1.6 }}>
              <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>{current.title}</h2>
              <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginBottom: 14 }}>
                {current.tags.join(" · ") || "태그 없음"}
              </div>
              <RelatedDocsSection note={current} />

              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
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
                {renderWikilinks(current.content)}
              </ReactMarkdown>
            </div>
          )}
          {subTab === "graph" && (
            <Suspense fallback={<div style={{ padding: 20, fontSize: 12, color: "var(--color-text-muted)" }}>그래프 로딩 중...</div>}>
              {graph ? (
                <NotesGraph
                  data={graph}
                  onNodeClick={(slug) => {
                    setSelectedSlug(slug);
                    setSubTab("edit");
                  }}
                />
              ) : (
                <div style={{ padding: 20, fontSize: 12, color: "var(--color-text-muted)" }}>
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
        <div style={{ fontSize: 15, fontWeight: 600, color: "var(--color-text)" }}>
          노트가 비어 있습니다
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          새싹이와 채팅하면서 자료를 첨부하고<br />
          <span style={{ color: "var(--color-accent)" }}>"오늘 ⟨이 자료⟩로 ⟨이 업무⟩ 처리했어요"</span><br />
          라고 말해보세요. AI가 알아서 정리해 저장합니다.
        </div>
        <div style={{ fontSize: 11, opacity: 0.7 }}>
          채팅 입력 영역의 📎 버튼으로 자료를 첨부할 수 있습니다.
        </div>
      </div>
    );
  }
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-text-muted)", flexDirection: "column", gap: 8 }}>
      <BookOpen size={36} style={{ opacity: 0.4 }} />
      <div style={{ fontSize: 13 }}>왼쪽에서 노트를 선택해 주세요</div>
    </div>
  );
}

// `[[slug]]` 위키링크를 ReactMarkdown이 처리할 수 있도록 마크다운 링크로 변환.
// 관련 자료(첨부 파일) 섹션 — 노트와 연결된 doc_id를 다운로드 가능한 칩으로 표시
function RelatedDocsSection({ note }: { note: KnowledgeNote }): React.ReactElement | null {
  const docs = note.related_docs_info ?? [];
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
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: "var(--color-text-muted)",
          marginBottom: 6,
          display: "flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <Paperclip size={11} />
        관련 자료 · {docs.length}건
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {docs.map((d) => {
          const label = d.filename ?? d.id;
          const href = d.filename ? getDocumentDownloadUrl(d.id) : undefined;
          if (!href) {
            return (
              <span
                key={d.id}
                title={`원본 없음: ${d.id}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 8px",
                  fontSize: 11,
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
            <a
              key={d.id}
              href={href}
              download={d.filename ?? undefined}
              title={`원본 다운로드: ${label}`}
              onClick={(e) => e.stopPropagation()}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "3px 8px",
                fontSize: 11,
                borderRadius: 8,
                background: "rgba(100,140,220,0.18)",
                border: "1px solid rgba(100,140,220,0.4)",
                color: "#7aa8ff",
                textDecoration: "none",
                maxWidth: 240,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              <Download size={11} />
              {label}
            </a>
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

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useStore } from "../store";
import { speak } from "../services/tts";
import {
  Upload,
  Trash2,
  Download,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  ChevronRight,
  ChevronDown,
  Pencil,
  Check,
  X,
} from "lucide-react";
import {
  fetchDocuments,
  fetchFolders,
  uploadDocument,
  deleteDocument,
  getDocumentDownloadUrl,
  createFolder,
  renameFolder,
  deleteFolder,
} from "../services/api";
import { invalidateDocsCache } from "../services/websocket";
import type { RagDocument, RagFolder } from "../types";

const ALLOWED_EXTS = [".txt", ".md", ".pdf", ".docx", ".pptx", ".hwpx"];
const ALLOWED_MIME = [
  "text/plain",
  "text/markdown",
  "application/pdf",
  "text/x-markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
];

function isAllowed(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return ALLOWED_EXTS.includes(ext) || ALLOWED_MIME.includes(file.type);
}

type UploadStatus = "waiting" | "uploading" | "processing" | "done" | "error";

interface UploadItem {
  id: string; // 진행률/제거를 인덱스가 아닌 id로 추적 (인덱스는 항목 제거 시 밀림)
  name: string;
  progress: number; // 0-100 (전송 진행률)
  status: UploadStatus; // 전송 100% 후 서버 파싱·임베딩 동안은 processing
  error?: string;
}

// 동시 업로드 제한 — 전 파일 동시 전송 시 서버가 대형 HWPX 파싱을 6개씩 떠안는다
const UPLOAD_CONCURRENCY = 2;

// ────────────────────────────────────────────────────────────
// FolderRow
// ────────────────────────────────────────────────────────────

interface FolderRowProps {
  folder: RagFolder;
  isOpen: boolean;
  docCount: number;
  onToggle: () => void;
  onRename: (newName: string) => Promise<void>;
  onDelete: () => void;
}

function FolderRow({ folder, isOpen, docCount, onToggle, onRename, onDelete }: FolderRowProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(folder.name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      // pet 모드에서 창이 setFocusable(false) 상태면 DOM focus()만으로는
      // 키보드 입력이 전달되지 않는다 (E-23/E-27) — 창 포커스부터 복구.
      window.electronAPI?.restoreFocus();
      inputRef.current?.focus();
    }
  }, [editing]);

  // 부모에서 이름이 바뀌면 인풋 값 동기화
  useEffect(() => {
    if (!editing) setValue(folder.name);
  }, [folder.name, editing]);

  async function commitRename() {
    const trimmed = value.trim();
    if (!trimmed || trimmed === folder.name) {
      setEditing(false);
      setValue(folder.name);
      return;
    }
    try {
      await onRename(trimmed);
    } catch {
      setValue(folder.name);
    }
    setEditing(false);
  }

  function cancelRename() {
    setValue(folder.name);
    setEditing(false);
  }

  return (
    <div
      style={{ display: "flex", alignItems: "center", gap: 4, padding: "5px 4px", borderRadius: 4 }}
      className="folder-row"
    >
      {/* 펼치기/접기 버튼 */}
      <button
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flex: 1,
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--color-text)",
          fontSize: "var(--fs-13)",
          fontWeight: 600,
          textAlign: "left",
          padding: 0,
          minWidth: 0,
        }}
      >
        {isOpen
          ? <ChevronDown size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
          : <ChevronRight size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
        }
        {isOpen
          ? <FolderOpen size={15} style={{ flexShrink: 0, color: "var(--color-accent)" }} />
          : <Folder size={15} style={{ flexShrink: 0, color: "var(--color-accent)" }} />
        }
        {!editing && (
          <>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {folder.name}
            </span>
            <span style={{ marginLeft: "auto", fontWeight: 400, fontSize: "var(--fs-11)", color: "var(--color-text-muted)", flexShrink: 0 }}>
              {docCount}
            </span>
          </>
        )}
      </button>

      {/* 인라인 이름 편집 */}
      {editing ? (
        <div style={{ display: "flex", alignItems: "center", gap: 4, flex: 1, minWidth: 0 }}>
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onClick={() => window.electronAPI?.restoreFocus()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitRename();
              if (e.key === "Escape") cancelRename();
            }}
            style={{
              flex: 1,
              fontSize: "var(--fs-13)",
              background: "var(--color-panel)",
              border: "1px solid var(--color-accent)",
              borderRadius: 3,
              color: "var(--color-text)",
              padding: "2px 6px",
              minWidth: 0,
            }}
          />
          <button onClick={() => void commitRename()} style={{ background: "none", border: "none", cursor: "pointer", color: "#4caf50", padding: 2 }}>
            <Check size={13} />
          </button>
          <button onClick={cancelRename} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: 2 }}>
            <X size={13} />
          </button>
        </div>
      ) : (
        <div className="folder-actions" style={{ display: "flex", gap: 2, flexShrink: 0 }}>
          <button
            onClick={(e) => { e.stopPropagation(); setEditing(true); }}
            title="폴더 이름 변경"
            className="btn-icon"
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: 2, opacity: 0 }}
          >
            <Pencil size={12} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            title="폴더 삭제"
            className="btn-icon"
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: 2, opacity: 0 }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// DocumentsView
// ────────────────────────────────────────────────────────────

export function DocumentsView(): React.ReactElement {
  const [folders, setFolders] = useState<RagFolder[]>([]);
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  // 업로드 대상 폴더 (명시적 선택)
  const [targetFolderId, setTargetFolderId] = useState<string>("");
  // 커스텀 드롭다운 — native <select>는 pet 모드 투명창에서 두 번 클릭 필요 문제 발생
  const [folderDropOpen, setFolderDropOpen] = useState(false);
  const dropRef = useRef<HTMLDivElement | null>(null);

  // 새 폴더 추가
  const [addingFolder, setAddingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [isCreating, setIsCreating] = useState(false);  // 이중 제출 방지

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const newFolderInputRef = useRef<HTMLInputElement | null>(null);

  // 커스텀 드롭다운 외부 클릭 시 닫기
  useEffect(() => {
    if (!folderDropOpen) return;
    function onOutside(e: MouseEvent): void {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setFolderDropOpen(false);
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [folderDropOpen]);

  // dragenter/leave 중첩 해결용 카운터
  const dragCounterRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [foldersData, docsData] = await Promise.all([fetchFolders(), fetchDocuments()]);
      setFolders(foldersData);
      setDocs(docsData);
    } catch {
      // API 미연결 시 무시
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (addingFolder) {
      // pet 모드 setFocusable(false) 대응 — 창 포커스 복구 후 input focus (E-23/E-27)
      window.electronAPI?.restoreFocus();
      newFolderInputRef.current?.focus();
    }
  }, [addingFolder]);

  // ── 파일 업로드 ──────────────────────────────────────────

  async function handleFiles(files: FileList | File[]): Promise<void> {
    const arr = Array.from(files).filter(isAllowed);
    if (arr.length === 0) return;

    const { setEmotion, setIsUploading } = useStore.getState();
    const folderId = targetFolderId || null;
    const items = arr.map((f) => ({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file: f,
    }));
    setUploads((prev) => [
      ...prev,
      ...items.map(({ id, file }) => ({
        id,
        name: file.name,
        progress: 0,
        status: "waiting" as UploadStatus,
      })),
    ]);
    setIsUploading(true);
    setEmotion("study");

    const patch = (id: string, p: Partial<UploadItem>): void =>
      setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...p } : u)));

    let successCount = 0;
    try {
      const queue = [...items];
      async function worker(): Promise<void> {
        for (;;) {
          const item = queue.shift();
          if (!item) return;
          const { id, file } = item;
          patch(id, { status: "uploading" });
          try {
            const doc = await uploadDocument(file, folderId, (pct) => {
              // 전송 100% 이후에는 서버가 파싱·임베딩 중 (응답 대기)
              patch(id, { progress: pct, status: pct >= 100 ? "processing" : "uploading" });
            });
            setDocs((prev) => [...prev, doc]);
            // 성공 행도 목록에 남긴다 — 파일별 성공/실패를 확인할 수 있어야 한다 (E-39)
            patch(id, { progress: 100, status: "done" });
            invalidateDocsCache();
            successCount++;
          } catch (err) {
            const msg = err instanceof Error ? err.message : "업로드 실패";
            patch(id, { status: "error", error: msg });
          }
        }
      }
      await Promise.all(
        Array.from({ length: Math.min(UPLOAD_CONCURRENCY, items.length) }, () => worker())
      );
    } finally {
      setIsUploading(false);
      setEmotion("neutral");
      // 파일 피커 종료 후 macOS pet 모드에서 키보드 포커스 복구
      window.electronAPI?.restoreFocus();
    }

    if (successCount > 0) {
      void speak("문서등록이 완료되었습니다.");
    }
  }

  // ── 드래그 앤 드롭 (drag counter로 중첩 처리) ────────────

  function onDragEnter(e: React.DragEvent): void {
    e.preventDefault();
    dragCounterRef.current += 1;
    setDragging(true);
  }

  function onDragOver(e: React.DragEvent): void {
    e.preventDefault(); // 드롭 허용을 위해 필수
  }

  function onDragLeave(): void {
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) setDragging(false);
  }

  function onDrop(e: React.DragEvent): void {
    e.preventDefault();
    dragCounterRef.current = 0;
    setDragging(false);
    void handleFiles(e.dataTransfer.files);
  }

  function onFileInputChange(e: React.ChangeEvent<HTMLInputElement>): void {
    // 파일 피커가 닫힌 직후 — macOS pet 모드에서 key window 포커스를 즉시 복구
    window.electronAPI?.restoreFocus();
    if (e.target.files) void handleFiles(e.target.files);
    e.target.value = "";
  }

  // ── 폴더 관리 ────────────────────────────────────────────

  async function handleCreateFolder(): Promise<void> {
    if (isCreating) return;
    const name = newFolderName.trim();
    if (!name) { setAddingFolder(false); return; }
    // 클라이언트 측 사전 중복 검사 — 백엔드 409보다 더 빠르고 친절한 메시지
    if (folders.some((f) => f.name === name)) {
      alert(`이미 '${name}' 이름의 폴더가 있습니다. 다른 이름을 입력하세요.`);
      return;
    }
    setIsCreating(true);
    try {
      const folder = await createFolder(name);
      setFolders((prev) => [...prev, folder]);
      setCollapsed((prev) => ({ ...prev, [folder.folder_id]: false }));
      setNewFolderName("");
      setAddingFolder(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 백엔드 응답을 그대로 alert — 사용자가 원인을 파악할 수 있게
      alert(`폴더 생성 실패\n\n원인: ${msg}\n\n백엔드가 실행 중인지, 폴더 이름이 중복되지 않는지 확인하세요.`);
    } finally {
      setIsCreating(false);
    }
  }

  async function handleRenameFolder(folderId: string, newName: string): Promise<void> {
    const updated = await renameFolder(folderId, newName);
    setFolders((prev) => prev.map((f) => (f.folder_id === folderId ? updated : f)));
  }

  async function handleDeleteFolder(folderId: string): Promise<void> {
    const folder = folders.find((f) => f.folder_id === folderId);
    if (!folder) return;
    const docCount = docs.filter((d) => d.folder_id === folderId).length;

    if (docCount === 0) {
      if (!confirm(`'${folder.name}' 폴더를 삭제하시겠습니까?`)) return;
    } else {
      // 1차: 경고
      const ok = confirm(
        `⚠️ '${folder.name}' 폴더 안에 문서 ${docCount}개가 있습니다.\n\n` +
          `폴더와 문서 ${docCount}개의 임베딩·원본 파일이 모두 영구 삭제됩니다.\n` +
          `이 작업은 되돌릴 수 없습니다.\n\n` +
          `정말 진행하시겠습니까?`
      );
      if (!ok) return;
      // 2차: 최종 확인 — Electron은 window.prompt()를 지원하지 않고 예외를 던지므로
      // (E-34: 확인 클릭 후 아무 일도 안 일어나는 버그) confirm 2단계로 대체.
      const finalOk = confirm(
        `마지막 확인입니다.\n\n'${folder.name}' 폴더와 문서 ${docCount}개를 영구 삭제합니다.`
      );
      if (!finalOk) return;
    }

    try {
      await deleteFolder(folderId, true);
      setFolders((prev) => prev.filter((f) => f.folder_id !== folderId));
      setDocs((prev) => prev.filter((d) => d.folder_id !== folderId));
      if (targetFolderId === folderId) setTargetFolderId("");
      invalidateDocsCache();
    } catch (err) {
      alert(err instanceof Error ? err.message : "폴더 삭제 실패");
    }
  }

  async function handleDeleteDoc(id: string): Promise<void> {
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
      invalidateDocsCache();
    } catch (err) {
      alert(err instanceof Error ? err.message : "문서 삭제 실패");
    }
  }

  function toggleFolder(folderId: string): void {
    setCollapsed((prev) => ({ ...prev, [folderId]: !prev[folderId] }));
  }

  // folder_id가 없거나 알 수 없는 폴더(orphan)인 문서도 미분류로 표시
  const knownFolderIds = new Set(folders.map((f) => f.folder_id));
  const unclassifiedDocs = docs.filter((d) => !d.folder_id || !knownFolderIds.has(d.folder_id));

  // 업로드 대상 폴더 이름 (표시용)
  const targetFolderName = folders.find((f) => f.folder_id === targetFolderId)?.name ?? "미분류";

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>

      {/* ── 폴더 선택 드롭다운 + 업로드 ────────────────────── */}
      <div style={{ padding: "10px 12px 0", flexShrink: 0 }}>
        {/* 업로드 대상 폴더 선택 — 커스텀 드롭다운 (native select는 pet 모드에서 두 번 클릭 필요 문제) */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", flexShrink: 0 }}>
            업로드 위치
          </span>
          <div ref={dropRef} style={{ flex: 1, position: "relative" }}>
            <button
              type="button"
              onClick={() => setFolderDropOpen((o) => !o)}
              onKeyDown={(e: ReactKeyboardEvent<HTMLButtonElement>) => {
                if (e.key === "Escape") setFolderDropOpen(false);
              }}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                fontSize: "var(--fs-12)",
                background: "var(--color-sidebar, #1a1a1a)",
                border: "1px solid var(--color-border)",
                borderRadius: 4,
                color: "var(--color-text)",
                padding: "3px 6px",
                cursor: "pointer",
                textAlign: "left",
              }}
            >
              <span>{targetFolderName}</span>
              <ChevronDown size={11} style={{ flexShrink: 0, marginLeft: 4, opacity: 0.6 }} />
            </button>
            {folderDropOpen && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  right: 0,
                  zIndex: 1000,
                  background: "var(--color-sidebar, #1a1a1a)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 4,
                  marginTop: 2,
                  overflow: "hidden",
                  boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
                }}
              >
                {[{ id: "", name: "미분류" }, ...folders.map((f) => ({ id: f.folder_id, name: f.name }))].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onMouseDown={(e) => {
                      e.stopPropagation();
                      setTargetFolderId(opt.id);
                      setFolderDropOpen(false);
                    }}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      padding: "5px 8px",
                      fontSize: "var(--fs-12)",
                      background: targetFolderId === opt.id ? "var(--color-accent, #c96442)" : "transparent",
                      color: targetFolderId === opt.id ? "#fff" : "var(--color-text)",
                      border: "none",
                      cursor: "pointer",
                    }}
                  >
                    {opt.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 드롭존 */}
        <div
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
          style={{
            border: `2px dashed ${dragging ? "var(--color-accent)" : "var(--color-border)"}`,
            borderRadius: 8,
            padding: "12px 16px",
            textAlign: "center",
            cursor: "pointer",
            background: dragging ? "rgba(201,100,66,0.06)" : "transparent",
            transition: "border-color 0.15s, background 0.15s",
          }}
        >
          <Upload size={18} style={{ marginBottom: 4, color: "var(--color-text-muted)" }} />
          <p style={{ fontWeight: 600, fontSize: "var(--fs-12)", marginBottom: 1 }}>
            클릭하여 파일 선택, 또는 파일 끌어다 놓기
            <span style={{ color: "var(--color-accent)", marginLeft: 4 }}>
              → {targetFolderName}
            </span>
          </p>
          <p style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
            .txt · .md · .pdf · .docx · .pptx · .hwpx
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_EXTS.join(",")}
            multiple
            style={{ display: "none" }}
            onChange={onFileInputChange}
          />
        </div>
      </div>

      {/* ── 업로드 진행 목록 ────────────────────────────────── */}
      {uploads.length > 0 && (() => {
        const total = uploads.length;
        const doneCount = uploads.filter((u) => u.status === "done").length;
        const errorCount = uploads.filter((u) => u.status === "error").length;
        const finished = doneCount + errorCount;
        const allFinished = finished === total;
        const overallPct = Math.round((finished / total) * 100);

        const statusLabel = (u: UploadItem): { text: string; color: string } => {
          switch (u.status) {
            case "waiting": return { text: "대기", color: "var(--color-text-muted)" };
            case "uploading": return { text: `${u.progress}%`, color: "var(--color-text-muted)" };
            case "processing": return { text: "분석·임베딩 중…", color: "var(--color-accent)" };
            case "done": return { text: "완료 ✓", color: "#4caf50" };
            case "error": return { text: u.error ?? "오류", color: "#e05050" };
          }
        };
        const barColor = (u: UploadItem): string =>
          u.status === "error" ? "#e05050" : u.status === "done" ? "#4caf50" : "var(--color-accent)";
        const barWidth = (u: UploadItem): number =>
          u.status === "error" || u.status === "done" || u.status === "processing" ? 100 : u.progress;

        return (
          <div style={{ padding: "6px 12px 0", flexShrink: 0 }}>
            {/* 전체 진행 헤더 */}
            <div style={{ background: "var(--color-sidebar)", border: "1px solid var(--color-border)", borderRadius: 6, padding: "6px 10px", marginBottom: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, fontSize: "var(--fs-11)" }}>
                <span style={{ fontWeight: 600 }}>
                  {allFinished ? "업로드 완료" : "업로드 중"} — {finished}/{total}
                  {errorCount > 0 && <span style={{ color: "#e05050" }}> (실패 {errorCount})</span>}
                </span>
                {allFinished && (
                  <button
                    onClick={() => setUploads([])}
                    style={{ background: "none", border: "1px solid var(--color-border)", borderRadius: 4, cursor: "pointer", color: "var(--color-text-muted)", fontSize: "var(--fs-11)", padding: "1px 6px" }}
                  >
                    목록 지우기
                  </button>
                )}
              </div>
              <div style={{ height: 4, background: "var(--color-border)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${overallPct}%`, height: "100%", background: errorCount > 0 ? "#e0a050" : "var(--color-accent)", transition: "width 0.2s" }} />
              </div>
            </div>

            {/* 파일별 목록 — 스크롤 */}
            <div style={{ maxHeight: 180, overflowY: "auto" }}>
              {uploads.map((u) => {
                const s = statusLabel(u);
                return (
                  <div key={u.id} style={{ background: "var(--color-sidebar)", border: "1px solid var(--color-border)", borderRadius: 6, padding: "6px 10px", marginBottom: 3 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3, fontSize: "var(--fs-11)" }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</span>
                      <span style={{ color: s.color, flexShrink: 0, marginLeft: 8 }}>{s.text}</span>
                    </div>
                    <div style={{ height: 2, background: "var(--color-border)", borderRadius: 1, overflow: "hidden" }}>
                      <div style={{ width: `${barWidth(u)}%`, height: "100%", background: barColor(u), transition: "width 0.2s" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* ── 툴바: 폴더 추가 ─────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", padding: "8px 12px 4px", gap: 6, flexShrink: 0 }}>
        <span style={{ fontSize: "var(--fs-11)", fontWeight: 600, color: "var(--color-text-muted)", flex: 1 }}>
          문서 탐색기
        </span>
        <button
          onClick={() => { setAddingFolder(true); setNewFolderName(""); }}
          title="새 폴더 만들기"
          style={{ display: "flex", alignItems: "center", gap: 4, background: "none", border: "1px solid var(--color-border)", borderRadius: 4, cursor: "pointer", color: "var(--color-text-muted)", fontSize: "var(--fs-11)", padding: "3px 7px" }}
        >
          <FolderPlus size={13} />
          새 폴더
        </button>
      </div>

      {/* ── 새 폴더 인라인 입력 ─────────────────────────────── */}
      {addingFolder && (
        <div style={{ padding: "0 12px 6px", flexShrink: 0, display: "flex", gap: 4 }}>
          <input
            ref={newFolderInputRef}
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            placeholder="폴더 이름"
            disabled={isCreating}
            onClick={() => window.electronAPI?.restoreFocus()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreateFolder();
              if (e.key === "Escape") { setAddingFolder(false); setNewFolderName(""); }
            }}
            style={{ flex: 1, fontSize: "var(--fs-12)", background: "var(--color-panel)", border: "1px solid var(--color-accent)", borderRadius: 4, color: "var(--color-text)", padding: "4px 8px", opacity: isCreating ? 0.5 : 1 }}
          />
          <button
            onClick={() => void handleCreateFolder()}
            disabled={isCreating}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#4caf50", padding: "0 4px" }}
          >
            <Check size={14} />
          </button>
          <button
            onClick={() => { setAddingFolder(false); setNewFolderName(""); }}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "0 4px" }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── 폴더 트리 ───────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 12px 12px" }}>
        {loading && (
          <div style={{ color: "var(--color-text-muted)", fontSize: "var(--fs-12)", padding: "10px 4px" }}>
            불러오는 중...
          </div>
        )}
        {!loading && folders.length === 0 && docs.length === 0 && uploads.length === 0 && (
          <div style={{ color: "var(--color-text-muted)", fontSize: "var(--fs-12)", padding: "10px 4px" }}>
            새 폴더를 만들고 문서를 업로드하세요.
          </div>
        )}

        {/* 사용자 폴더 */}
        {folders.map((folder) => {
          const folderDocs = docs.filter((d) => d.folder_id === folder.folder_id);
          const isOpen = !collapsed[folder.folder_id];
          return (
            <div key={folder.folder_id} style={{ marginBottom: 1 }}>
              <FolderRow
                folder={folder}
                isOpen={isOpen}
                docCount={folderDocs.length}
                onToggle={() => toggleFolder(folder.folder_id)}
                onRename={(name) => handleRenameFolder(folder.folder_id, name)}
                onDelete={() => void handleDeleteFolder(folder.folder_id)}
              />
              {isOpen && (
                <div style={{ paddingLeft: 24 }}>
                  {folderDocs.length === 0 ? (
                    <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)", padding: "4px 6px", fontStyle: "italic" }}>
                      문서 없음
                    </div>
                  ) : (
                    folderDocs.map((doc) => (
                      <DocRow key={doc.id} doc={doc} onDelete={handleDeleteDoc} />
                    ))
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* 미분류 */}
        {unclassifiedDocs.length > 0 && (
          <div style={{ marginBottom: 1 }}>
            <button
              onClick={() => toggleFolder("__unclassified__")}
              style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", background: "none", border: "none", cursor: "pointer", color: "var(--color-text)", padding: "5px 4px", borderRadius: 4, fontSize: "var(--fs-13)", fontWeight: 600, textAlign: "left" }}
            >
              {!collapsed["__unclassified__"]
                ? <ChevronDown size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
                : <ChevronRight size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
              }
              {!collapsed["__unclassified__"]
                ? <FolderOpen size={15} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
                : <Folder size={15} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
              }
              <span>미분류</span>
              <span style={{ marginLeft: "auto", fontWeight: 400, fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
                {unclassifiedDocs.length}
              </span>
            </button>
            {!collapsed["__unclassified__"] && (
              <div style={{ paddingLeft: 24 }}>
                {unclassifiedDocs.map((doc) => (
                  <DocRow key={doc.id} doc={doc} onDelete={handleDeleteDoc} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// DocRow
// ────────────────────────────────────────────────────────────

function DocRow({ doc, onDelete }: { doc: RagDocument; onDelete: (id: string) => Promise<void> }) {
  return (
    <div
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 6px", borderRadius: 4, fontSize: "var(--fs-12)" }}
      className="doc-row"
    >
      <FileText size={13} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {doc.filename}
        </div>
        <div style={{ fontSize: "var(--fs-11)", color: "var(--color-text-muted)" }}>
          청크 {doc.chunk_count}개
          {doc.uploaded_at ? ` · ${new Date(doc.uploaded_at).toLocaleDateString("ko-KR")}` : ""}
        </div>
      </div>
      <a
        href={getDocumentDownloadUrl(doc.id)}
        download={doc.filename}
        className="btn-download"
        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", flexShrink: 0, padding: 2, display: "flex", alignItems: "center", opacity: 0 }}
        title="원본 다운로드"
        onClick={(e) => e.stopPropagation()}
      >
        <Download size={12} />
      </a>
      <button
        onClick={() => void onDelete(doc.id)}
        className="btn-delete"
        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", flexShrink: 0, padding: 2, opacity: 0 }}
        title="RAG에서 삭제"
      >
        <Trash2 size={12} />
      </button>
    </div>
  );
}

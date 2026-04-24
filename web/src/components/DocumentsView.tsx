import { useCallback, useEffect, useRef, useState } from "react";
import {
  Upload,
  Trash2,
  FileText,
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import {
  fetchDocuments,
  uploadDocument,
  deleteDocument,
} from "../services/api";
import type { RagDocument } from "../types";

const ALLOWED_TYPES = [".txt", ".md", ".pdf"];
const ALLOWED_MIME = [
  "text/plain",
  "text/markdown",
  "application/pdf",
  "text/x-markdown",
];

function isAllowed(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return ALLOWED_TYPES.includes(ext) || ALLOWED_MIME.includes(file.type);
}

// ────────────────────────────────────────────────────────────
// 카테고리 자동 감지
// ────────────────────────────────────────────────────────────

const CATEGORIES = ["규정", "매뉴얼", "회의록", "공지", "기타"] as const;
type Category = (typeof CATEGORIES)[number];

function detectCategory(filename: string): Category {
  const lower = filename.toLowerCase();
  if (/규정|내규|규칙|지침/.test(lower)) return "규정";
  if (/매뉴얼|안내|가이드|사용법|manual/.test(lower)) return "매뉴얼";
  if (/회의|결과보고|보고서|minutes/.test(lower)) return "회의록";
  if (/공지|공문|통보|notice/.test(lower)) return "공지";
  return "기타";
}

function groupByCategory(
  docs: RagDocument[]
): Record<Category, RagDocument[]> {
  const groups: Record<Category, RagDocument[]> = {
    규정: [],
    매뉴얼: [],
    회의록: [],
    공지: [],
    기타: [],
  };
  for (const doc of docs) {
    groups[detectCategory(doc.filename)].push(doc);
  }
  return groups;
}

// ────────────────────────────────────────────────────────────
// 업로드 항목
// ────────────────────────────────────────────────────────────

interface UploadItem {
  name: string;
  progress: number; // 0-100, -1=error
}

// ────────────────────────────────────────────────────────────
// DocumentsView
// ────────────────────────────────────────────────────────────

export function DocumentsView(): React.ReactElement {
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchDocuments();
      setDocs(data);
    } catch {
      // API 미연결
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleFiles(files: FileList | File[]): Promise<void> {
    const arr = Array.from(files).filter(isAllowed);
    if (arr.length === 0) return;

    const startIdx = uploads.length;
    const newUploads: UploadItem[] = arr.map((f) => ({
      name: f.name,
      progress: 0,
    }));
    setUploads((prev) => [...prev, ...newUploads]);

    await Promise.all(
      arr.map(async (file, i) => {
        const idx = startIdx + i;
        try {
          const doc = await uploadDocument(file, (pct) => {
            setUploads((prev) =>
              prev.map((u, j) => (j === idx ? { ...u, progress: pct } : u))
            );
          });
          setDocs((prev) => [...prev, doc]);
          setUploads((prev) => prev.filter((_, j) => j !== idx));
        } catch {
          setUploads((prev) =>
            prev.map((u, j) => (j === idx ? { ...u, progress: -1 } : u))
          );
        }
      })
    );
  }

  async function handleDelete(id: string): Promise<void> {
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch {
      // ignore
    }
  }

  function toggleCategory(cat: string): void {
    setCollapsed((prev) => ({ ...prev, [cat]: !prev[cat] }));
  }

  function onDragOver(e: React.DragEvent): void {
    e.preventDefault();
    setDragging(true);
  }

  function onDragLeave(): void {
    setDragging(false);
  }

  function onDrop(e: React.DragEvent): void {
    e.preventDefault();
    setDragging(false);
    void handleFiles(e.dataTransfer.files);
  }

  function onFileInputChange(e: React.ChangeEvent<HTMLInputElement>): void {
    if (e.target.files) void handleFiles(e.target.files);
    e.target.value = "";
  }

  const grouped = groupByCategory(docs);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* 드롭존 */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? "var(--color-accent)" : "var(--color-border)"}`,
          borderRadius: 8,
          padding: "18px 16px",
          textAlign: "center",
          cursor: "pointer",
          background: dragging ? "rgba(201,100,66,0.06)" : "transparent",
          transition: "border-color 0.15s, background 0.15s",
          margin: "12px 12px 0",
          flexShrink: 0,
        }}
      >
        <Upload
          size={22}
          style={{ marginBottom: 6, color: "var(--color-text-muted)" }}
        />
        <p style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>
          파일 드래그 또는 클릭해서 업로드
        </p>
        <p style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
          .txt · .md · .pdf
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept={ALLOWED_TYPES.join(",")}
          multiple
          style={{ display: "none" }}
          onChange={onFileInputChange}
        />
      </div>

      {/* 업로드 중 목록 */}
      {uploads.length > 0 && (
        <div style={{ padding: "8px 12px 0", flexShrink: 0 }}>
          {uploads.map((u, i) => (
            <div
              key={i}
              style={{
                background: "var(--color-sidebar)",
                border: "1px solid var(--color-border)",
                borderRadius: 6,
                padding: "8px 10px",
                marginBottom: 4,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 4,
                  fontSize: 12,
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {u.name}
                </span>
                <span
                  style={{
                    color:
                      u.progress === -1 ? "#e05050" : "var(--color-text-muted)",
                    flexShrink: 0,
                    marginLeft: 8,
                  }}
                >
                  {u.progress === -1 ? "오류" : `${u.progress}%`}
                </span>
              </div>
              <div
                style={{
                  height: 3,
                  background: "var(--color-border)",
                  borderRadius: 2,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${u.progress === -1 ? 100 : u.progress}%`,
                    height: "100%",
                    background:
                      u.progress === -1 ? "#e05050" : "var(--color-accent)",
                    transition: "width 0.2s",
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 폴더 트리 */}
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px 12px" }}>
        {loading && (
          <div
            style={{
              color: "var(--color-text-muted)",
              fontSize: 12,
              padding: "12px 4px",
            }}
          >
            불러오는 중...
          </div>
        )}
        {!loading && docs.length === 0 && uploads.length === 0 && (
          <div
            style={{
              color: "var(--color-text-muted)",
              fontSize: 12,
              padding: "12px 4px",
            }}
          >
            업로드된 문서가 없습니다.
          </div>
        )}

        {CATEGORIES.map((cat) => {
          const catDocs = grouped[cat];
          if (catDocs.length === 0) return null;
          const isOpen = !collapsed[cat];

          return (
            <div key={cat} style={{ marginBottom: 2 }}>
              {/* 폴더 헤더 */}
              <button
                onClick={() => toggleCategory(cat)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  width: "100%",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--color-text)",
                  padding: "6px 4px",
                  borderRadius: 4,
                  fontSize: 13,
                  fontWeight: 600,
                  textAlign: "left",
                }}
              >
                {isOpen ? (
                  <ChevronDown size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
                ) : (
                  <ChevronRight size={14} style={{ flexShrink: 0, color: "var(--color-text-muted)" }} />
                )}
                {isOpen ? (
                  <FolderOpen size={15} style={{ flexShrink: 0, color: "var(--color-accent)" }} />
                ) : (
                  <Folder size={15} style={{ flexShrink: 0, color: "var(--color-accent)" }} />
                )}
                <span>{cat}</span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontWeight: 400,
                    fontSize: 11,
                    color: "var(--color-text-muted)",
                  }}
                >
                  {catDocs.length}
                </span>
              </button>

              {/* 파일 목록 */}
              {isOpen && (
                <div style={{ paddingLeft: 24 }}>
                  {catDocs.map((doc) => (
                    <div
                      key={doc.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "5px 6px",
                        borderRadius: 4,
                        fontSize: 12,
                      }}
                      className="doc-row"
                    >
                      <FileText
                        size={13}
                        style={{
                          flexShrink: 0,
                          color: "var(--color-text-muted)",
                        }}
                      />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {doc.filename}
                        </div>
                        <div
                          style={{
                            fontSize: 10,
                            color: "var(--color-text-muted)",
                          }}
                        >
                          청크 {doc.chunk_count}개
                          {doc.uploaded_at
                            ? ` · ${new Date(doc.uploaded_at).toLocaleDateString("ko-KR")}`
                            : ""}
                        </div>
                      </div>
                      <button
                        onClick={() => void handleDelete(doc.id)}
                        className="btn-delete"
                        style={{
                          background: "none",
                          border: "none",
                          cursor: "pointer",
                          color: "var(--color-text-muted)",
                          flexShrink: 0,
                          padding: 2,
                          opacity: 0,
                        }}
                        title="RAG에서 삭제"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

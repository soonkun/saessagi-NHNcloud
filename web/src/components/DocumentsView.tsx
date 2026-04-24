import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, Trash2, FileText } from "lucide-react";
import {
  fetchDocuments,
  uploadDocument,
  deleteDocument,
} from "../services/api";
import type { RagDocument } from "../types";

const ALLOWED_TYPES = [".txt", ".md", ".pdf"];
const ALLOWED_MIME = ["text/plain", "text/markdown", "application/pdf", "text/x-markdown"];

function isAllowed(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return ALLOWED_TYPES.includes(ext) || ALLOWED_MIME.includes(file.type);
}

interface UploadItem {
  name: string;
  progress: number; // 0~100, -1 = error
}

export function DocumentsView(): React.ReactElement {
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
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

    const newUploads: UploadItem[] = arr.map((f) => ({
      name: f.name,
      progress: 0,
    }));
    setUploads((prev) => [...prev, ...newUploads]);

    await Promise.all(
      arr.map(async (file, i) => {
        const idx = uploads.length + i;
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
            prev.map((u, j) =>
              j === idx ? { ...u, progress: -1 } : u
            )
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

  return (
    <div style={{ padding: 24, height: "100%", overflowY: "auto" }}>
      <h2 style={{ fontWeight: 700, fontSize: 18, marginBottom: 20 }}>
        문서 관리
      </h2>

      {/* 드롭존 */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? "var(--color-accent)" : "var(--color-border)"}`,
          borderRadius: 12,
          padding: "40px 24px",
          textAlign: "center",
          cursor: "pointer",
          background: dragging ? "rgba(201,100,66,0.06)" : "transparent",
          transition: "border-color 0.15s, background 0.15s",
          marginBottom: 24,
        }}
      >
        <Upload
          size={32}
          style={{ marginBottom: 12, color: "var(--color-text-muted)" }}
        />
        <p style={{ fontWeight: 600, marginBottom: 4 }}>
          파일을 드래그하거나 클릭해서 업로드
        </p>
        <p style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
          지원 형식: .txt, .md, .pdf
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
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--color-text-muted)" }}>
            업로드 중
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {uploads.map((u, i) => (
              <div
                key={i}
                style={{
                  background: "var(--color-sidebar)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  padding: "10px 12px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginBottom: 6,
                    fontSize: 13,
                  }}
                >
                  <span>{u.name}</span>
                  <span style={{ color: u.progress === -1 ? "#e05050" : "var(--color-text-muted)" }}>
                    {u.progress === -1 ? "오류" : `${u.progress}%`}
                  </span>
                </div>
                <div
                  style={{
                    height: 4,
                    background: "var(--color-border)",
                    borderRadius: 2,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${u.progress === -1 ? 100 : u.progress}%`,
                      height: "100%",
                      background: u.progress === -1 ? "#e05050" : "var(--color-accent)",
                      transition: "width 0.2s",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 업로드된 문서 목록 */}
      <div>
        <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--color-text-muted)" }}>
          {loading ? "불러오는 중..." : `문서 ${docs.length}개`}
        </h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {docs.map((doc) => (
            <div
              key={doc.id}
              style={{
                background: "var(--color-sidebar)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                padding: "10px 12px",
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <FileText size={16} style={{ color: "var(--color-text-muted)", flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {doc.filename}
                </div>
                <div style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
                  청크 {doc.chunk_count}개
                  {doc.uploaded_at
                    ? ` · ${new Date(doc.uploaded_at).toLocaleDateString("ko-KR")}`
                    : ""}
                </div>
              </div>
              <button
                onClick={() => void handleDelete(doc.id)}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--color-text-muted)",
                  flexShrink: 0,
                  padding: 4,
                }}
                title="삭제"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {!loading && docs.length === 0 && uploads.length === 0 && (
            <div style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
              업로드된 문서가 없습니다.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

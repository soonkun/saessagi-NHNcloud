import type {
  CalendarEvent,
  RagDocument,
  RagFolder,
  KnowledgeNote,
  KnowledgeNoteMeta,
  KnowledgeGraphData,
} from "../types";

// Electron은 file:// 로드 → 상대경로가 백엔드로 라우팅되지 않으므로 절대 URL 필요
export const API_BASE: string =
  (window as { electronAPI?: { isElectron?: boolean } }).electronAPI?.isElectron
    ? "http://127.0.0.1:12393"
    : "";

// ────────────────────────────────────────────────────────────
// fetch 래퍼 — 사내 IP / localhost 전용
// ────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// 백엔드 RAG 응답 원본 형태 (doc_id 필드)
interface RagDocumentRaw {
  doc_id: string;
  filename: string;
  chunk_count: number;
  folder_id?: string | null;
}

function mapRagDoc(raw: RagDocumentRaw): RagDocument {
  return {
    id: raw.doc_id,
    filename: raw.filename,
    chunk_count: raw.chunk_count,
    folder_id: raw.folder_id ?? null,
  };
}

// ────────────────────────────────────────────────────────────
// Calendar API
// ────────────────────────────────────────────────────────────

export async function fetchCalendarEvents(): Promise<CalendarEvent[]> {
  return apiFetch<CalendarEvent[]>("/api/calendar/events");
}

export async function createCalendarEvent(
  event: Omit<CalendarEvent, "id">
): Promise<CalendarEvent> {
  return apiFetch<CalendarEvent>("/api/calendar/events", {
    method: "POST",
    body: JSON.stringify(event),
  });
}

export async function deleteCalendarEvent(id: number): Promise<void> {
  await apiFetch<unknown>(`/api/calendar/events/${id}`, { method: "DELETE" });
}

// ────────────────────────────────────────────────────────────
// RAG Folders API
// ────────────────────────────────────────────────────────────

export async function fetchFolders(): Promise<RagFolder[]> {
  return apiFetch<RagFolder[]>("/api/rag/folders");
}

export async function createFolder(name: string): Promise<RagFolder> {
  return apiFetch<RagFolder>("/api/rag/folders", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function renameFolder(folderId: string, name: string): Promise<RagFolder> {
  return apiFetch<RagFolder>(`/api/rag/folders/${encodeURIComponent(folderId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deleteFolder(
  folderId: string,
  deleteDocs: boolean = true
): Promise<void> {
  await apiFetch<unknown>(
    `/api/rag/folders/${encodeURIComponent(folderId)}?delete_docs=${deleteDocs}`,
    { method: "DELETE" }
  );
}

// ────────────────────────────────────────────────────────────
// RAG Documents API
// ────────────────────────────────────────────────────────────

export async function fetchDocuments(): Promise<RagDocument[]> {
  const raw = await apiFetch<RagDocumentRaw[]>("/api/rag/documents");
  return raw.map(mapRagDoc);
}

export async function uploadDocument(
  file: File,
  folderId?: string | null,
  onProgress?: (pct: number) => void,
  options?: { folderName?: string }
): Promise<RagDocument> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API_BASE + "/api/rag/documents");

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      });
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const raw = JSON.parse(xhr.responseText) as RagDocumentRaw;
          resolve(mapRagDoc(raw));
        } catch {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        let detail = `Upload failed: ${xhr.status}`;
        try {
          const body = JSON.parse(xhr.responseText) as { detail?: string };
          if (body.detail) detail = body.detail;
        } catch { /* ignore */ }
        reject(new Error(detail));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload"));

    const formData = new FormData();
    formData.append("file", file);
    if (folderId) formData.append("folder_id", folderId);
    if (options?.folderName) formData.append("folder_name", options.folderName);
    xhr.send(formData);
  });
}

export async function deleteDocument(id: string): Promise<void> {
  // doc_id는 파일명 기반이라 #·&·공백 등이 들어올 수 있음 — 인코딩 필수
  // (#이 인코딩 없이 들어가면 URL fragment로 해석돼 doc_id가 잘린 채 전송됨)
  await apiFetch<unknown>(`/api/rag/documents/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function getDocumentDownloadUrl(docId: string): string {
  return `${API_BASE}/api/rag/documents/${encodeURIComponent(docId)}/download`;
}

/**
 * 원본 문서를 다운로드 위치를 묻지 않고 기본 앱으로 바로 연다.
 * Electron에서는 임시 폴더로 받아 shell.openPath로 열고,
 * 비-Electron(웹) 환경에서는 새 탭으로 연다(폴백).
 */
export function openDocument(docId: string, filename: string): void {
  const url = getDocumentDownloadUrl(docId);
  const shellApi = window.shell;
  if (shellApi?.openDocument) {
    void shellApi
      .openDocument(url, filename)
      .then((err) => {
        // shell.openPath는 실패 시 에러 문자열을, 성공 시 "" 를 반환
        if (err) window.open(url, "_blank");
      })
      .catch(() => window.open(url, "_blank"));
  } else {
    window.open(url, "_blank");
  }
}

// ────────────────────────────────────────────────────────────
// Knowledge Notes API (M_15)
// ────────────────────────────────────────────────────────────

export async function fetchNotes(): Promise<KnowledgeNoteMeta[]> {
  return apiFetch<KnowledgeNoteMeta[]>("/api/knowledge/notes");
}

export async function fetchNote(slug: string): Promise<KnowledgeNote> {
  return apiFetch<KnowledgeNote>(`/api/knowledge/notes/${encodeURIComponent(slug)}`);
}

export async function createNote(body: {
  title: string;
  content?: string;
  tags?: string[];
  related_docs?: string[];
}): Promise<KnowledgeNote> {
  return apiFetch<KnowledgeNote>("/api/knowledge/notes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateNote(
  slug: string,
  body: {
    title?: string;
    content?: string;
    tags?: string[];
    related_docs?: string[];
  }
): Promise<KnowledgeNote> {
  return apiFetch<KnowledgeNote>(
    `/api/knowledge/notes/${encodeURIComponent(slug)}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
}

export async function deleteNote(slug: string): Promise<void> {
  await apiFetch<unknown>(
    `/api/knowledge/notes/${encodeURIComponent(slug)}`,
    { method: "DELETE" }
  );
}

export async function fetchKnowledgeGraph(): Promise<KnowledgeGraphData> {
  return apiFetch<KnowledgeGraphData>("/api/knowledge/graph");
}

// ────────────────────────────────────────────────────────────
// Meeting Minutes API
// ────────────────────────────────────────────────────────────

export interface MeetingMinutesResult {
  file_id: string;
  download_url: string;
  expires_at: string;
}

interface GenerateParams {
  transcript?: string;
  audio_file?: File;
  pages: 1 | 2;
}

export interface MeetingProgressEvent {
  stage: "stt" | "chunk_start" | "chunk" | "generate" | "done" | "error";
  message?: string;
  file_id?: string;
  download_url?: string;
  expires_at?: string;
}

export async function generateMeetingMinutesStream(
  params: GenerateParams,
  onProgress: (evt: MeetingProgressEvent) => void
): Promise<MeetingMinutesResult> {
  const form = new FormData();
  form.append("pages", String(params.pages));
  if (params.transcript) form.append("transcript", params.transcript);
  if (params.audio_file) form.append("audio_file", params.audio_file);

  const res = await fetch(API_BASE + "/api/meeting-minutes/generate-stream", {
    method: "POST",
    body: form,
  });

  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      let evt: MeetingProgressEvent;
      try {
        evt = JSON.parse(line.slice(5).trim()) as MeetingProgressEvent;
      } catch {
        continue; // 손상된 SSE 라인 무시
      }
      onProgress(evt);
      if (evt.stage === "done" && evt.file_id && evt.download_url && evt.expires_at) {
        return { file_id: evt.file_id, download_url: evt.download_url, expires_at: evt.expires_at };
      }
      if (evt.stage === "error") {
        throw new Error(evt.message ?? "생성 실패");
      }
    }
  }

  throw new Error("스트림이 완료 이벤트 없이 종료됐습니다.");
}

// 아래 함수들은 현재 미사용 (3단계 분리 이전 API). 전체 시스템 검토 시 삭제 여부 결정.
// export async function generateMeetingMinutes(
//   params: GenerateParams
// ): Promise<MeetingMinutesResult> {
//   if (params.audio_file) {
//     const form = new FormData();
//     form.append("audio_file", params.audio_file);
//     form.append("pages", String(params.pages));
//     if (params.transcript) form.append("transcript", params.transcript);
//     const res = await fetch(API_BASE + "/api/meeting-minutes/generate-audio", {
//       method: "POST",
//       body: form,
//     });
//     if (!res.ok) {
//       let detail = `${res.status} ${res.statusText}`;
//       try {
//         const body = (await res.json()) as { detail?: string };
//         if (body.detail) detail = body.detail;
//       } catch { /* ignore */ }
//       throw new Error(detail);
//     }
//     return res.json() as Promise<MeetingMinutesResult>;
//   }
//   return apiFetch<MeetingMinutesResult>("/api/meeting-minutes/generate", {
//     method: "POST",
//     body: JSON.stringify({ transcript: params.transcript, pages: params.pages }),
//   });
// }

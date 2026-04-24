import type { CalendarEvent, RagDocument } from "../types";

// ────────────────────────────────────────────────────────────
// fetch 래퍼 — 사내 IP / localhost 전용
// ────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// 백엔드 RAG 응답 원본 형태 (doc_id 필드)
interface RagDocumentRaw {
  doc_id: string;
  filename: string;
  chunk_count: number;
}

function mapRagDoc(raw: RagDocumentRaw): RagDocument {
  return { id: raw.doc_id, filename: raw.filename, chunk_count: raw.chunk_count };
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

export async function deleteCalendarEvent(id: string): Promise<void> {
  await apiFetch<unknown>(`/api/calendar/events/${id}`, { method: "DELETE" });
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
  onProgress?: (pct: number) => void
): Promise<RagDocument> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/rag/documents");

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
        reject(new Error(`Upload failed: ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload"));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export async function deleteDocument(id: string): Promise<void> {
  await apiFetch<unknown>(`/api/rag/documents/${id}`, { method: "DELETE" });
}

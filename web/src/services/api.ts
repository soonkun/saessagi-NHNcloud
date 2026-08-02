import type {
  CalendarEvent,
  RagDocument,
  RagFolder,
  KnowledgeNote,
  KnowledgeNoteMeta,
  KnowledgeGraphData,
  GraphRagData,
  GraphRagEvidence,
  GraphRagStatus,
} from "../types";

// Electron은 file:// 로드 → 상대경로가 백엔드로 라우팅되지 않으므로 절대 URL 필요
export const API_BASE: string =
  (window as { electronAPI?: { isElectron?: boolean } }).electronAPI?.isElectron
    ? "http://127.0.0.1:12393"
    : "";

// ────────────────────────────────────────────────────────────
// 인증 (M_21 / CR-38)
// ────────────────────────────────────────────────────────────

/** 인증이 켜져 있는지 — 로그아웃 버튼 노출 여부 판단용. 실패 시 false(버튼 숨김). */
export async function fetchAuthEnabled(): Promise<boolean> {
  try {
    const res = await fetch(API_BASE + "/api/auth/status");
    if (!res.ok) return false;
    const body = (await res.json()) as { auth_enabled?: boolean };
    return !!body.auth_enabled;
  } catch {
    return false;
  }
}

/**
 * 로그아웃 — 세션 쿠키를 지우고 로그인 화면으로 보낸다.
 *
 * replace()를 쓰는 이유: assign()이면 뒤로가기로 로그아웃 이전 화면(캐시)이 보여
 * 로그아웃된 것처럼 안 느껴진다. 서버 요청은 쿠키가 없어 어차피 302로 튕긴다.
 */
export async function logout(): Promise<void> {
  try {
    await fetch(API_BASE + "/api/auth/logout", { method: "POST" });
  } catch {
    // 네트워크 실패해도 로그인 화면으로는 보낸다 — 쿠키가 남아 있으면 다시 들어가진다.
  }
  window.location.replace("/login");
}

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

// CR-28: 문서 청크 목록 (청킹 검증 뷰어)
export interface DocumentChunkInfo {
  page: number | null;
  chars: number;
  text: string;
}

export async function fetchDocumentChunks(
  docId: string
): Promise<{ doc_id: string; doc_name: string; chunk_count: number; chunks: DocumentChunkInfo[] }> {
  return apiFetch(`/api/rag/documents/${encodeURIComponent(docId)}/chunks`);
}

// CR-24: 문서를 다른 폴더로 이동 (folderId null = 미분류)
export async function moveDocument(id: string, folderId: string | null): Promise<void> {
  await apiFetch<unknown>(`/api/rag/documents/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_id: folderId }),
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

// CR-23: 노트 AI 편집 — 지시(선택 영역·첨부 포함)로 본문 재작성/부분 대체 텍스트 생성
export async function aiEditNote(body: {
  instruction: string;
  content: string;
  title?: string;
  selection?: string;
  file?: File | null;
}): Promise<{ mode: "whole" | "selection"; result: string }> {
  const form = new FormData();
  form.append("instruction", body.instruction);
  form.append("content", body.content);
  if (body.title) form.append("title", body.title);
  if (body.selection) form.append("selection", body.selection);
  if (body.file) form.append("file", body.file);
  const res = await fetch(API_BASE + "/api/knowledge/notes/ai-edit", {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `AI 편집 실패 (${res.status})`);
  }
  return (await res.json()) as { mode: "whole" | "selection"; result: string };
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
// M_19 GraphRAG API (CR-18)
// ────────────────────────────────────────────────────────────

export async function fetchGraphRag(limit = 500, types: string[] = []): Promise<GraphRagData> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (types.length > 0) q.set("types", types.join(","));
  return apiFetch<GraphRagData>(`/api/graphrag/graph?${q.toString()}`);
}

export async function fetchGraphRagStatus(): Promise<GraphRagStatus> {
  return apiFetch<GraphRagStatus>("/api/graphrag/status");
}

// CR-37: 한 문서 중심 포커스 서브그래프 (검색→선택 시 그 과제와 연결만)
export async function fetchGraphDocFocus(docId: string, limit = 40): Promise<GraphRagData> {
  const q = new URLSearchParams({ doc_id: docId, limit: String(limit) });
  return apiFetch<GraphRagData>(`/api/graphrag/doc-focus?${q.toString()}`);
}

// CR-35: onlyMissing=true면 그래프에 없는 문서만 인덱싱 (증분)
export async function requestGraphReindex(
  docId?: string,
  onlyMissing = false
): Promise<{ scheduled: boolean; count: number }> {
  return apiFetch<{ scheduled: boolean; count: number }>("/api/graphrag/reindex", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id: docId ?? null, only_missing: onlyMissing }),
  });
}

// CR-26: 진행·대기 중 그래프 인덱싱 중단
export async function cancelGraphIndexing(): Promise<{ cancelled: number }> {
  return apiFetch<{ cancelled: number }>("/api/graphrag/cancel", { method: "POST" });
}

// CR-26: 그래프 전체 초기화 — confirm 문구가 정확히 일치해야 실행됨
export async function clearGraph(confirm: string): Promise<{ ok: boolean; before: Record<string, number> }> {
  return apiFetch<{ ok: boolean; before: Record<string, number> }>("/api/graphrag/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm }),
  });
}

// CR-31: 그래프 문서(과제) 검색 — 제목·키워드 신호로 문서만 찾는다
export interface GraphDocMatch {
  doc_id: string;
  title: string;
  project_no: string;
  title_match: boolean;
  matched_keywords: string[];
}

export async function searchGraphDocs(q: string, limit = 12): Promise<GraphDocMatch[]> {
  const data = await apiFetch<{ docs: GraphDocMatch[] }>(
    `/api/graphrag/search-docs?q=${encodeURIComponent(q)}&limit=${limit}`
  );
  return data.docs;
}

// CR-35: onlyNew=true(기본)면 아직 정규화 안 된 키워드만 (증분). false면 전체 재정규화
export async function requestGraphNormalize(
  onlyNew = true
): Promise<{ groups: string[][]; merged: number }> {
  return apiFetch<{ groups: string[][]; merged: number }>("/api/graphrag/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ only_new: onlyNew }),
  });
}

export interface ContentSearchHit {
  doc_id: string;
  doc_name: string;
  page: number | null;
  snippet: string;
  score: number;
}

export async function searchRagContent(q: string, topK = 5): Promise<ContentSearchHit[]> {
  const data = await apiFetch<{ hits: ContentSearchHit[] }>(
    `/api/rag/search?q=${encodeURIComponent(q)}&top_k=${topK}`
  );
  return data.hits;
}

export async function fetchGraphEvidence(): Promise<GraphRagEvidence | null> {
  try {
    return await apiFetch<GraphRagEvidence>("/api/graphrag/evidence/latest");
  } catch {
    return null; // 404 = 아직 근거 없음
  }
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

// ── LLM 공급자 (CR-55) ────────────────────────────────────────────────────────
// 설정 화면 안에만 있던 조회를 공용으로 옮겼다 — 상태줄에 쓰는 모델명은
// 설정 탭을 열지 않아도 앱 시작 시 알아야 한다.

export interface LlmProviderState {
  provider: "ollama" | "openai";
  openai_api_key_set: boolean;
  openai_model: string;
  ollama_model: string;
}

export async function fetchLlmProvider(): Promise<LlmProviderState | null> {
  try {
    const res = await fetch(API_BASE + "/api/settings/llm-provider");
    if (!res.ok) return null;
    return (await res.json()) as LlmProviderState;
  } catch {
    return null;
  }
}

// ── 기능별 적용 중인 모델 (CR-57) ────────────────────────────────────────────
// 설정이 대화·비전·의도분류·그래프추출·딥리서치로 나뉘어, 어느 화면이 어떤 모델로
// 도는지 알 수가 없었다. 화면 제목 옆에 붙이려고 한 번에 받아온다.

export interface ActiveModel {
  key: "chat" | "intent_gate" | "vision" | "graphrag" | "deep_research";
  label: string;
  provider: string;
  model: string;
  /** 해당 기능이 꺼져 있으면 false (의도분류기·지식그래프). */
  enabled?: boolean;
}

export async function fetchActiveModels(): Promise<ActiveModel[]> {
  try {
    const res = await fetch(API_BASE + "/api/settings/active-models");
    if (!res.ok) return [];
    const data = (await res.json()) as { models?: ActiveModel[] };
    return data.models ?? [];
  } catch {
    return [];
  }
}

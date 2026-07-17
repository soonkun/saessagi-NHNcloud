// 공유 타입 정의

export type Emotion =
  | "neutral"
  | "happy"
  | "sad"
  | "surprised"
  | "thinking"
  | "sleepy"
  | "study"
  | "writing"
  | "note_writing"
  | "uploading"
  | "worried";

export type AiStatus = "idle" | "thinking" | "speaking";

export type ChatTab =
  | "chat"
  | "calendar"
  | "documents"
  | "meeting"
  | "notes"
  | "graph"
  | "research"
  | "settings";

// ── M_19 GraphRAG (CR-18) ──────────────────────────────────────────────────
export interface GraphRagNode {
  id: string;
  label: string;
  kind: "entity" | "document" | "note";
  type: string; // 엔티티 타입 (인물|조직|사업|제도|기술|장소|기타), 비엔티티는 ""
}

export interface GraphRagEdge {
  source: string;
  target: string;
  kind: "rel" | "mentioned_in" | "part_of";
  weight: number;
}

export interface GraphRagData {
  nodes: GraphRagNode[];
  edges: GraphRagEdge[];
  stats: Record<string, number>;
}

export interface GraphRagStatus {
  enabled: boolean;
  connected: boolean;
  stats: Record<string, number>;
  indexing: {
    doc_id: string;
    state: string;
    total_chunks: number;
    done_chunks: number;
    skipped_chunks: number;
    error: string;
  }[];
}

export interface GraphRagEvidence {
  query: string;
  created: string;
  nodes: GraphRagNode[];
  edges: GraphRagEdge[];
  chunk_ids: string[];
}

export interface KnowledgeNoteMeta {
  slug: string;
  title: string;
  tags: string[];
  related_docs: string[];
  created: string;
  updated: string;
}

export interface RelatedDocInfo {
  id: string;
  filename?: string | null;
}

export interface KnowledgeNote extends KnowledgeNoteMeta {
  content: string;
  related_docs_info?: RelatedDocInfo[];
}

export interface KnowledgeGraphData {
  nodes: { slug: string; title: string; tags: string[] }[];
  edges: { source: string; target: string; kind: "wikilink" | "tag" | "doc" }[];
}

export interface CitedDoc {
  id: string;
  filename: string;
}

export interface CitedNote {
  slug: string;
  title: string;
}

export interface MessageAttachment {
  id: string;
  filename: string;
}

export interface MessageImage {
  /** data URL: data:image/png;base64,... */
  dataUrl: string;
  filename: string;
}

export interface Message {
  id: string;
  role: "human" | "ai";
  text: string;
  timestamp: number;
  citedDocs?: CitedDoc[];
  citedNotes?: CitedNote[];
  attachments?: MessageAttachment[];
  images?: MessageImage[];
}

export interface Position {
  x: number;
  y: number;
}

// WebSocket 수신 메시지 타입
export interface WsControlMessage {
  type: "control";
  text: string; // upstream: "start-mic" | "stop-mic" | "conversation-chain-start" | "conversation-chain-end"
}

export interface WsAudioMessage {
  type: "audio";
  audio: string;
  display_text?: { text: string; type: string };
  expression?: string;
}

export interface WsChatMessage {
  type: "message";
  message: string;
  role: "human" | "ai";
}

export interface WsAvatarStateMessage {
  type: "avatar-state";
  emotion: Emotion;
  speaking: boolean;
}

export interface WsToolCallStatusMessage {
  type: "tool_call_status"; // 백엔드 실제 전송값 (언더스코어)
  tool_id: string;
  tool_name: string;
  status: "running" | "completed" | "error";
  /** tool_name="_agent_status"일 때 진행 상태 문구 ("문서를 찾아보고 있어요…" 등) */
  content?: string;
}

export interface WsBackendSynthComplete {
  type: "backend-synth-complete";
}

export interface WsNewHistoryCreated {
  type: "new-history-created";
  history_uid: string;
}

// CR-23: 대화 히스토리 (upstream chat_history_manager 형식)
export interface HistoryMessage {
  role: "human" | "ai";
  content: string;
  timestamp?: string | null;
}

export interface HistoryInfo {
  uid: string;
  latest_message: HistoryMessage | null;
  timestamp: string | null;
}

export interface WsHistoryListMessage {
  type: "history-list";
  histories: HistoryInfo[];
}

export interface WsHistoryDataMessage {
  type: "history-data";
  messages: HistoryMessage[];
}

export interface WsHistoryDeletedMessage {
  type: "history-deleted";
  success: boolean;
  history_uid: string;
}

export type WsIncomingMessage =
  | WsControlMessage
  | WsAudioMessage
  | WsChatMessage
  | WsAvatarStateMessage
  | WsToolCallStatusMessage
  | WsBackendSynthComplete
  | WsNewHistoryCreated
  | WsHistoryListMessage
  | WsHistoryDataMessage
  | WsHistoryDeletedMessage;

/**
 * upstream ImageData dataclass 매칭 — `{source, data, mime_type}` 필수.
 * - source: "camera" | "screen" | "clipboard" | "upload"
 * - data: "data:image/png;base64,..." 형식의 full data URL
 * - mime_type: "image/png" 등
 */
export interface WsImagePayload {
  source: "camera" | "screen" | "clipboard" | "upload";
  data: string;
  mime_type: string;
}

// WebSocket 송신 메시지 타입
export interface WsSendUserMessage {
  type: "text-input"; // upstream은 "text-input" 타입만 처리
  text: string;
  /** 비전 LLM에 전달할 이미지. upstream conversation_utils.create_batch_input이 dict로 인덱싱 */
  images?: WsImagePayload[];
}

export interface WsSendNewHistory {
  type: "create-new-history";
}

export interface WsSendInterrupt {
  type: "interrupt-signal";
}

export interface WsSendPlaybackComplete {
  type: "frontend-playback-complete";
}

// CR-23: 대화 히스토리 송신 메시지 (upstream websocket_handler 계약)
export interface WsSendFetchHistoryList {
  type: "fetch-history-list";
}

export interface WsSendFetchAndSetHistory {
  type: "fetch-and-set-history";
  history_uid: string;
}

export interface WsSendDeleteHistory {
  type: "delete-history";
  history_uid: string;
}

export type WsOutgoingMessage =
  | WsSendUserMessage
  | WsSendNewHistory
  | WsSendInterrupt
  | WsSendPlaybackComplete
  | WsSendFetchHistoryList
  | WsSendFetchAndSetHistory
  | WsSendDeleteHistory;

// Calendar 타입
export interface CalendarEvent {
  id: number;
  title: string;
  start: string; // ISO datetime
  duration_minutes?: number; // backend field name
  description?: string;
}

// RAG 폴더 타입
export interface RagFolder {
  folder_id: string;
  name: string;
}

// RAG 문서 타입
export interface RagDocument {
  id: string;
  filename: string;
  chunk_count: number;
  folder_id?: string | null;
  uploaded_at?: string;
}

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
  | "worried";

export type AiStatus = "idle" | "thinking" | "speaking";

export type SidebarView = "calendar" | "documents" | "settings";

export type ChatTab = "chat" | "calendar" | "documents" | "meeting" | "notes" | "settings";

export interface KnowledgeNoteMeta {
  slug: string;
  title: string;
  tags: string[];
  related_docs: string[];
  created: string;
  updated: string;
}

export interface KnowledgeNote extends KnowledgeNoteMeta {
  content: string;
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

export interface Message {
  id: string;
  role: "human" | "ai";
  text: string;
  timestamp: number;
  citedDocs?: CitedDoc[];
  citedNotes?: CitedNote[];
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
  type: "tool-call-status";
  tool_id: string;
  tool_name: string;
  status: "running" | "completed" | "error";
}

export interface WsBackendSynthComplete {
  type: "backend-synth-complete";
}

export interface WsNewHistoryCreated {
  type: "new-history-created";
  history_uid: string;
}

export type WsIncomingMessage =
  | WsControlMessage
  | WsAudioMessage
  | WsChatMessage
  | WsAvatarStateMessage
  | WsToolCallStatusMessage
  | WsBackendSynthComplete
  | WsNewHistoryCreated;

// WebSocket 송신 메시지 타입
export interface WsSendUserMessage {
  type: "text-input"; // upstream은 "text-input" 타입만 처리
  text: string;
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

export type WsOutgoingMessage =
  | WsSendUserMessage
  | WsSendNewHistory
  | WsSendInterrupt
  | WsSendPlaybackComplete;

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

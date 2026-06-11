import { create } from "zustand";
import type {
  Message,
  Emotion,
  AiStatus,
  Position,
  SidebarView,
  ChatTab,
} from "./types";

// ────────────────────────────────────────────────────────────
// 초기 캐릭터 위치: 화면 오른쪽 아래 (저장된 값 우선)
// ────────────────────────────────────────────────────────────
function loadPosition(): Position {
  try {
    const raw = localStorage.getItem("saessagi_char_pos");
    if (raw) return JSON.parse(raw) as Position;
  } catch {
    // ignore
  }
  return { x: window.innerWidth - 32 - 120, y: window.innerHeight - 32 - 120 };
}

function loadCharSize(): number {
  try {
    const raw = localStorage.getItem("saessagi_char_size");
    if (raw) {
      const n = Number(raw);
      if (n >= 60 && n <= 300) return n;
    }
  } catch { /* ignore */ }
  return 120;
}

function loadWsUrl(): string {
  return (
    localStorage.getItem("saessagi_ws_url") ??
    "ws://127.0.0.1:12393/client-ws"
  );
}

function loadTtsRate(): number {
  try {
    const raw = localStorage.getItem("saessagi_tts_rate");
    if (raw) {
      const n = Number(raw);
      if (n >= 0.5 && n <= 2.0) return n;
    }
  } catch { /* ignore */ }
  return 1.2;
}

function loadTtsVoiceName(): string {
  // 빈 문자열도 "미설정"으로 처리해 기본값 사용
  return localStorage.getItem("saessagi_tts_voice") || "Shelley (한국어(대한민국))";
}

function loadTtsEngine(): "melo" | "system" {
  return localStorage.getItem("saessagi_tts_engine") === "system" ? "system" : "melo";
}

export interface LlmInfo {
  provider: "ollama" | "openai";
  model: string;
}

export type ThemeMode = "dark" | "light";

function loadTheme(): ThemeMode {
  return localStorage.getItem("saessagi_theme") === "light" ? "light" : "dark";
}

function loadLlmInfo(): LlmInfo | null {
  try {
    const raw = localStorage.getItem("saessagi_llm_info");
    if (raw) return JSON.parse(raw) as LlmInfo;
  } catch { /* ignore */ }
  return null;
}

// ────────────────────────────────────────────────────────────
// Chat Slice
// ────────────────────────────────────────────────────────────
interface ChatSlice {
  messages: Message[];
  aiStatus: AiStatus;
  /** 진행 상태 말풍선 텍스트 ("문서를 찾아보고 있어요…" 등). null이면 비표시 */
  agentStatus: string | null;
  isMicOn: boolean;
  addMessage: (msg: Omit<Message, "id" | "timestamp">) => string;
  setAiStatus: (status: AiStatus) => void;
  setAgentStatus: (text: string | null) => void;
  setMicOn: (on: boolean) => void;
  clearMessages: () => void;
  attachCitations: (messageId: string, citedDocs: import("./types").CitedDoc[]) => void;
  attachNoteCitations: (messageId: string, citedNotes: import("./types").CitedNote[]) => void;
}

// ────────────────────────────────────────────────────────────
// Avatar Slice
// ────────────────────────────────────────────────────────────
interface AvatarSlice {
  emotion: Emotion;
  speaking: boolean;
  position: Position;
  charSize: number;
  isUploading: boolean;
  isMeetingGenerating: boolean;
  setEmotion: (emotion: Emotion) => void;
  setSpeaking: (speaking: boolean) => void;
  setPosition: (pos: Position) => void;
  setPositionSilent: (pos: Position) => void;
  setCharSize: (size: number) => void;
  setIsUploading: (v: boolean) => void;
  setMeetingGenerating: (v: boolean) => void;
}

// ────────────────────────────────────────────────────────────
// UI Slice
// ────────────────────────────────────────────────────────────
interface UiSlice {
  chatOpen: boolean;
  chatTab: ChatTab;
  activeView: SidebarView | null;
  wsUrl: string;
  ttsRate: number;
  ttsVoiceName: string;
  ttsEngine: "melo" | "system";
  windowMode: "pet" | "window";
  llmInfo: LlmInfo | null;
  selectedNoteSlug: string | null;
  notesRevision: number;
  theme: ThemeMode;
  toggleChat: () => void;
  setChatOpen: (open: boolean) => void;
  setChatTab: (tab: ChatTab) => void;
  setActiveView: (view: SidebarView | null) => void;
  setWindowMode: (mode: "pet" | "window") => void;
  setLlmInfo: (info: LlmInfo | null) => void;
  setSelectedNoteSlug: (slug: string | null) => void;
  bumpNotesRevision: () => void;
  setTheme: (theme: ThemeMode) => void;
  setWsUrl: (url: string) => void;
  setTtsRate: (rate: number) => void;
  setTtsVoiceName: (name: string) => void;
  setTtsEngine: (engine: "melo" | "system") => void;
}

// ────────────────────────────────────────────────────────────
// Combined Store
// ────────────────────────────────────────────────────────────
type AppStore = ChatSlice & AvatarSlice & UiSlice;

let _msgCounter = 0;
function nextId(): string {
  return `msg_${Date.now()}_${++_msgCounter}`;
}

export const useStore = create<AppStore>((set) => ({
  // Chat
  messages: [],
  aiStatus: "idle",
  agentStatus: null,
  isMicOn: false,
  addMessage: (msg) => {
    const id = nextId();
    set((state) => ({
      messages: [
        ...state.messages,
        { ...msg, id, timestamp: Date.now() },
      ],
      // AI 답변이 표시되는 순간 진행 상태 말풍선은 치운다
      ...(msg.role === "ai" ? { agentStatus: null } : {}),
    }));
    return id;
  },
  // idle 전환 시 진행 상태도 함께 정리 (대화 종료·에러·타임아웃 전 경로 공통)
  setAiStatus: (status) =>
    set(status === "idle" ? { aiStatus: status, agentStatus: null } : { aiStatus: status }),
  setAgentStatus: (text) => set({ agentStatus: text }),
  setMicOn: (on) => set({ isMicOn: on }),
  clearMessages: () => set({ messages: [] }),
  attachCitations: (messageId, citedDocs) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId ? { ...m, citedDocs } : m
      ),
    })),
  attachNoteCitations: (messageId, citedNotes) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId ? { ...m, citedNotes } : m
      ),
    })),

  // Avatar
  emotion: "neutral",
  speaking: false,
  position: loadPosition(),
  charSize: loadCharSize(),
  isUploading: false,
  isMeetingGenerating: false,
  setEmotion: (emotion) => set({ emotion }),
  setSpeaking: (speaking) => set({ speaking }),
  setIsUploading: (v) => set({ isUploading: v }),
  setMeetingGenerating: (v) => set({ isMeetingGenerating: v }),
  setPosition: (pos) => {
    try {
      localStorage.setItem("saessagi_char_pos", JSON.stringify(pos));
    } catch {
      // ignore
    }
    set({ position: pos });
  },
  setPositionSilent: (pos) => set({ position: pos }),
  setCharSize: (size) => {
    try {
      localStorage.setItem("saessagi_char_size", String(size));
    } catch { /* ignore */ }
    set({ charSize: size });
  },

  // UI
  chatOpen: false,
  chatTab: "chat" as ChatTab,
  activeView: "calendar" as SidebarView,
  wsUrl: loadWsUrl(),
  ttsRate: loadTtsRate(),
  ttsVoiceName: loadTtsVoiceName(),
  ttsEngine: loadTtsEngine(),
  windowMode: "pet" as "pet" | "window",
  llmInfo: loadLlmInfo(),
  selectedNoteSlug: null as string | null,
  notesRevision: 0,
  theme: loadTheme(),
  toggleChat: () => set((state) => ({ chatOpen: !state.chatOpen })),
  setChatOpen: (open) => set({ chatOpen: open }),
  setChatTab: (tab) => set({ chatTab: tab }),
  setActiveView: (view) => set({ activeView: view }),
  setWindowMode: (mode) => set({ windowMode: mode }),
  setLlmInfo: (info) => {
    try {
      if (info) localStorage.setItem("saessagi_llm_info", JSON.stringify(info));
      else localStorage.removeItem("saessagi_llm_info");
    } catch { /* ignore */ }
    set({ llmInfo: info });
  },
  setSelectedNoteSlug: (slug) => set({ selectedNoteSlug: slug }),
  bumpNotesRevision: () => set((state) => ({ notesRevision: state.notesRevision + 1 })),
  setTheme: (theme) => {
    try { localStorage.setItem("saessagi_theme", theme); } catch { /* ignore */ }
    set({ theme });
  },
  setWsUrl: (url) => {
    try {
      localStorage.setItem("saessagi_ws_url", url);
    } catch {
      // ignore
    }
    set({ wsUrl: url });
  },
  setTtsRate: (rate) => {
    try { localStorage.setItem("saessagi_tts_rate", String(rate)); } catch { /* ignore */ }
    set({ ttsRate: rate });
  },
  setTtsVoiceName: (name) => {
    try { localStorage.setItem("saessagi_tts_voice", name); } catch { /* ignore */ }
    set({ ttsVoiceName: name });
  },
  setTtsEngine: (engine) => {
    try { localStorage.setItem("saessagi_tts_engine", engine); } catch { /* ignore */ }
    set({ ttsEngine: engine });
  },
}));

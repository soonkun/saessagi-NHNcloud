import { create } from "zustand";
import type {
  Message,
  Emotion,
  AiStatus,
  Position,
  SidebarView,
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

function loadWsUrl(): string {
  return (
    localStorage.getItem("saessagi_ws_url") ??
    "ws://127.0.0.1:12393/client-ws"
  );
}

// ────────────────────────────────────────────────────────────
// Chat Slice
// ────────────────────────────────────────────────────────────
interface ChatSlice {
  messages: Message[];
  aiStatus: AiStatus;
  isMicOn: boolean;
  addMessage: (msg: Omit<Message, "id" | "timestamp">) => void;
  setAiStatus: (status: AiStatus) => void;
  setMicOn: (on: boolean) => void;
  clearMessages: () => void;
}

// ────────────────────────────────────────────────────────────
// Avatar Slice
// ────────────────────────────────────────────────────────────
interface AvatarSlice {
  emotion: Emotion;
  speaking: boolean;
  position: Position;
  setEmotion: (emotion: Emotion) => void;
  setSpeaking: (speaking: boolean) => void;
  setPosition: (pos: Position) => void;
}

// ────────────────────────────────────────────────────────────
// UI Slice
// ────────────────────────────────────────────────────────────
interface UiSlice {
  chatOpen: boolean;
  activeView: SidebarView | null;
  wsUrl: string;
  toggleChat: () => void;
  setChatOpen: (open: boolean) => void;
  setActiveView: (view: SidebarView | null) => void;
  setWsUrl: (url: string) => void;
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
  isMicOn: false,
  addMessage: (msg) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { ...msg, id: nextId(), timestamp: Date.now() },
      ],
    })),
  setAiStatus: (status) => set({ aiStatus: status }),
  setMicOn: (on) => set({ isMicOn: on }),
  clearMessages: () => set({ messages: [] }),

  // Avatar
  emotion: "neutral",
  speaking: false,
  position: loadPosition(),
  setEmotion: (emotion) => set({ emotion }),
  setSpeaking: (speaking) => set({ speaking }),
  setPosition: (pos) => {
    try {
      localStorage.setItem("saessagi_char_pos", JSON.stringify(pos));
    } catch {
      // ignore
    }
    set({ position: pos });
  },

  // UI
  chatOpen: false,
  activeView: "calendar" as SidebarView,
  wsUrl: loadWsUrl(),
  toggleChat: () => set((state) => ({ chatOpen: !state.chatOpen })),
  setChatOpen: (open) => set({ chatOpen: open }),
  setActiveView: (view) => set({ activeView: view }),
  setWsUrl: (url) => {
    try {
      localStorage.setItem("saessagi_ws_url", url);
    } catch {
      // ignore
    }
    set({ wsUrl: url });
  },
}));

import { useEffect, useRef, useState } from "react";
import {
  MessageCircle,
  Calendar,
  FolderOpen,
  FileAudio,
  Settings,
  Send,
  Mic,
  MicOff,
  X,
  RotateCcw,
} from "lucide-react";
import { useStore } from "../store";
import { send } from "../services/websocket";
import { startVoice, stopVoice } from "../services/voice";
import { CalendarView } from "./CalendarView";
import { DocumentsView } from "./DocumentsView";
import { MeetingView } from "./MeetingView";
import { SettingsView } from "./SettingsView";
import type { Position, ChatTab } from "../types";

const PANEL_W = 580;
const PANEL_H = 660;
const GAP = 8;

interface ChatPanelProps {
  charPosition: Position;
  charSize: number;
}

function calcPanelStyle(charPos: Position, charSize: number): React.CSSProperties {
  let left = charPos.x + charSize - PANEL_W;
  let top = charPos.y - PANEL_H - GAP;
  if (left < 8) left = 8;
  if (left + PANEL_W > window.innerWidth - 8)
    left = window.innerWidth - 8 - PANEL_W;
  if (top < 8) top = charPos.y + charSize + GAP;
  if (top + PANEL_H > window.innerHeight - 8)
    top = window.innerHeight - 8 - PANEL_H;
  return { position: "fixed", left, top, width: PANEL_W, height: PANEL_H };
}

const STATUS_LABEL: Record<string, string> = {
  idle: "대기 중",
  thinking: "생각 중...",
  speaking: "말하는 중...",
};

const STATUS_COLOR: Record<string, string> = {
  idle: "#888",
  thinking: "var(--color-accent)",
  speaking: "#4caf84",
};

const TABS: { id: ChatTab; label: string; Icon: React.ElementType }[] = [
  { id: "chat", label: "새싹이", Icon: MessageCircle },
  { id: "calendar", label: "일정표", Icon: Calendar },
  { id: "documents", label: "문서", Icon: FolderOpen },
  { id: "meeting", label: "회의록", Icon: FileAudio },
  { id: "settings", label: "설정", Icon: Settings },
];

// ────────────────────────────────────────────────────────────
// Chat content
// ────────────────────────────────────────────────────────────

function ChatContent(): React.ReactElement {
  const messages = useStore((s) => s.messages);
  const aiStatus = useStore((s) => s.aiStatus);
  const addMessage = useStore((s) => s.addMessage);

  const [input, setInput] = useState("");
  const [voiceActive, setVoiceActive] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  function handleNewHistory(): void {
    send({ type: "create-new-history" });
    // 백엔드 응답(new-history-created)으로 clearMessages가 호출됨
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  function handleSend(): void {
    const text = input.trim();
    if (!text) return;
    addMessage({ role: "human", text });
    send({ type: "text-input", text });
    setInput("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    // nativeEvent.isComposing: 한국어/일본어 IME 조합 중 Enter는 무시
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleMicToggle(): void {
    if (voiceActive) {
      stopVoice();
      setVoiceActive(false);
      return;
    }
    void startVoice({
      onStart: () => setVoiceActive(true),
      onStop: () => setVoiceActive(false),
      onText: (text) => {
        addMessage({ role: "human", text });
        send({ type: "text-input", text });
      },
      onError: (msg) => {
        console.warn("[STT]", msg);
        setVoiceActive(false);
      },
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 상태 표시 줄 */}
      <div
        style={{
          padding: "8px 16px",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}
      >
        <span
          className={aiStatus !== "idle" ? "status-blink" : ""}
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: STATUS_COLOR[aiStatus] ?? "#888",
            display: "inline-block",
            flexShrink: 0,
          }}
        />
        <span style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
          {STATUS_LABEL[aiStatus] ?? ""}
        </span>
        {voiceActive && (
          <span
            className="status-blink"
            style={{ fontSize: 12, color: "#e74c3c", marginLeft: "auto" }}
          >
            ● 녹음 중
          </span>
        )}
        <button
          onClick={handleNewHistory}
          title="새 대화 시작 (대화 기억 초기화)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            display: "flex",
            alignItems: "center",
            padding: "2px 4px",
            borderRadius: 4,
            flexShrink: 0,
          }}
        >
          <RotateCcw size={13} />
        </button>
      </div>

      {/* 메시지 목록 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              color: "var(--color-text-muted)",
              textAlign: "center",
              marginTop: 40,
              fontSize: 13,
            }}
          >
            안녕하세요! 무엇을 도와드릴까요?
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className="msg-enter"
            style={{
              display: "flex",
              justifyContent: msg.role === "human" ? "flex-end" : "flex-start",
            }}
          >
            <div
              style={{
                maxWidth: "80%",
                padding: "8px 12px",
                borderRadius:
                  msg.role === "human"
                    ? "12px 12px 4px 12px"
                    : "12px 12px 12px 4px",
                background:
                  msg.role === "human"
                    ? "var(--color-msg-human)"
                    : "var(--color-msg-ai)",
                border: "1px solid var(--color-border)",
                fontSize: 13,
                lineHeight: 1.5,
                wordBreak: "break-word",
                whiteSpace: "pre-wrap",
              }}
            >
              {msg.text}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 입력 영역 */}
      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--color-border)",
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <button
          onClick={handleMicToggle}
          title={voiceActive ? "녹음 중단" : "마이크 누르고 말하기"}
          style={{
            background: voiceActive ? "rgba(231,76,60,0.2)" : "transparent",
            border: `1px solid ${voiceActive ? "#e74c3c" : "var(--color-border)"}`,
            borderRadius: 8,
            color: voiceActive ? "#e74c3c" : "var(--color-text-muted)",
            cursor: "pointer",
            padding: "6px 8px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          {voiceActive ? <Mic size={16} /> : <MicOff size={16} />}
        </button>

        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="메시지를 입력하세요..."
          style={{
            flex: 1,
            background: "var(--color-bg)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            color: "var(--color-text)",
            padding: "7px 12px",
            fontSize: 13,
            outline: "none",
          }}
        />

        <button
          onClick={handleSend}
          disabled={!input.trim()}
          title="전송"
          style={{
            background: input.trim()
              ? "var(--color-accent)"
              : "var(--color-border)",
            border: "none",
            borderRadius: 8,
            color: "#fff",
            cursor: input.trim() ? "pointer" : "default",
            padding: "6px 10px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
            transition: "background 0.15s",
          }}
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// ChatPanel
// ────────────────────────────────────────────────────────────

export function ChatPanel({ charPosition, charSize }: ChatPanelProps): React.ReactElement {
  const setChatOpen = useStore((s) => s.setChatOpen);
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);

  const panelStyle = calcPanelStyle(charPosition, charSize);

  return (
    <div
      id="chat-panel"
      onMouseEnter={() => window.electronAPI?.setIgnoreMouseEvents(false)}
      style={{
        ...panelStyle,
        zIndex: 999,
        background: "var(--color-panel)",
        borderRadius: 12,
        border: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        // Override #root pointer-events:none so the panel is interactive
        pointerEvents: "auto",
      }}
    >
      {/* 네비게이션 탭 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          borderBottom: "1px solid var(--color-border)",
          flexShrink: 0,
          background: "var(--color-sidebar)",
          paddingRight: 8,
        }}
      >
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setChatTab(id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: "11px 14px",
              border: "none",
              borderBottom: chatTab === id
                ? "2px solid var(--color-accent)"
                : "2px solid transparent",
              background: "transparent",
              color: chatTab === id ? "var(--color-accent)" : "var(--color-text-muted)",
              cursor: "pointer",
              fontSize: 12,
              fontWeight: chatTab === id ? 600 : 400,
              transition: "color 0.15s",
              flexShrink: 0,
            }}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}

        {/* 닫기 버튼 — 우측 */}
        <button
          onClick={() => setChatOpen(false)}
          style={{
            marginLeft: "auto",
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            display: "flex",
            alignItems: "center",
            padding: "4px 6px",
            borderRadius: 4,
          }}
          title="닫기"
        >
          <X size={15} />
        </button>
      </div>

      {/* 컨텐츠 영역 */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {chatTab === "chat" && <ChatContent />}
        {chatTab === "calendar" && <CalendarView />}
        {chatTab === "documents" && <DocumentsView />}
        {/* MeetingView 항상 마운트 — 탭 전환 시 state 보존 (E-19 연장) */}
        <div style={{
          display: chatTab === "meeting" ? "flex" : "none",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          minHeight: 0,
        }}>
          <MeetingView />
        </div>
        {chatTab === "settings" && <SettingsView />}
      </div>
    </div>
  );
}

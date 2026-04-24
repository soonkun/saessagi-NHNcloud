import { useEffect, useRef, useState } from "react";
import { Send, Mic, MicOff, X } from "lucide-react";
import clsx from "clsx";
import { useStore } from "../store";
import { send } from "../services/websocket";
import type { Position } from "../types";

const PANEL_W = 360;
const PANEL_H = 520;
const CHAR_SIZE = 120;
const GAP = 8;

interface ChatPanelProps {
  charPosition: Position;
}

function calcPanelStyle(charPos: Position): React.CSSProperties {
  // 채팅 패널은 캐릭터 위/왼쪽에 붙음
  let left = charPos.x + CHAR_SIZE - PANEL_W;
  let top = charPos.y - PANEL_H - GAP;

  // 화면 경계 보정
  if (left < 8) left = 8;
  if (left + PANEL_W > window.innerWidth - 8)
    left = window.innerWidth - 8 - PANEL_W;
  if (top < 8) top = charPos.y + CHAR_SIZE + GAP;

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

export function ChatPanel({ charPosition }: ChatPanelProps): React.ReactElement {
  const messages = useStore((s) => s.messages);
  const aiStatus = useStore((s) => s.aiStatus);
  const isMicOn = useStore((s) => s.isMicOn);
  const setChatOpen = useStore((s) => s.setChatOpen);

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 새 메시지 오면 하단 자동 스크롤
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 패널 열릴 때 입력 포커스
  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  function handleSend(): void {
    const text = input.trim();
    if (!text) return;
    send({ type: "user-message", text });
    setInput("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleMicToggle(): void {
    send({ type: isMicOn ? "interrupt-signal" : "user-message", text: "" });
    // 실제 마이크 상태는 서버가 control 메시지로 알려줌
  }

  const panelStyle = calcPanelStyle(charPosition);

  return (
    <div
      style={{
        ...panelStyle,
        zIndex: 999,
        background: "var(--color-panel)",
        backdropFilter: "blur(12px)",
        borderRadius: 12,
        border: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
      }}
    >
      {/* 상단 헤더 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid var(--color-border)",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            className={aiStatus !== "idle" ? "status-blink" : ""}
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: STATUS_COLOR[aiStatus] ?? "#888",
              display: "inline-block",
            }}
          />
          <span style={{ fontWeight: 600, fontSize: 14 }}>새싹이</span>
          <span style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
            {STATUS_LABEL[aiStatus] ?? ""}
          </span>
        </div>
        <button
          onClick={() => setChatOpen(false)}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-muted)",
            display: "flex",
            alignItems: "center",
            padding: 4,
            borderRadius: 4,
          }}
          title="닫기"
        >
          <X size={16} />
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
                maxWidth: "78%",
                padding: "8px 12px",
                borderRadius: msg.role === "human" ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
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

      {/* 하단 입력 */}
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
          title={isMicOn ? "마이크 끄기" : "마이크 켜기"}
          style={{
            background: isMicOn ? "var(--color-accent)" : "transparent",
            border: `1px solid ${isMicOn ? "var(--color-accent)" : "var(--color-border)"}`,
            borderRadius: 8,
            color: isMicOn ? "#fff" : "var(--color-text-muted)",
            cursor: "pointer",
            padding: "6px 8px",
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          {isMicOn ? <Mic size={16} /> : <MicOff size={16} />}
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

// suppress unused warning — clsx is used transitively in other components
void clsx;

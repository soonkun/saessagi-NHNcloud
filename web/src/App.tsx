import { useEffect, useRef } from "react";
import { useStore } from "./store";
import { connect } from "./services/websocket";
import {
  initClickthrough,
  type ClickthroughHandle,
} from "./services/clickthrough";
import { showStartupGreeting } from "./services/startup";
import { startReminderPoll } from "./services/reminder";
import { CharacterWidget } from "./components/CharacterWidget";
import { ChatPanel } from "./components/ChatPanel";

export function App(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const chatOpen = useStore((s) => s.chatOpen);
  const setChatOpen = useStore((s) => s.setChatOpen);
  const charPosition = useStore((s) => s.position);
  const charSize = useStore((s) => s.charSize);
  const clickthroughRef = useRef<ClickthroughHandle | null>(null);

  // WebSocket — reconnects when URL changes
  useEffect(() => {
    connect(wsUrl);
  }, [wsUrl]);

  // Clickthrough — initialized once on mount (M5 fix: separate effect with [])
  useEffect(() => {
    clickthroughRef.current = initClickthrough();
    return () => clickthroughRef.current?.dispose();
  }, []);

  // 시작 인사 + 일정 알림 폴링 — 한 번만
  useEffect(() => {
    void showStartupGreeting();
    const stopPoll = startReminderPoll();
    return stopPoll;
  }, []);

  // Wire tray "채팅 열기" → open chat panel (M4)
  useEffect(() => {
    if (!window.electronAPI?.onOpenChat) return;
    const unsub = window.electronAPI.onOpenChat(() => {
      setChatOpen(true);
    });
    return unsub;
  }, [setChatOpen]);

  // Close chat on mousedown outside the panel — no overlay div needed
  useEffect(() => {
    if (!chatOpen) return;
    function onMouseDown(e: MouseEvent): void {
      const panel = document.getElementById("chat-panel");
      const char = document.getElementById("char-widget");
      if (
        panel &&
        !panel.contains(e.target as Node) &&
        char &&
        !char.contains(e.target as Node)
      ) {
        setChatOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [chatOpen, setChatOpen]);

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative", pointerEvents: "none" }}>
      <CharacterWidget clickthroughHandle={clickthroughRef} />
      {chatOpen && <ChatPanel charPosition={charPosition} charSize={charSize} />}
    </div>
  );
}

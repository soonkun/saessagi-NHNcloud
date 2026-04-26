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

  // Electron pet 모드 초기화 — 앱 시작 시 즉시 투명 전체화면으로 전환
  useEffect(() => {
    if (!window.petMode) return;

    // pre-mode-changed 수신 → renderer-ready-for-mode-change 즉시 응답
    // (continueSetWindowModePet을 트리거하기 위해 필요)
    const ipc = (window as any).electron?.ipcRenderer;
    const ack = (_e: unknown, mode: string): void => {
      ipc?.send("renderer-ready-for-mode-change", mode);
    };
    ipc?.on("pre-mode-changed", ack);

    void window.petMode.enable();

    return () => {
      ipc?.removeListener("pre-mode-changed", ack);
    };
  }, []);

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
      const target = e.target as Node;
      const panel = document.getElementById("chat-panel");
      const char = document.getElementById("char-widget");
      if (
        panel &&
        !panel.contains(target) &&
        char &&
        !char.contains(target)
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
      {/* 항상 마운트 — display:none으로만 숨김으로써 MeetingView 등의 작업 state를 보존 */}
      <div style={{ display: chatOpen ? undefined : "none", pointerEvents: chatOpen ? undefined : "none" }}>
        <ChatPanel charPosition={charPosition} charSize={charSize} />
      </div>
    </div>
  );
}

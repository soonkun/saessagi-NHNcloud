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
import { DesktopView } from "./components/DesktopView";

export function App(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const chatOpen = useStore((s) => s.chatOpen);
  const setChatOpen = useStore((s) => s.setChatOpen);
  const charPosition = useStore((s) => s.position);
  const charSize = useStore((s) => s.charSize);
  const setPositionSilent = useStore((s) => s.setPositionSilent);
  const setWindowMode = useStore((s) => s.setWindowMode);
  const windowMode = useStore((s) => s.windowMode);
  const theme = useStore((s) => s.theme);

  // 테마 → documentElement data-theme 속성 반영
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 글씨 크기 배율 → --ui-scale (index.css의 --fs-* 토큰이 비례 확대)
  // 펫/데스크톱 모드별로 별도 저장된 값을 현재 모드에 따라 적용
  const uiScale = useStore((s) => (s.windowMode === "pet" ? s.uiScalePet : s.uiScaleDesktop));
  useEffect(() => {
    document.documentElement.style.setProperty("--ui-scale", String(uiScale));
  }, [uiScale]);

  // 드롭존 밖에 파일을 떨어뜨려도 Chromium이 file://로 내비게이션하지 않도록
  // 전역 차단. 각 드롭존의 onDrop은 버블링 전에 처리되므로 영향 없음.
  useEffect(() => {
    const prevent = (e: DragEvent): void => e.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);
  const clickthroughRef = useRef<ClickthroughHandle | null>(null);
  const charSizeRef = useRef(charSize);
  charSizeRef.current = charSize;

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

  // mode-changed IPC → store 동기화 + clickthrough/위치/채팅 패널 제어
  useEffect(() => {
    const ipc = (window as any).electron?.ipcRenderer;
    if (!ipc) return;
    const handler = (_e: unknown, mode: string): void => {
      if (mode !== "pet" && mode !== "window") return;
      setWindowMode(mode);
      if (mode === "window") {
        // click-through 비활성화 — 흰 배경 위에서도 클릭 가능하게
        clickthroughRef.current?.setEnabled(false);
        // 채팅 패널 자동 열기
        setChatOpen(true);
        // 캐릭터를 창 안에 보이는 위치로 이동 (localStorage 덮어쓰지 않음)
        const sz = charSizeRef.current;
        setPositionSilent({
          x: Math.max(8, window.innerWidth - sz - 24),
          y: Math.max(8, window.innerHeight - sz - 24),
        });
      } else {
        // pet 모드 복귀: click-through 재활성화 + localStorage 저장 위치 복원
        clickthroughRef.current?.setEnabled(true);
        // 채팅 패널 닫기 — 데스크탑 모드에서 열려있던 패널이 그대로 유지되면
        // 작은 화면을 덮어 캐릭터를 가림. 사용자가 캐릭터 클릭으로 다시 열게.
        setChatOpen(false);
        const saved = (() => {
          try { return JSON.parse(localStorage.getItem("saessagi_char_pos") ?? "null"); }
          catch { return null; }
        })() as { x: number; y: number } | null;
        if (saved) setPositionSilent(saved);
      }
    };
    ipc.on("mode-changed", handler);
    return () => ipc.removeListener("mode-changed", handler);
  }, [setWindowMode, setChatOpen, setPositionSilent]);

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

  // Close chat on mousedown outside the panel — pet 모드 한정
  // window 모드에서는 외부 클릭으로 닫으면 흰 화면만 남으므로 비활성화
  useEffect(() => {
    if (!chatOpen || windowMode === "window") return;
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
  }, [chatOpen, setChatOpen, windowMode]);

  if (windowMode === "window") {
    // 창 모드 — Gemini 스타일 전용 레이아웃. 펫 캐릭터·플로팅 패널은 숨김.
    return (
      <div style={{ width: "100vw", height: "100vh", position: "relative" }}>
        <DesktopView />
      </div>
    );
  }

  // 펫 모드 — 떠다니는 캐릭터 + 플로팅 채팅 패널
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

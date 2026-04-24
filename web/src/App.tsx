import { useEffect } from "react";
import { useStore } from "./store";
import { connect } from "./services/websocket";
import { Sidebar } from "./components/Sidebar";
import { CharacterWidget } from "./components/CharacterWidget";
import { ChatPanel } from "./components/ChatPanel";
import { CalendarView } from "./components/CalendarView";
import { DocumentsView } from "./components/DocumentsView";
import { SettingsView } from "./components/SettingsView";

function MainContent(): React.ReactElement {
  const activeView = useStore((s) => s.activeView);

  if (!activeView) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--color-text-muted)",
          fontSize: 15,
        }}
      >
        왼쪽 메뉴에서 항목을 선택하세요
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      {activeView === "calendar" && <CalendarView />}
      {activeView === "documents" && <DocumentsView />}
      {activeView === "settings" && <SettingsView />}
    </div>
  );
}

export function App(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const chatOpen = useStore((s) => s.chatOpen);
  const charPosition = useStore((s) => s.position);

  // 앱 마운트 시 WebSocket 자동 연결
  useEffect(() => {
    connect(wsUrl);
  }, [wsUrl]);

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "flex",
        overflow: "hidden",
        position: "relative",
      }}
    >
      {/* 좌측 사이드바 */}
      <Sidebar />

      {/* 메인 콘텐츠 */}
      <MainContent />

      {/* 캐릭터 위젯 (항상 표시) */}
      <CharacterWidget />

      {/* 채팅 패널 (토글) */}
      {chatOpen && <ChatPanel charPosition={charPosition} />}
    </div>
  );
}

import { useStore } from "../store";
import {
  MessageCircle,
  Calendar,
  FolderOpen,
  FileAudio,
  BookOpen,
  Settings,
  LayoutGrid,
  PanelLeftClose,
  Power,
} from "lucide-react";
import { ChatContent } from "./ChatPanel";
import { CalendarView } from "./CalendarView";
import { DocumentsView } from "./DocumentsView";
import { MeetingView } from "./MeetingView";
import { NotesView } from "./NotesView";
import { SettingsView } from "./SettingsView";
import type { ChatTab } from "../types";

const SIDEBAR_TABS: { id: ChatTab; label: string; Icon: React.ElementType }[] = [
  { id: "chat", label: "새싹이", Icon: MessageCircle },
  { id: "calendar", label: "일정표", Icon: Calendar },
  { id: "documents", label: "문서", Icon: FolderOpen },
  { id: "meeting", label: "회의록", Icon: FileAudio },
  { id: "notes", label: "업무 노트", Icon: BookOpen },
  { id: "settings", label: "설정", Icon: Settings },
];

const SAMPLE_PROMPTS = [
  { title: "오늘 한 업무 기록", body: "오늘 ⟨이 자료⟩로 ⟨이 업무⟩를 이렇게 처리했어" },
  { title: "지난 업무 검색", body: "출장비 정산은 어떻게 해?" },
  { title: "회의록 작성", body: "회의록 탭에서 음성 파일을 업로드해 자동 정리" },
  { title: "노트 그래프", body: "업무 노트 탭의 그래프에서 관련 업무 연결망 확인" },
];

export function DesktopView(): React.ReactElement {
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);
  const llmInfo = useStore((s) => s.llmInfo);
  const emotion = useStore((s) => s.emotion);

  const avatarSrc = `${import.meta.env.BASE_URL}avatars/${emotion}.png`;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        background: "var(--color-bg)",
        color: "var(--color-text)",
        pointerEvents: "auto",
      }}
    >
      {/* 좌측 사이드바 */}
      <aside
        style={{
          width: 240,
          flexShrink: 0,
          background: "var(--color-sidebar)",
          borderRight: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        {/* 새싹이 헤더 */}
        <div
          style={{
            padding: "14px 14px 12px",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: "rgba(100,140,220,0.15)",
              border: "1px solid rgba(100,140,220,0.35)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              overflow: "hidden",
              flexShrink: 0,
            }}
          >
            <img
              src={avatarSrc}
              alt="새싹이"
              style={{ width: "85%", height: "85%", objectFit: "contain" }}
              onError={(e) => {
                e.currentTarget.src = `${import.meta.env.BASE_URL}avatars/neutral.png`;
              }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>새싹이</div>
            {llmInfo && (
              <div
                style={{
                  fontSize: 10,
                  color: llmInfo.provider === "openai" ? "#10a37f" : "#7aa8ff",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={`${llmInfo.provider === "openai" ? "GPT" : "Ollama"} · ${llmInfo.model}`}
              >
                {llmInfo.provider === "openai" ? "GPT" : "Ollama"} · {llmInfo.model}
              </div>
            )}
          </div>
        </div>

        {/* 탭 메뉴 */}
        <nav style={{ flex: 1, padding: "10px 8px", overflowY: "auto" }}>
          {SIDEBAR_TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => setChatTab(id)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "9px 12px",
                marginBottom: 2,
                background:
                  chatTab === id ? "rgba(100,140,220,0.15)" : "transparent",
                border: "none",
                borderRadius: 8,
                color: chatTab === id ? "var(--color-accent)" : "var(--color-text)",
                cursor: "pointer",
                fontSize: 13,
                fontWeight: chatTab === id ? 600 : 400,
                textAlign: "left",
                transition: "background 0.12s",
              }}
            >
              <Icon size={15} style={{ flexShrink: 0 }} />
              {label}
            </button>
          ))}
        </nav>

        {/* 하단: 펫 모드 전환 + 종료 */}
        <div
          style={{
            borderTop: "1px solid var(--color-border)",
            padding: 10,
            display: "flex",
            gap: 6,
          }}
        >
          <button
            onClick={() => void window.petMode?.enable()}
            title="펫 모드로 전환"
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: "8px 10px",
              fontSize: 12,
            }}
          >
            <PanelLeftClose size={13} />
            펫 모드
          </button>
          <button
            onClick={() => window.electronAPI?.quit()}
            title="새싹이 종료"
            style={{
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: "8px 10px",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Power size={13} />
          </button>
        </div>
      </aside>

      {/* 메인 영역 — 모든 탭은 항상 마운트(상태 보존), display로 토글 */}
      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          background: "var(--color-bg)",
        }}
      >
        <div
          style={{
            display: chatTab === "chat" ? "flex" : "none",
            flex: 1,
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          <ChatContent emptyHero={<WelcomeHero />} />
        </div>
        {chatTab === "calendar" && <CalendarView />}
        {chatTab === "documents" && <DocumentsView />}
        <div
          style={{
            display: chatTab === "meeting" ? "flex" : "none",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
            minHeight: 0,
          }}
        >
          <MeetingView />
        </div>
        <div
          style={{
            display: chatTab === "notes" ? "flex" : "none",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
            minHeight: 0,
          }}
        >
          <NotesView />
        </div>
        {chatTab === "settings" && <SettingsView />}
      </main>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// 환영 화면 — chat 탭에서 메시지가 0개일 때 ChatContent의 emptyHero로 주입
// ────────────────────────────────────────────────────────────

function WelcomeHero(): React.ReactElement {
  const emotion = useStore((s) => s.emotion);
  const avatarSrc = `${import.meta.env.BASE_URL}avatars/${emotion}.png`;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "60px 24px 24px",
        gap: 28,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 14,
          maxWidth: 720,
        }}
      >
        <img
          src={avatarSrc}
          alt="새싹이"
          style={{ width: 88, height: 88, objectFit: "contain" }}
          onError={(e) => {
            e.currentTarget.src = `${import.meta.env.BASE_URL}avatars/neutral.png`;
          }}
        />
        <h1 style={{ fontSize: 26, fontWeight: 700, margin: 0 }}>
          안녕하세요, 새싹이예요
        </h1>
        <p
          style={{
            fontSize: 14,
            color: "var(--color-text-muted)",
            margin: 0,
            textAlign: "center",
            lineHeight: 1.7,
            maxWidth: 560,
          }}
        >
          오늘 처리하신 업무를 보고해 주세요. 자료를 첨부하고 상황을 설명하면
          <br />
          자동으로 정리해 업무 노트로 저장합니다. 비슷한 업무가 다시 들어올 때 꺼내 드릴게요.
        </p>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: 12,
          width: "100%",
          maxWidth: 720,
        }}
      >
        {SAMPLE_PROMPTS.map((p, i) => (
          <div
            key={i}
            style={{
              background: "var(--color-panel)",
              border: "1px solid var(--color-border)",
              borderRadius: 12,
              padding: "14px 16px",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
              <LayoutGrid
                size={13}
                style={{ marginRight: 6, verticalAlign: "-2px", opacity: 0.6 }}
              />
              {p.title}
            </div>
            <div style={{ fontSize: 12, color: "var(--color-text-muted)", lineHeight: 1.5 }}>
              {p.body}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

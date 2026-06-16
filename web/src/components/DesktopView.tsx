import { useEffect, useState } from "react";
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
  Minus,
  Square,
  X as XIcon,
  Copy as RestoreIcon,
  Sun,
  Moon,
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
  { id: "notes", label: "업무 노트", Icon: BookOpen },
  { id: "documents", label: "문서", Icon: FolderOpen },
  { id: "meeting", label: "회의록", Icon: FileAudio },
  { id: "settings", label: "설정", Icon: Settings },
];

const SAMPLE_PROMPTS = [
  { title: "오늘 한 업무 기록", body: "오늘 ⟨이 자료⟩로 ⟨이 업무⟩를 이렇게 처리했어" },
  { title: "이미지·스크린샷 정리", body: "화면 캡처를 붙여넣고 “노트로 정리해줘”" },
  { title: "사내 문서에서 답 찾기", body: "출장비 신청 절차 알려줘 (출처와 함께)" },
  { title: "지난 업무 다시 찾기", body: "지난번 LG 협의 결과 뭐였지?" },
  { title: "일정 등록·확인", body: "내일 오후 2시 팀 회의 잡아줘" },
  { title: "회의록 작성", body: "회의록 탭에서 음성 파일 업로드 → 한글 보고서까지" },
];

export function DesktopView(): React.ReactElement {
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);
  const llmInfo = useStore((s) => s.llmInfo);
  const emotion = useStore((s) => s.emotion);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);

  const avatarSrc = `${import.meta.env.BASE_URL}avatars/${emotion}.png`;

  // window 최대화 상태 추적 — 토글 아이콘 결정용
  const [isMaximized, setIsMaximized] = useState(false);
  useEffect(() => {
    const ipc = (window as { electron?: { ipcRenderer?: { on: (c: string, h: (...a: unknown[]) => void) => void; removeListener: (c: string, h: (...a: unknown[]) => void) => void } } }).electron?.ipcRenderer;
    if (!ipc) return;
    const handler = (_e: unknown, val: boolean): void => setIsMaximized(!!val);
    ipc.on("window-maximized-change", handler as (...a: unknown[]) => void);
    return () => ipc.removeListener("window-maximized-change", handler as (...a: unknown[]) => void);
  }, []);

  function sendWindowAction(channel: string): void {
    const ipc = (window as { electron?: { ipcRenderer?: { send: (c: string) => void } } }).electron?.ipcRenderer;
    ipc?.send(channel);
  }

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        background: "var(--color-bg)",
        color: "var(--color-text)",
        pointerEvents: "auto",
        // 데스크탑 가독성 향상 — base font-size 키움.
        // 자식 컴포넌트에서 em 단위를 쓰면 비례 확대, px 단위는 그대로.
        // (이전엔 zoom:1.5 사용했으나 ForceGraph2D 등 canvas hit testing과 충돌해 변경)
        fontSize: "var(--fs-16)",
      }}
    >
      {/* 상단 타이틀 바 — 드래그 영역 + 창 제어 버튼 */}
      <header
        style={{
          height: 36,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "var(--color-sidebar)",
          borderBottom: "1px solid var(--color-border)",
          // 전체를 드래그 가능 영역으로 — 아래에서 버튼만 no-drag
          // @ts-ignore — Electron 전용 CSS
          WebkitAppRegion: "drag",
        }}
      >
        {/* 왼쪽: 빈 공간 (macOS native traffic light 영역 회피용 패딩) */}
        <div style={{ width: 70, flexShrink: 0 }} />
        {/* 중앙: 타이틀 — 드래그 영역 안에 텍스트만 */}
        <div
          style={{
            flex: 1,
            textAlign: "center",
            fontSize: "var(--fs-13)",
            color: "var(--color-text-muted)",
            userSelect: "none",
          }}
        >
          새싹이 · AI 비서
        </div>
        {/* 오른쪽: 창 제어 버튼 */}
        <div
          style={{
            display: "flex",
            gap: 0,
            flexShrink: 0,
            // @ts-ignore
            WebkitAppRegion: "no-drag",
          }}
        >
          <TitleBarBtn onClick={() => sendWindowAction("window-minimize")} title="최소화">
            <Minus size={13} />
          </TitleBarBtn>
          <TitleBarBtn
            onClick={() => sendWindowAction("window-maximize")}
            title={isMaximized ? "복원" : "최대화"}
          >
            {isMaximized ? <RestoreIcon size={11} /> : <Square size={11} />}
          </TitleBarBtn>
          <TitleBarBtn
            onClick={() => sendWindowAction("window-close")}
            title="창 닫기 (앱은 트레이에 남음)"
            danger
          >
            <XIcon size={13} />
          </TitleBarBtn>
        </div>
      </header>

      {/* 본문: 사이드바 + 메인 영역 */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
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
            <div style={{ fontSize: "var(--fs-15)", fontWeight: 700 }}>새싹이</div>
            {llmInfo && (
              <div
                style={{
                  fontSize: "var(--fs-12)",
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
                fontSize: "var(--fs-15)",
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
              fontSize: "var(--fs-13)",
            }}
          >
            <PanelLeftClose size={14} />
            펫 모드
          </button>
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "라이트 모드로 전환" : "다크 모드로 전환"}
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
            {theme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
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
          <MeetingView desktop />
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
          <NotesView desktop />
        </div>
        {chatTab === "settings" && <SettingsView desktop />}
      </main>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// 타이틀바 버튼
// ────────────────────────────────────────────────────────────

function TitleBarBtn({
  onClick,
  title,
  children,
  danger = false,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
  danger?: boolean;
}): React.ReactElement {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 44,
        height: 36,
        background: hover
          ? danger
            ? "#e53935"
            : "rgba(255,255,255,0.08)"
          : "transparent",
        border: "none",
        cursor: "pointer",
        color: hover && danger ? "#fff" : "var(--color-text)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "background 0.12s, color 0.12s",
      }}
    >
      {children}
    </button>
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
        <h1 style={{ fontSize: "var(--fs-26)", fontWeight: 700, margin: 0 }}>
          안녕하세요, 새싹이예요
        </h1>
        <p
          style={{
            fontSize: "var(--fs-14)",
            color: "var(--color-text-muted)",
            margin: 0,
            textAlign: "center",
            lineHeight: 1.7,
            maxWidth: 560,
          }}
        >
          오늘 처리하신 업무를 알려주세요. 자료를 첨부하거나 화면 캡처도 좋아요.
          <br />
          상황을 설명해주시면 내용을 정리해서 노트로 저장할게요.
          <br />
          제 지식이 늘어날수록 주인님의 업무가 편해질거예요.
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
            <div style={{ fontSize: "var(--fs-13)", fontWeight: 600, marginBottom: 6 }}>
              <LayoutGrid
                size={13}
                style={{ marginRight: 6, verticalAlign: "-2px", opacity: 0.6 }}
              />
              {p.title}
            </div>
            <div style={{ fontSize: "var(--fs-12)", color: "var(--color-text-muted)", lineHeight: 1.5 }}>
              {p.body}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

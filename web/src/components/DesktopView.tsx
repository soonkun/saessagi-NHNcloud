import { useEffect, useState, lazy, Suspense } from "react";
import { useStore, isElectronRuntime } from "../store";
import { startNewHistoryIfSafe } from "../services/history";
import {
  Calendar,
  PanelLeftClose,
  Power,
  X as XIcon,
  Sun,
  Moon,
  LogOut,
  Sprout,
  Menu,
} from "lucide-react";
import { fetchAuthEnabled, logout } from "../services/api";
import { useIsCompact, useIsNarrow } from "../hooks/useMediaQuery";
import { ChatContent } from "./ChatPanel";
import { CalendarView } from "./CalendarView";
import { DeepResearchView } from "./DeepResearchView";
import { AdminGate } from "./AdminGate";
import { DocumentsView } from "./DocumentsView";
import { FloatingAvatar } from "./FloatingAvatar";
import { HistoryList } from "./HistoryList";
import { MeetingView } from "./MeetingView";
import { NotesView } from "./NotesView";
import { SettingsView } from "./SettingsView";
import { CHAT_TABS } from "../chatTabs";

// ForceGraph2D 번들이 커서 lazy — ChatPanel과 동일 패턴 (청크 공유)
const GraphRagView = lazy(() => import("./GraphRagView"));

const SIDEBAR_TABS = CHAT_TABS.map(({ id, desktopLabel, Icon }) => ({
  id,
  label: desktopLabel,
  Icon,
}));

/** 좁은 화면에서 사이드바 서랍을 여는 버튼. 손가락 대상 크기(44×40)를 지킨다. */
export function MenuButton({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}): React.ReactElement {
  return (
    <button
      onClick={onToggle}
      title={open ? "메뉴 닫기" : "메뉴 열기"}
      aria-label="메뉴"
      style={{
        width: 44,
        height: 40,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "transparent",
        border: "none",
        color: "var(--color-text)",
        cursor: "pointer",
      }}
    >
      {open ? <XIcon size={20} /> : <Menu size={20} />}
    </button>
  );
}

export function DesktopView(): React.ReactElement {
  const chatTab = useStore((s) => s.chatTab);
  const setChatTab = useStore((s) => s.setChatTab);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const avatarVisible = useStore((s) => s.avatarVisible);
  const toggleAvatarVisible = useStore((s) => s.toggleAvatarVisible);

  // 네이티브 전용 UI(창 제어·펫 모드·앱 종료) 노출 여부. 브라우저에서는 전부 감춘다.
  const isElectron = isElectronRuntime();

  // CR-43: 좁은 화면(태블릿·분할창)에서는 사이드바를 서랍으로 접어 본문 폭을 확보한다.
  const isNarrow = useIsNarrow();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 넓어지면 서랍 상태를 초기화한다 — 안 하면 다시 좁아질 때 열린 채로 뜬다.
  useEffect(() => {
    if (!isNarrow) setDrawerOpen(false);
  }, [isNarrow]);

  // 인증이 꺼진 배포에서 로그아웃 버튼을 보여주면 누른 뒤 의미 없는 로그인 화면에 갇힌다.
  const [authEnabled, setAuthEnabled] = useState(false);
  useEffect(() => {
    void fetchAuthEnabled().then(setAuthEnabled);
  }, []);


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
      {/* CR-55: 상단 타이틀 바를 없앴다.
          로고·모델명만 있던 줄인데, 히어로·떠 있는 캐릭터와 합쳐 **같은 캐릭터가 크기만
          다르게 셋** 보여 산만했다(사용자 지적). 햄버거는 아래 줄로 내리고 모델명은
          상태줄로 옮겼다. 창 제어 버튼은 Electron 전용이라 CR-38 이후 쓰이지 않는다.

          채팅 탭은 ChatContent의 상태줄이 햄버거를 안고 있고, 나머지 탭은 여기서
          좁은 화면에 한해 최소 바를 그린다 — 그래야 어느 탭에서도 메뉴를 열 수 있다. */}
      {isNarrow && chatTab !== "chat" && (
        <div
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 6px",
            background: "var(--color-sidebar)",
            borderBottom: "1px solid var(--color-border)",
          }}
        >
          <MenuButton open={drawerOpen} onToggle={() => setDrawerOpen((o) => !o)} />
          <span style={{ fontSize: "var(--fs-14)", fontWeight: 600 }}>
            {SIDEBAR_TABS.find((t) => t.id === chatTab)?.label ?? ""}
          </span>
        </div>
      )}

      {/* 본문: 사이드바 + 메인 영역 */}
      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
      {/* 좁은 화면에서 서랍이 열렸을 때의 배경 — 바깥을 누르면 닫힌다 */}
      {isNarrow && drawerOpen && (
        <div
          onClick={() => setDrawerOpen(false)}
          style={{
            position: "absolute",
            inset: 0,
            background: "rgba(0,0,0,0.4)",
            zIndex: 40,
          }}
        />
      )}
      {/* 좌측 사이드바 — 좁은 화면에서는 겹쳐 뜨는 서랍이 된다.
          240px 고정이던 탓에 600px 태블릿에서 본문이 360px만 남았다 (CR-43). */}
      <aside
        style={{
          width: 240,
          flexShrink: 0,
          background: "var(--color-sidebar)",
          borderRight: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          ...(isNarrow
            ? {
                position: "absolute",
                top: 0,
                bottom: 0,
                left: 0,
                zIndex: 41,
                transform: drawerOpen ? "translateX(0)" : "translateX(-100%)",
                transition: "transform 0.2s ease",
                boxShadow: drawerOpen ? "2px 0 12px rgba(0,0,0,0.25)" : "none",
              }
            : {}),
        }}
      >
        {/* 탭 메뉴 */}
        <nav style={{ flexShrink: 0, padding: "10px 8px" }}>
          {SIDEBAR_TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              onClick={() => {
                // "새 대화" 탭: 대화가 있고 생성 중이 아닐 때만 새 히스토리 시작.
                // (빈 대화거나 답변 생성 중이면 이동만 — E-75)
                if (id === "chat") startNewHistoryIfSafe();
                setChatTab(id);
                setDrawerOpen(false); // 좁은 화면: 선택하면 서랍을 닫아 본문을 보여준다
              }}
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

        {/* CR-23: 대화방 히스토리 — 최신 대화부터 (ChatGPT 스타일) */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            borderTop: "1px solid var(--color-border)",
          }}
        >
          <div
            style={{
              fontSize: "var(--fs-11)",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              padding: "10px 16px 2px",
              letterSpacing: "0.06em",
              flexShrink: 0,
            }}
          >
            대화
          </div>
          <HistoryList />
        </div>

        {/* 하단: 펫 모드 전환 + 종료 */}
        <div
          style={{
            borderTop: "1px solid var(--color-border)",
            padding: 10,
            display: "flex",
            gap: 6,
          }}
        >
          {/* 펫 모드·앱 종료는 Electron 창을 조작하는 기능이라 브라우저에선 숨긴다 (CR-38).
              테마 토글만 남으면 혼자 폭을 차지하도록 flex:1을 넘겨준다. */}
          {isElectron && (
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
          )}
          {/* CR-55: 떠 있는 새싹이 켜기/끄기. 데스크톱에서 잠깐 치우고 싶을 때 쓴다. */}
          <button
            onClick={toggleAvatarVisible}
            title={avatarVisible ? "새싹이 숨기기" : "새싹이 보이기"}
            aria-label={avatarVisible ? "새싹이 숨기기" : "새싹이 보이기"}
            style={{
              width: 36,
              height: 36,
              flexShrink: 0,
              justifyContent: "center",
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: avatarVisible ? "var(--color-accent)" : "var(--color-text-muted)",
              cursor: "pointer",
              padding: 0,
              display: "flex",
              alignItems: "center",
            }}
          >
            {/* 캐릭터 그림을 쓰면 히어로·떠 있는 캐릭터와 함께 **같은 얼굴이 크기만 다르게
                여럿** 보인다 — 사용자가 지적한 바로 그 산만함이다. 새싹 아이콘으로 대신한다. */}
            <Sprout size={17} strokeWidth={avatarVisible ? 2.4 : 1.8} />
          </button>

          {/* 테마 전환은 가끔 쓰는 기능이라 아이콘만. 자리는 로그아웃에 양보한다. */}
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "라이트 모드로 전환" : "다크 모드로 전환"}
            aria-label={theme === "dark" ? "라이트 모드로 전환" : "다크 모드로 전환"}
            style={{
              width: 36,
              height: 36,
              flexShrink: 0,
              justifyContent: "center",
              background: "transparent",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              color: "var(--color-text-muted)",
              cursor: "pointer",
              padding: 0,
              display: "flex",
              alignItems: "center",
            }}
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          {authEnabled && (
            <button
              onClick={() => void logout()}
              title="로그아웃 — 세션을 끊고 로그인 화면으로"
              style={{
                flex: 1,
                gap: 6,
                justifyContent: "center",
                background: "transparent",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                color: "var(--color-text-muted)",
                cursor: "pointer",
                padding: "8px 10px",
                fontSize: "var(--fs-13)",
                display: "flex",
                alignItems: "center",
              }}
            >
              <LogOut size={14} />
              로그아웃
            </button>
          )}
          {isElectron && (
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
          )}
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
          <ChatContent
            emptyHero={<WelcomeHero />}
            leading={
              isNarrow ? (
                <MenuButton open={drawerOpen} onToggle={() => setDrawerOpen((o) => !o)} />
              ) : undefined
            }
          />
        </div>
        {chatTab === "calendar" && <CalendarView />}
        {chatTab === "documents" && (
          <AdminGate label="문서 관리">
            <DocumentsView />
          </AdminGate>
        )}
        {chatTab === "graph" && (
          <Suspense
            fallback={
              <div style={{ padding: 20, fontSize: "var(--fs-12)", color: "var(--color-text-muted)" }}>
                그래프 로딩 중...
              </div>
            }
          >
            <GraphRagView />
          </Suspense>
        )}
        {/* DeepResearchView 항상 마운트 — 진행 중 리서치 state 보존 (E-19/E-20) */}
        <div
          style={{
            display: chatTab === "research" ? "flex" : "none",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
            minHeight: 0,
          }}
        >
          <DeepResearchView desktop />
        </div>
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
      {/* CR-47 — 화면 위에 떠 있는 새싹이. 탭과 무관하게 항상 같은 자리에 있어야 하므로
          최상위에 둔다(탭 안에 두면 탭을 옮길 때마다 사라졌다 나타난다). */}
      <FloatingAvatar />
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// 타이틀바 버튼
// ────────────────────────────────────────────────────────────

function WelcomeHero(): React.ReactElement {
  const emotion = useStore((s) => s.emotion);
  const startupBriefing = useStore((s) => s.startupBriefing);
  const avatarVisible = useStore((s) => s.avatarVisible);
  const isCompact = useIsCompact();
  const avatarSrc = `${import.meta.env.BASE_URL}avatars/${emotion}.png`;
  // CR-55: 화면에 새싹이는 **하나만** 둔다. 떠 있는 캐릭터가 보이는 상태면
  // 첫 화면 그림은 생략한다 — 같은 얼굴이 크기만 다르게 둘 보이면 산만하다는 지적.
  // 좁은 화면·숨김 상태에서는 떠 있는 쪽이 없으므로 여기서 보여준다.
  const showHeroAvatar = isCompact || !avatarVisible;

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
        {showHeroAvatar && (
          <img
            src={avatarSrc}
            alt="새싹이"
            style={{ width: 88, height: 88, objectFit: "contain" }}
            onError={(e) => {
              e.currentTarget.src = `${import.meta.env.BASE_URL}avatars/neutral.png`;
            }}
          />
        )}
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
        {startupBriefing && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: "rgba(100,140,220,0.08)",
              border: "1px solid rgba(100,140,220,0.28)",
              borderRadius: 10,
              padding: "10px 16px",
              fontSize: "var(--fs-13)",
              lineHeight: 1.6,
              maxWidth: 560,
            }}
          >
            <Calendar size={14} style={{ color: "var(--color-accent)", flexShrink: 0 }} />
            <span>{startupBriefing}</span>
          </div>
        )}
      </div>

    </div>
  );
}

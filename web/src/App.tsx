import { useEffect } from "react";
import { useAppHeight } from "./hooks/useAppHeight";
import { useStore } from "./store";
import { connect } from "./services/websocket";
import { fetchLlmProvider } from "./services/api";
import { showStartupGreeting } from "./services/startup";
import { startReminderPoll } from "./services/reminder";
import { DesktopView } from "./components/DesktopView";

/**
 * 앱 루트.
 *
 * CR-38에서 Electron을 제거하면서 펫 모드(투명 창 위 떠다니는 캐릭터)와 그에 딸린
 * 클릭 관통·창 모드 전환 IPC가 전부 사라졌다. 이제 화면은 데스크탑 레이아웃 하나뿐이다.
 */
export function App(): React.ReactElement {
  const wsUrl = useStore((s) => s.wsUrl);
  const theme = useStore((s) => s.theme);
  const uiScale = useStore((s) => s.uiScaleDesktop);

  // 테마 → documentElement data-theme 속성 반영
  // 보이는 영역에 앱 높이를 맞춘다 (모바일 크롬 상단 잘림, E-76)
  useAppHeight();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // 글씨 크기 배율 → --ui-scale (index.css의 --fs-* 토큰이 비례 확대)
  useEffect(() => {
    document.documentElement.style.setProperty("--ui-scale", String(uiScale));
  }, [uiScale]);

  // 드롭존 밖에 파일을 떨어뜨려도 브라우저가 그 파일로 내비게이션하지 않도록 전역 차단.
  // 각 드롭존의 onDrop은 버블링 전에 처리되므로 영향 없음.
  useEffect(() => {
    const prevent = (e: DragEvent): void => e.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  // WebSocket — 주소가 바뀌면 재연결
  useEffect(() => {
    connect(wsUrl);
  }, [wsUrl]);

  // 사용 중인 모델 정보를 시작할 때 한 번 읽는다 (CR-55).
  // 예전에는 설정 탭을 열어야만 채워져, 상태줄의 모델명이 첫 화면에서 비어 있었다.
  useEffect(() => {
    void fetchLlmProvider()
      .then((s) => {
        if (!s) return;
        const provider = s.provider === "openai" ? "openai" : "ollama";
        useStore.getState().setLlmInfo({
          provider,
          model: provider === "openai" ? s.openai_model : s.ollama_model,
        });
      })
      .catch(() => {
        /* 모델명 표시는 부가 정보다 — 실패해도 앱 동작에는 영향 없음 */
      });
  }, []);

  // 시작 인사 + 일정 알림 폴링 — 한 번만
  useEffect(() => {
    void showStartupGreeting();
    const stopPoll = startReminderPoll();
    return stopPoll;
  }, []);

  return (
    // 100vw/100vh를 쓰면 안 된다. 모바일에서 100vh는 **주소창이 숨겨진** 큰 뷰포트라
    // 실제 보이는 높이보다 커지고, 그만큼 화면 밖으로 밀려 잘린다(모바일 크롬에서
    // 상단 두 줄이 사라진 실제 원인, E-76). #root가 이미 보이는 높이에 맞춰져 있으므로
    // 100%로 채우면 된다. 100vw도 스크롤바 폭까지 세어 가로 넘침을 만든다.
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <DesktopView />
    </div>
  );
}

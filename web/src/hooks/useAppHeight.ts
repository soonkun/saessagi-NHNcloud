// web/src/hooks/useAppHeight.ts
/**
 * 앱 높이를 **실제로 보이는 영역**에 맞춘다 (E-76).
 *
 * 모바일 브라우저마다 주소창·툴바를 세는 방식이 달라, CSS 뷰포트 단위만 믿으면 어긋난다.
 * 실제로 같은 페이지가 네이버 브라우저에서는 멀쩡한데 **모바일 크롬에서는 상단 두 줄
 * (타이틀 바·상태줄)이 잘려** 햄버거 메뉴에 손이 닿지 않았다. `100%`→`100dvh`→`100svh`로
 * 옮겨가며 고쳐 봤지만 브라우저마다 결과가 달랐다.
 *
 * 그래서 단위를 믿는 대신 `visualViewport`로 **지금 보이는 높이를 직접 재서** 쓴다.
 * 이 값은 브라우저가 툴바를 접든 펴든, 키보드가 올라오든 항상 사실을 말해준다.
 * (키보드가 뜨면 앱이 그만큼 줄어 입력창이 가려지지 않는 부수 효과도 있다.)
 */
import { useEffect } from "react";

/** CSS에서 `height: var(--app-height, 100%)`로 쓴다. */
export const APP_HEIGHT_VAR = "--app-height";

/** 보이는 영역이 레이아웃 안에서 아래로 밀린 정도(px). 키보드가 뜰 때 0이 아니게 된다. */
export const APP_OFFSET_VAR = "--app-offset";

export function measuredHeight(): number {
  const vv = typeof window !== "undefined" ? window.visualViewport : null;
  // visualViewport가 없는 환경(구형·일부 웹뷰)은 innerHeight로 물러선다.
  return Math.round(vv?.height ?? window.innerHeight);
}

export function useAppHeight(): void {
  useEffect(() => {
    const apply = (): void => {
      const root = document.documentElement;
      root.style.setProperty(APP_HEIGHT_VAR, `${measuredHeight()}px`);
      // 키보드가 올라오면 iOS는 보이는 영역(visual viewport)을 레이아웃 안에서 아래로
      // 밀어낸다. 앱은 레이아웃 기준으로 고정돼 있어 그만큼 화면 위로 사라지고, 아래에는
      // 빈 영역이 남는다 — 사용자에게는 "본문이 확 올라가고 흰 화면"으로 보인다 (E-82).
      // 밀린 만큼 앱을 같이 내려 항상 보이는 영역에 맞춘다.
      const vv = window.visualViewport;
      root.style.setProperty(APP_OFFSET_VAR, `${Math.round(vv?.offsetTop ?? 0)}px`);
    };
    apply();

    // 키보드가 열리는 동안 여러 번 다시 잰다 (E-83).
    //
    // 이벤트만 믿으면 안 된다. 카카오톡 인앱 브라우저에서는 입력창을 눌러 키보드가 올라와도
    // resize/scroll이 제때 오지 않아 앱이 예전 높이(키보드 없는 높이)로 남았고, 입력바가
    // 키보드 아래로 밀려 보이지 않았다. 사용자가 화면을 손가락으로 건드리자 그제야
    // 이벤트가 발생해 정상 배치가 됐다 — 즉 계산은 맞고 **호출 시점**이 문제였다.
    // 키보드 애니메이션(약 0.3초)을 덮도록 잠깐 반복 측정한다.
    let burst: number | null = null;
    const remeasure = (): void => {
      if (burst !== null) window.clearInterval(burst);
      let n = 0;
      burst = window.setInterval(() => {
        apply();
        if (++n >= 12) {
          // 1.2초면 키보드 애니메이션이 끝난다. 계속 돌리면 배터리만 먹는다.
          if (burst !== null) window.clearInterval(burst);
          burst = null;
        }
      }, 100);
    };

    const vv = window.visualViewport;
    // resize만으로는 부족하다 — iOS는 툴바가 접힐 때 scroll 이벤트로 알린다.
    vv?.addEventListener("resize", apply);
    vv?.addEventListener("scroll", apply);
    window.addEventListener("resize", apply);
    window.addEventListener("orientationchange", apply);
    // 입력에 포커스가 들어오고 나갈 때가 곧 키보드가 열리고 닫히는 순간이다.
    document.addEventListener("focusin", remeasure);
    document.addEventListener("focusout", remeasure);
    return () => {
      if (burst !== null) window.clearInterval(burst);
      vv?.removeEventListener("resize", apply);
      vv?.removeEventListener("scroll", apply);
      window.removeEventListener("resize", apply);
      window.removeEventListener("orientationchange", apply);
      document.removeEventListener("focusin", remeasure);
      document.removeEventListener("focusout", remeasure);
    };
  }, []);
}

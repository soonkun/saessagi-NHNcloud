/**
 * 화면 폭 감지 (CR-43).
 *
 * 태블릿에서 UI가 잘리던 원인은 사이드바가 240px로 고정돼 있어서였다. 폭 600px 태블릿에서
 * 본문이 360px만 남고, 540px 이하에서는 채팅 입력창이 화면 밖으로 밀려났다.
 *
 * JS 이벤트(resize) 대신 matchMedia를 쓴다 — 브라우저가 조건 변화만 알려주므로
 * 리사이즈 중 불필요한 리렌더가 없다.
 */
import { useEffect, useState } from "react";

/** 이 폭 미만이면 사이드바를 서랍(overlay)으로 접는다. */
export const NARROW_MAX_WIDTH = 1000;

/** 이 폭 미만이면 본문 내부의 보조 패널도 세로로 쌓는다(그래프 상세 등). */
export const COMPACT_MAX_WIDTH = 700;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (!window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent): void => setMatches(e.matches);
    setMatches(mql.matches); // query가 바뀐 경우 즉시 반영
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** 태블릿·좁은 창 — 사이드바를 서랍으로 접어야 하는 폭. */
export function useIsNarrow(): boolean {
  return useMediaQuery(`(max-width: ${NARROW_MAX_WIDTH - 1}px)`);
}

/** 휴대폰·아주 좁은 창 — 보조 패널까지 세로로 쌓아야 하는 폭. */
export function useIsCompact(): boolean {
  return useMediaQuery(`(max-width: ${COMPACT_MAX_WIDTH - 1}px)`);
}

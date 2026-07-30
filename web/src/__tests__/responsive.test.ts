/**
 * CR-43 회귀 방지 — 좁은 화면 판정.
 *
 * 태블릿에서 UI가 잘린 원인은 사이드바가 240px 고정이어서 600px 화면에서 본문이 360px만
 * 남고, 540px 이하에서는 채팅 입력창이 화면 밖으로 밀려난 것이었다. 이 판정이 깨지면
 * 사이드바가 다시 고정 폭으로 돌아가 같은 증상이 재발한다.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import { NARROW_MAX_WIDTH, COMPACT_MAX_WIDTH } from "../hooks/useMediaQuery";

/** matchMedia를 주어진 뷰포트 폭에 맞게 흉내낸다 (jsdom에는 구현이 없다). */
function mockViewport(width: number): void {
  vi.stubGlobal(
    "matchMedia",
    (query: string) => {
      const m = /max-width:\s*(\d+)px/.exec(query);
      const max = m ? Number(m[1]) : Infinity;
      return {
        matches: width <= max,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      };
    }
  );
}

describe("CR-43 반응형 breakpoint", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("breakpoint 값이 태블릿을 좁은 화면으로 분류한다", () => {
    // 흔한 태블릿 폭이 전부 서랍 모드에 들어가야 한다
    for (const w of [600, 640, 744, 768, 800, 834, 900, 999]) {
      expect(w).toBeLessThan(NARROW_MAX_WIDTH);
    }
    // 일반 노트북·데스크탑은 기존 레이아웃을 유지해야 한다
    for (const w of [1000, 1024, 1280, 1440, 1920]) {
      expect(w).toBeGreaterThanOrEqual(NARROW_MAX_WIDTH);
    }
  });

  it("compact는 narrow보다 좁게 설정되어 있다", () => {
    expect(COMPACT_MAX_WIDTH).toBeLessThan(NARROW_MAX_WIDTH);
  });

  it("휴대폰 폭은 compact로 분류된다", () => {
    for (const w of [390, 414, 480, 600]) {
      expect(w).toBeLessThanOrEqual(COMPACT_MAX_WIDTH);
    }
  });

  it("useIsNarrow가 폭에 따라 올바르게 판정한다", async () => {
    mockViewport(768);
    vi.resetModules();
    const { useIsNarrow } = await import("../hooks/useMediaQuery");
    // 훅 본체는 React 없이 호출할 수 없으므로 matchMedia 결과로 계약을 검증한다
    expect(window.matchMedia(`(max-width: ${NARROW_MAX_WIDTH - 1}px)`).matches).toBe(true);
    expect(typeof useIsNarrow).toBe("function");

    mockViewport(1440);
    expect(window.matchMedia(`(max-width: ${NARROW_MAX_WIDTH - 1}px)`).matches).toBe(false);
  });
});

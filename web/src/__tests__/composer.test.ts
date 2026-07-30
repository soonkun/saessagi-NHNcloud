// web/src/__tests__/composer.test.ts
/**
 * CR-48 입력창 자동 높이 — ChatGPT처럼 늘어나다 일정 줄 수에서 멈추고 스크롤.
 *
 * 상한이 없으면 긴 글을 붙여넣었을 때 입력창이 화면을 다 덮어 대화가 안 보인다.
 */
import { describe, it, expect } from "vitest";

import { composerHeight, LINE_HEIGHT, MAX_ROWS, TEXTAREA_PAD } from "../components/ChatPanel";

const MAX = MAX_ROWS * LINE_HEIGHT + TEXTAREA_PAD;

describe("composerHeight", () => {
  it("한 줄이면 그 높이를 그대로 쓴다", () => {
    expect(composerHeight(LINE_HEIGHT + TEXTAREA_PAD)).toBe(LINE_HEIGHT + TEXTAREA_PAD);
  });

  it("상한 전까지는 내용에 따라 늘어난다", () => {
    const three = 3 * LINE_HEIGHT + TEXTAREA_PAD;
    expect(composerHeight(three)).toBe(three);
  });

  it("7줄이 상한 — 그보다 길어도 높이가 고정된다(넘치면 스크롤)", () => {
    expect(composerHeight(MAX)).toBe(MAX);
    expect(composerHeight(MAX + 500)).toBe(MAX);
    expect(composerHeight(99999)).toBe(MAX);
  });

  it("상한이 화면을 덮을 만큼 크지 않다 — 작은 노트북(700px)에서도 절반 이하", () => {
    expect(MAX).toBeLessThan(700 / 2);
  });
});

// web/src/__tests__/history-guard.test.ts
/**
 * "새 대화" 시작 조건 (E-75).
 *
 * 답변 생성 중에 히스토리를 바꾸면 뒤늦게 도착한 답변이 새 히스토리에 저장되어 질문과
 * 답이 갈라진다. 실제로 "안녕"만 든 히스토리와 "안녕하세요! 무엇을 도와드릴까요? 😊"만
 * 든 히스토리가 따로 남아, 목록에 인사말짜리 대화가 생겼다.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const sent: unknown[] = [];
vi.mock("../services/websocket", () => ({
  send: (m: unknown) => { sent.push(m); },
}));

const state = { messages: [] as unknown[], aiStatus: "idle" as string };
vi.mock("../store", () => ({
  useStore: { getState: () => state },
}));

const { startNewHistoryIfSafe } = await import("../services/history");

beforeEach(() => {
  sent.length = 0;
  state.messages = [];
  state.aiStatus = "idle";
});

describe("startNewHistoryIfSafe", () => {
  it("대화가 있고 대기 중이면 새 히스토리를 만든다", () => {
    state.messages = [{}];
    expect(startNewHistoryIfSafe()).toBe(true);
    expect(sent).toEqual([{ type: "create-new-history" }]);
  });

  it("답변 생성 중에는 만들지 않는다 — 답이 새 히스토리로 갈라진다", () => {
    state.messages = [{}];
    state.aiStatus = "thinking";
    expect(startNewHistoryIfSafe()).toBe(false);
    expect(sent).toEqual([]);
  });

  it("말하는 중에도 만들지 않는다", () => {
    state.messages = [{}];
    state.aiStatus = "speaking";
    expect(startNewHistoryIfSafe()).toBe(false);
    expect(sent).toEqual([]);
  });

  it("이미 빈 대화면 만들지 않는다 — 빈 히스토리가 쌓인다", () => {
    expect(startNewHistoryIfSafe()).toBe(false);
    expect(sent).toEqual([]);
  });
});

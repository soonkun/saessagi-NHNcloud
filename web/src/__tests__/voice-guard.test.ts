// web/src/__tests__/voice-guard.test.ts
/**
 * STT 무음 환각 방지 (E-73 조사 중 발견).
 *
 * Whisper는 무음을 받으면 학습 데이터(자막)에 흔한 문구를 지어낸다. 실측: 완전한 무음
 * WAV(RMS 0)에 "다음 영상에서 만나요."를 반환했다. 음성 입력은 인식 결과를 **곧바로
 * 대화로 전송**하므로, 말하지 않고 마이크를 끄면 엉뚱한 메시지가 그대로 나간다.
 */
import { describe, it, expect } from "vitest";

import { isHallucination } from "../services/voice";

describe("isHallucination", () => {
  it("실측된 무음 환각 문구를 걸러낸다", () => {
    expect(isHallucination("다음 영상에서 만나요.")).toBe(true);
    expect(isHallucination(" 다음 영상에서 만나요.")).toBe(true); // 앞 공백 포함(서버 원문)
  });

  it("자막 상투구도 걸러낸다", () => {
    expect(isHallucination("시청해 주셔서 감사합니다")).toBe(true);
    expect(isHallucination("구독과 좋아요~")).toBe(true);
  });

  it("실제 업무 발화는 통과시킨다 — 과하게 막으면 기능이 무의미해진다", () => {
    expect(isHallucination("내일 오후 2시 팀 회의 잡아줘")).toBe(false);
    expect(isHallucination("기후변화 과제 자료 찾아줘")).toBe(false);
    expect(isHallucination("다음 영상에서 만나요 라고 자막을 넣어줘")).toBe(false);
    expect(isHallucination("감사합니다만 다시 확인해 주세요")).toBe(false);
  });

  it("빈 문자열은 환각이 아니다(별도 경로에서 처리)", () => {
    expect(isHallucination("")).toBe(false);
  });
});

// web/src/services/history.ts
/**
 * "새 대화" 시작 규칙 한 곳 (E-75).
 *
 * 답변이 생성되는 중에 히스토리를 바꾸면, 뒤늦게 도착한 답변이 **새 히스토리**에 저장되어
 * 질문과 답이 갈라진다. 실제로 "안녕"만 든 히스토리와 "안녕하세요! 무엇을 도와드릴까요?"만
 * 든 히스토리가 따로 남아, 목록에 인사말짜리 대화가 생겼다.
 * 모바일에서는 서랍의 "새 대화"가 채팅 화면으로 돌아가는 통로이기도 해서 더 쉽게 눌린다.
 */
import { useStore } from "../store";
import { send } from "./websocket";

/** 새 히스토리를 시작해도 되는 상황이면 시작한다. 반환값은 실제로 시작했는지 여부. */
export function startNewHistoryIfSafe(): boolean {
  const s = useStore.getState();
  if (s.messages.length === 0) return false; // 이미 빈 대화 — 만들 필요 없음
  if (s.aiStatus !== "idle") return false; // 생성 중 — 바꾸면 답변이 갈라진다
  send({ type: "create-new-history" });
  return true;
}

// 앱 시작 시 오늘 일정을 가져와 랜덤 인사 메시지를 채팅에 추가하고 TTS로 재생
import { useStore } from "../store";
import type { CalendarEvent } from "../types";
import { API_BASE } from "./api";
import { speak } from "./tts";

const WITH_EVENTS: Array<(events: CalendarEvent[]) => string> = [
  (ev) => `안녕하세요! 오늘 ${_fmt(ev[0])}에 ${ev[0].title}이(가) 있어요. ${ev.length > 1 ? `그 외 ${ev.length - 1}개 일정도 있고요. ` : ""}잊지 마세요~`,
  (ev) => `좋은 하루예요! 오늘 일정 알려드릴게요. ${_list(ev)}`,
  (ev) => `어서오세요! 오늘 ${ev.length}개 일정이 있어요. ${_list(ev)}`,
  (ev) => `반가워요! 오늘은 바쁜 날이네요. ${_list(ev)} 화이팅!`,
  (ev) => `안녕하세요! 오늘 ${_fmt(ev[0])}에 ${ev[0].title} 일정 있는 날이에요. 잘 부탁드려요!`,
];

const WITHOUT_EVENTS: string[] = [
  "안녕하세요! 오늘은 일정이 없는 날이에요. 힘차게 하루를 시작해볼까요?",
  "좋은 하루예요! 오늘은 특별한 일정이 없네요. 무엇을 도와드릴까요?",
  "어서오세요! 오늘은 일정이 비어있어요. 집중 작업하기 딱 좋은 날이에요!",
  "반가워요! 오늘은 여유로운 날이에요. 새로운 일을 시작해보는 건 어떨까요?",
  "안녕하세요! 오늘은 일정이 없어요. 편안하게 업무 보내세요~",
];

function _fmt(ev: CalendarEvent): string {
  const [hStr, mStr] = ev.start.slice(11, 16).split(":");
  const h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10);
  const period = h < 12 ? "오전" : "오후";
  const h12 = h % 12 || 12;
  return m === 0 ? `${period} ${h12}시` : `${period} ${h12}시 ${m}분`;
}

function _list(events: CalendarEvent[]): string {
  return events
    .slice(0, 3)
    .map((e) => `${_fmt(e)} ${e.title}`)
    .join(", ");
}

function _pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// 모듈 스코프 플래그 — JS 프로세스(앱) 재시작 시 리셋, StrictMode 이중 호출만 차단
let _greeted = false;

export async function showStartupGreeting(): Promise<void> {
  if (_greeted) return;
  _greeted = true;

  // 로컬 시간대 기준 오늘 날짜.
  // OS 시간대가 KST(또는 사용자 거주지)이므로 getFullYear/Month/Date를 사용하면 정확.
  // toISOString()은 UTC라 자정~09:00 사이에 어제 날짜가 됨.
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  const today = `${y}-${m}-${d}`;

  let todayEvents: CalendarEvent[] = [];
  try {
    const res = await fetch(API_BASE + "/api/calendar/events");
    if (res.ok) {
      const all = await (res.json() as Promise<CalendarEvent[]>);
      todayEvents = all
        .filter((e) => e.start.startsWith(today))
        .sort((a, b) => a.start.localeCompare(b.start));
    }
  } catch {
    // 백엔드 미연결 시 일정 없이 인사
  }

  const text =
    todayEvents.length > 0
      ? _pick(WITH_EVENTS)(todayEvents)
      : _pick(WITHOUT_EVENTS);

  useStore.getState().addMessage({ role: "ai", text });
  void speak(text);
}

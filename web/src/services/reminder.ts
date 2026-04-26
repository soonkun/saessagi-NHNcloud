// 10분마다 일정을 확인해 시작 10분 전 알림을 채팅에 추가

import { useStore } from "../store";
import type { CalendarEvent } from "../types";
import { API_BASE } from "./api";
import { speak } from "./tts";

const POLL_MS = 10 * 60 * 1000; // 10분
const REMIND_AHEAD_MS = 10 * 60 * 1000; // 10분 전
const REMIND_WINDOW_MS = 15 * 60 * 1000; // 폴링 지터 대응 15분 창

const remindedIds = new Set<number>();
let pollTimer: ReturnType<typeof setInterval> | null = null;

function _koTime(isoStart: string): string {
  const [hStr, mStr] = isoStart.slice(11, 16).split(":");
  const h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10);
  const period = h < 12 ? "오전" : "오후";
  const h12 = h % 12 || 12;
  return m === 0 ? `${period} ${h12}시` : `${period} ${h12}시 ${m}분`;
}

const REMINDER_TEMPLATES: Array<(ev: CalendarEvent) => string> = [
  (e) => `곧 ${_koTime(e.start)}에 "${e.title}" 일정이 시작돼요! 준비하세요~`,
  (e) => `알림: ${_koTime(e.start)} "${e.title}" 일정이 10분 후 시작됩니다.`,
  (e) => `"${e.title}" 일정 시작까지 약 10분 남았어요! (${_koTime(e.start)})`,
];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

async function checkUpcoming(): Promise<void> {
  const now = Date.now();
  const windowStart = now + REMIND_AHEAD_MS - 60_000; // 9분 후
  const windowEnd = now + REMIND_WINDOW_MS; // 15분 후

  let events: CalendarEvent[] = [];
  try {
    const res = await fetch(API_BASE + "/api/calendar/events");
    if (!res.ok) return;
    events = await (res.json() as Promise<CalendarEvent[]>);
  } catch {
    return; // 백엔드 미연결 시 조용히 무시
  }

  for (const ev of events) {
    if (remindedIds.has(ev.id)) continue;
    const startMs = new Date(ev.start).getTime();
    if (startMs >= windowStart && startMs <= windowEnd) {
      remindedIds.add(ev.id);
      const text = pick(REMINDER_TEMPLATES)(ev);
      useStore.getState().addMessage({ role: "ai", text });
      void speak(text);
    }
  }
}

export function startReminderPoll(): () => void {
  if (pollTimer !== null) return () => { /* already running */ };

  void checkUpcoming(); // 즉시 한 번 확인
  pollTimer = setInterval(() => { void checkUpcoming(); }, POLL_MS);

  return () => {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };
}

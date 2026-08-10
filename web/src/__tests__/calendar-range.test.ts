// CR-70 일정 시각 표시
import { describe, expect, it } from "vitest";
import { timeRange } from "../components/CalendarView";

describe("timeRange", () => {
  it("서버가 end를 주면 범위로 보여준다", () => {
    expect(timeRange({ start: "2026-08-11T09:00:00+09:00", end: "2026-08-11T18:00:00+09:00" }))
      .toBe("09:00 – 18:00");
  });

  it("end가 없으면 길이로 계산한다 (옛 응답 호환)", () => {
    // 실측 사고: `09:00 · 540분`으로 보여 언제 끝나는지 계산해야 했다
    expect(timeRange({ start: "2026-08-11T09:00:00", duration_minutes: 540 }))
      .toBe("09:00 – 18:00");
  });

  it("자정을 넘겨도 시각만 보여준다", () => {
    expect(timeRange({ start: "2026-08-11T23:00:00", duration_minutes: 120 }))
      .toBe("23:00 – 01:00");
  });

  it("길이 정보가 없으면 시작만", () => {
    expect(timeRange({ start: "2026-08-11T09:00:00" })).toBe("09:00");
  });
});

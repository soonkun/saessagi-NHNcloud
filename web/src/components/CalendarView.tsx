import { useEffect, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, Plus, Trash2, X } from "lucide-react";
import {
  fetchCalendarEvents,
  createCalendarEvent,
  deleteCalendarEvent,
} from "../services/api";
import type { CalendarEvent } from "../types";

// ────────────────────────────────────────────────────────────
// 유틸
// ────────────────────────────────────────────────────────────

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function firstDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay(); // 0=Sun
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

// ────────────────────────────────────────────────────────────
// 이벤트 추가 모달
// ────────────────────────────────────────────────────────────

interface AddEventModalProps {
  defaultDate: string;
  onClose: () => void;
  onSave: (event: Omit<CalendarEvent, "id">) => Promise<void>;
}

function AddEventModal({
  defaultDate,
  onClose,
  onSave,
}: AddEventModalProps): React.ReactElement {
  const [title, setTitle] = useState("");
  const [dateStr, setDateStr] = useState(defaultDate);
  const [hour, setHour] = useState("09");
  const [minute, setMinute] = useState("00");
  const [duration, setDuration] = useState("60");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave(): Promise<void> {
    if (!title.trim()) {
      setError("제목을 입력하세요");
      return;
    }
    setSaving(true);
    try {
      await onSave({
        title: title.trim(),
        start: `${dateStr}T${hour}:${minute}`,
        duration_minutes: duration ? Number(duration) : undefined,
        description: description.trim() || undefined,
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 2000,
        pointerEvents: "auto",
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--color-sidebar)",
          border: "1px solid var(--color-border)",
          borderRadius: 12,
          padding: 24,
          width: 360,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          pointerEvents: "auto",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ fontWeight: 600 }}>이벤트 추가</h3>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)" }}
          >
            <X size={16} />
          </button>
        </div>

        {error && (
          <div style={{ color: "#e05050", fontSize: 13 }}>{error}</div>
        )}

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>제목 *</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={inputStyle}
            placeholder="이벤트 제목"
            autoFocus
          />
        </label>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>시작 일시</span>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              type="date"
              value={dateStr}
              onChange={(e) => setDateStr(e.target.value)}
              style={{ ...inputStyle, flex: 1, colorScheme: "dark" }}
            />
            <select
              value={hour}
              onChange={(e) => setHour(e.target.value)}
              style={{ ...inputStyle, width: 64, padding: "7px 6px" }}
            >
              {Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0")).map((h) => (
                <option key={h} value={h}>{h}시</option>
              ))}
            </select>
            <select
              value={minute}
              onChange={(e) => setMinute(e.target.value)}
              style={{ ...inputStyle, width: 64, padding: "7px 6px" }}
            >
              {["00", "10", "20", "30", "40", "50"].map((m) => (
                <option key={m} value={m}>{m}분</option>
              ))}
            </select>
          </div>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>기간 (분)</span>
          <input
            type="number"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            style={inputStyle}
            min={1}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>설명</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={{ ...inputStyle, resize: "vertical", minHeight: 64 }}
            placeholder="선택 사항"
          />
        </label>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={cancelBtnStyle}>취소</button>
          <button onClick={handleSave} disabled={saving} style={saveBtnStyle}>
            {saving ? "저장 중..." : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--color-bg)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  color: "var(--color-text)",
  padding: "7px 10px",
  fontSize: 13,
  outline: "none",
  width: "100%",
};

const cancelBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  color: "var(--color-text)",
  cursor: "pointer",
  padding: "6px 14px",
  fontSize: 13,
};

const saveBtnStyle: React.CSSProperties = {
  background: "var(--color-accent)",
  border: "none",
  borderRadius: 6,
  color: "#fff",
  cursor: "pointer",
  padding: "6px 14px",
  fontSize: 13,
  fontWeight: 600,
};

// ────────────────────────────────────────────────────────────
// CalendarView
// ────────────────────────────────────────────────────────────

export function CalendarView(): React.ReactElement {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth());
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>(isoDate(today));
  const [showModal, setShowModal] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCalendarEvents();
      setEvents(data);
    } catch {
      // API 미연결 시 빈 목록
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function prevMonth(): void {
    if (month === 0) {
      setMonth(11);
      setYear((y) => y - 1);
    } else {
      setMonth((m) => m - 1);
    }
  }

  function nextMonth(): void {
    if (month === 11) {
      setMonth(0);
      setYear((y) => y + 1);
    } else {
      setMonth((m) => m + 1);
    }
  }

  async function handleAddEvent(
    event: Omit<CalendarEvent, "id">
  ): Promise<void> {
    const created = await createCalendarEvent({
      ...event,
      duration_minutes: event.duration_minutes,
    });
    setEvents((prev) => [...prev, created]);
  }

  async function handleDeleteEvent(id: number): Promise<void> {
    await deleteCalendarEvent(id);
    setEvents((prev) => prev.filter((e) => e.id !== id));
  }

  const firstDay = firstDayOfMonth(year, month);
  const totalDays = daysInMonth(year, month);
  const MONTH_KO = [
    "1월", "2월", "3월", "4월", "5월", "6월",
    "7월", "8월", "9월", "10월", "11월", "12월",
  ];
  const WEEK_DAYS = ["일", "월", "화", "수", "목", "금", "토"];

  const selectedEvents = events.filter((e) =>
    e.start.startsWith(selectedDate)
  );

  return (
    <div
      style={{
        display: "flex",
        height: "100%",
        gap: 0,
      }}
    >
      {/* 달력 그리드 */}
      <div style={{ flex: 1, padding: 24, overflowY: "auto" }}>
        {/* 헤더 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 20,
          }}
        >
          <button onClick={prevMonth} style={navBtnStyle}>
            <ChevronLeft size={18} />
          </button>
          <h2 style={{ fontWeight: 700, fontSize: 18 }}>
            {year}년 {MONTH_KO[month]}
          </h2>
          <button onClick={nextMonth} style={navBtnStyle}>
            <ChevronRight size={18} />
          </button>
        </div>

        {/* 요일 헤더 */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(7, 1fr)",
            gap: 2,
            marginBottom: 4,
          }}
        >
          {WEEK_DAYS.map((d, i) => (
            <div
              key={d}
              style={{
                textAlign: "center",
                fontSize: 12,
                color: i === 0 ? "#e05050" : i === 6 ? "#5080e0" : "var(--color-text-muted)",
                padding: "4px 0",
                fontWeight: 600,
              }}
            >
              {d}
            </div>
          ))}
        </div>

        {/* 날짜 그리드 */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(7, 1fr)",
            gap: 2,
          }}
        >
          {/* 빈 셀 */}
          {Array.from({ length: firstDay }, (_, i) => (
            <div key={`empty-${i}`} />
          ))}

          {Array.from({ length: totalDays }, (_, i) => {
            const day = i + 1;
            const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
            const isToday = dateStr === isoDate(today);
            const isSelected = dateStr === selectedDate;
            const hasEvent = events.some((e) => e.start.startsWith(dateStr));

            return (
              <button
                key={day}
                onClick={() => setSelectedDate(dateStr)}
                style={{
                  padding: "8px 4px",
                  borderRadius: 8,
                  border: "none",
                  background: isSelected
                    ? "var(--color-accent)"
                    : isToday
                    ? "rgba(201,100,66,0.2)"
                    : "transparent",
                  color: isSelected
                    ? "#fff"
                    : (firstDay + i) % 7 === 0 // 일요일
                    ? "#e05050"
                    : (firstDay + i) % 7 === 6 // 토요일
                    ? "#5080e0"
                    : "var(--color-text)",
                  cursor: "pointer",
                  position: "relative",
                  fontSize: 13,
                  fontWeight: isToday || isSelected ? 700 : 400,
                  transition: "background 0.1s",
                }}
              >
                {day}
                {hasEvent && (
                  <span
                    style={{
                      position: "absolute",
                      bottom: 3,
                      left: "50%",
                      transform: "translateX(-50%)",
                      width: 4,
                      height: 4,
                      borderRadius: "50%",
                      background: isSelected ? "#fff" : "var(--color-accent)",
                    }}
                  />
                )}
              </button>
            );
          })}
        </div>

        {loading && (
          <div style={{ color: "var(--color-text-muted)", textAlign: "center", marginTop: 20, fontSize: 13 }}>
            불러오는 중...
          </div>
        )}
      </div>

      {/* 이벤트 패널 */}
      <div
        style={{
          width: 280,
          borderLeft: "1px solid var(--color-border)",
          padding: 20,
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 16,
          }}
        >
          <h3 style={{ fontWeight: 600, fontSize: 15 }}>{selectedDate}</h3>
          <button
            onClick={() => setShowModal(true)}
            style={{
              background: "var(--color-accent)",
              border: "none",
              borderRadius: 6,
              color: "#fff",
              cursor: "pointer",
              padding: "5px 8px",
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: 12,
            }}
          >
            <Plus size={14} />
            추가
          </button>
        </div>

        {selectedEvents.length === 0 ? (
          <div style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
            이벤트 없음
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {selectedEvents.map((event) => (
              <div
                key={event.id}
                style={{
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  position: "relative",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                  {event.title}
                </div>
                <div style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
                  {event.start.slice(11, 16)}
                  {event.duration_minutes ? ` (${event.duration_minutes}분)` : ""}
                </div>
                {event.description && (
                  <div style={{ fontSize: 12, marginTop: 4, color: "var(--color-text-muted)" }}>
                    {event.description}
                  </div>
                )}
                <button
                  onClick={() => void handleDeleteEvent(event.id)}
                  className="btn-delete"
                  style={{
                    position: "absolute",
                    top: 8,
                    right: 8,
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--color-text-muted)",
                  }}
                  title="삭제"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {showModal && (
        <AddEventModal
          defaultDate={selectedDate}
          onClose={() => setShowModal(false)}
          onSave={handleAddEvent}
        />
      )}
    </div>
  );
}

const navBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  color: "var(--color-text)",
  cursor: "pointer",
  padding: "4px 8px",
  display: "flex",
  alignItems: "center",
};

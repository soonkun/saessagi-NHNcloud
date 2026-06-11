import { useEffect, useState, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { Calendar, ChevronLeft, ChevronRight, Plus, Trash2, X } from "lucide-react";
import {
  fetchCalendarEvents,
  createCalendarEvent,
  deleteCalendarEvent,
} from "../services/api";
import type { CalendarEvent } from "../types";
import { useStore } from "../store";

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
  // 로컬 시간대 기준 YYYY-MM-DD.
  // toISOString()을 쓰면 UTC 기준이라 KST 자정~09:00 사이에 어제 날짜로 잘못 표시됨.
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// ────────────────────────────────────────────────────────────
// 커스텀 날짜 피커
// ────────────────────────────────────────────────────────────

const WEEK_DAYS_SHORT = ["일", "월", "화", "수", "목", "금", "토"];

function DatePicker({ value, onChange }: { value: string; onChange: (v: string) => void }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [popupPos, setPopupPos] = useState({ top: 0, left: 0 });
  const parsed = new Date(value + "T00:00:00");
  const [viewYear, setViewYear] = useState(parsed.getFullYear());
  const [viewMonth, setViewMonth] = useState(parsed.getMonth());
  const triggerRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  // Close popup on outside click (document level)
  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent): void {
      const t = e.target as Node;
      if (
        !(popupRef.current?.contains(t)) &&
        !(triggerRef.current?.contains(t))
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  function toggleOpen(): void {
    if (!open && triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect();
      setPopupPos({ top: r.bottom + 4, left: r.left });
    }
    setOpen((o) => !o);
  }

  function prevMonth(): void {
    if (viewMonth === 0) { setViewMonth(11); setViewYear((y) => y - 1); }
    else setViewMonth((m) => m - 1);
  }
  function nextMonth(): void {
    if (viewMonth === 11) { setViewMonth(0); setViewYear((y) => y + 1); }
    else setViewMonth((m) => m + 1);
  }

  const firstDay = firstDayOfMonth(viewYear, viewMonth);
  const totalDays = daysInMonth(viewYear, viewMonth);

  const popup = open ? (
    <div
      ref={popupRef}
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: "fixed",
        top: popupPos.top,
        left: popupPos.left,
        zIndex: 9999,
        background: "var(--color-sidebar)",
        border: "1px solid var(--color-border)",
        borderRadius: 10,
        padding: 12,
        width: 240,
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
        pointerEvents: "auto",
      }}
    >
      {/* 월 네비게이션 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <button onClick={prevMonth} style={miniNavBtn}><ChevronLeft size={14} /></button>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{viewYear}년 {viewMonth + 1}월</span>
        <button onClick={nextMonth} style={miniNavBtn}><ChevronRight size={14} /></button>
      </div>

      {/* 요일 헤더 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", marginBottom: 4 }}>
        {WEEK_DAYS_SHORT.map((d, i) => (
          <div key={d} style={{ textAlign: "center", fontSize: 11, fontWeight: 600, padding: "2px 0",
            color: i === 0 ? "#e05050" : i === 6 ? "#5080e0" : "var(--color-text-muted)" }}>{d}</div>
        ))}
      </div>

      {/* 날짜 그리드 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 2 }}>
        {Array.from({ length: firstDay }, (_, i) => <div key={`e-${i}`} />)}
        {Array.from({ length: totalDays }, (_, i) => {
          const day = i + 1;
          const dateStr = `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
          const isSelected = dateStr === value;
          return (
            <button
              key={day}
              onClick={() => { onChange(dateStr); setOpen(false); }}
              style={{
                padding: "5px 2px",
                border: "none",
                borderRadius: 6,
                fontSize: 12,
                cursor: "pointer",
                background: isSelected ? "var(--color-accent)" : "transparent",
                color: isSelected ? "#fff"
                  : (firstDay + i) % 7 === 0 ? "#e05050"
                  : (firstDay + i) % 7 === 6 ? "#5080e0"
                  : "var(--color-text)",
                fontWeight: isSelected ? 700 : 400,
              }}
            >
              {day}
            </button>
          );
        })}
      </div>
    </div>
  ) : null;

  return (
    <div ref={triggerRef} style={{ flex: 1, pointerEvents: "auto" }}>
      <div
        onClick={toggleOpen}
        style={{
          ...pickerInputStyle,
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
          userSelect: "none",
          pointerEvents: "auto",
        }}
      >
        <span style={{ flex: 1 }}>{value}</span>
        <Calendar size={13} style={{ color: "var(--color-text-muted)", flexShrink: 0 }} />
      </div>
      {createPortal(popup, document.body)}
    </div>
  );
}

const pickerInputStyle: React.CSSProperties = {
  background: "var(--color-bg)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  color: "var(--color-text)",
  padding: "7px 10px",
  fontSize: 13,
};

const miniNavBtn: React.CSSProperties = {
  background: "transparent",
  border: "none",
  cursor: "pointer",
  color: "var(--color-text)",
  display: "flex",
  alignItems: "center",
  padding: 2,
};

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
            onClick={() => window.electronAPI?.restoreFocus()}
            style={inputStyle}
            placeholder="이벤트 제목"
            autoFocus
          />
        </label>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>시작 일시</span>
          <div style={{ display: "flex", gap: 6 }}>
            <DatePicker value={dateStr} onChange={setDateStr} />
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
            onClick={() => window.electronAPI?.restoreFocus()}
            style={inputStyle}
            min={1}
          />
        </label>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>설명</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onClick={() => window.electronAPI?.restoreFocus()}
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
  const windowMode = useStore((s) => s.windowMode);
  const isDesktop = windowMode === "window";
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
            gap: isDesktop ? 6 : 2,
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
            const dayEvents = events
              .filter((e) => e.start.startsWith(dateStr))
              .sort((a, b) => a.start.localeCompare(b.start));
            const hasEvent = dayEvents.length > 0;
            const colIdx = (firstDay + i) % 7;
            const dayColor =
              isSelected ? "#fff"
              : colIdx === 0 ? "#e05050"
              : colIdx === 6 ? "#5080e0"
              : "var(--color-text)";

            if (isDesktop) {
              // 데스크탑: 큰 박스 + 셀 안에 이벤트 인라인 (시간순)
              return (
                <div
                  key={day}
                  onClick={() => setSelectedDate(dateStr)}
                  style={{
                    minHeight: 92,
                    padding: "6px 8px",
                    borderRadius: 8,
                    border: `1px solid ${
                      isSelected ? "var(--color-accent)" : "var(--color-border)"
                    }`,
                    background: isSelected
                      ? "rgba(201,100,66,0.12)"
                      : isToday
                      ? "rgba(201,100,66,0.06)"
                      : "var(--color-panel)",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    gap: 3,
                    transition: "background 0.1s, border-color 0.1s",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: isToday ? 800 : 600,
                      color: isToday && !isSelected ? "var(--color-accent)" : dayColor,
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    {day}
                    {isToday && (
                      <span style={{ fontSize: 9, color: "var(--color-accent)", fontWeight: 700 }}>오늘</span>
                    )}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
                    {dayEvents.slice(0, 3).map((ev) => (
                      <div
                        key={ev.id}
                        title={`${ev.start.slice(11, 16)} · ${ev.title}`}
                        style={{
                          fontSize: 10.5,
                          padding: "2px 5px",
                          borderRadius: 4,
                          background: "rgba(100,140,220,0.18)",
                          borderLeft: "2px solid var(--color-accent)",
                          color: "var(--color-text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        <span style={{ color: "var(--color-text-muted)", marginRight: 4 }}>
                          {ev.start.slice(11, 16)}
                        </span>
                        {ev.title}
                      </div>
                    ))}
                    {dayEvents.length > 3 && (
                      <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginLeft: 4 }}>
                        +{dayEvents.length - 3}건
                      </div>
                    )}
                  </div>
                </div>
              );
            }

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
                  color: dayColor,
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
          width: isDesktop ? 340 : 280,
          borderLeft: "1px solid var(--color-border)",
          padding: isDesktop ? 24 : 20,
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
          <div style={{ display: "flex", flexDirection: "column", gap: isDesktop ? 10 : 8 }}>
            {/* 시간순 정렬해서 표시 */}
            {[...selectedEvents].sort((a, b) => a.start.localeCompare(b.start)).map((event) => (
              <div
                key={event.id}
                style={{
                  background: "var(--color-bg)",
                  border: "1px solid var(--color-border)",
                  borderRadius: isDesktop ? 10 : 8,
                  borderLeft: `4px solid var(--color-accent)`,
                  padding: isDesktop ? "14px 16px" : "10px 12px",
                  position: "relative",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: isDesktop ? 15 : 13, marginBottom: 4 }}>
                  {event.title}
                </div>
                <div style={{ color: "var(--color-text-muted)", fontSize: isDesktop ? 13 : 12 }}>
                  🕐 {event.start.slice(11, 16)}
                  {event.duration_minutes ? ` · ${event.duration_minutes}분` : ""}
                </div>
                {event.description && (
                  <div style={{ fontSize: isDesktop ? 13 : 12, marginTop: 6, color: "var(--color-text-muted)" }}>
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

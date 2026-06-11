# src/proactive/dispatcher.py
"""ProactiveDispatcher — 스케줄러 + 쿨다운 + DND 통합 프로액티브 발화 디스패처."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from .messages import _compose_message
from .types import TOPICS, ProactiveTopic

# 타입 체크 시에만 임포트 (런타임 순환 방지)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from apscheduler.schedulers.base import BaseScheduler

    from calendar_service.service import CalendarService
    from idle_monitor import IdleMonitor


SendTextCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""upstream WebSocket send_text 시그니처. dict를 받아 직렬화는 호출자 책임."""


class ProactiveDispatcher:
    """스케줄러 + 쿨다운 + DND를 통합 관리하는 프로액티브 발화 디스패처.

    책임:
      - APScheduler 잡 2종(cron: morning briefing, interval: event reminder)을 관리.
      - M_10 IdleMonitor의 단일 콜백 슬롯을 차지.
      - 토픽별 쿨다운(동일 토픽 N분 내 재발행 금지) + DND 드롭.
      - upstream `ai-speak-signal` 페이로드로 변환해 `send_text`에 전달.

    비책임 (§1.3):
      - 자연어 프롬프트 최종 생성 (upstream `proactive_speak_prompt`).
      - TTS / 아바타 표정 / 프론트 팝업.
    """

    def __init__(
        self,
        *,
        calendar: "CalendarService",
        idle_monitor: "IdleMonitor",
        send_text: SendTextCallback,
        morning_time: str = "09:00",
        timezone: "ZoneInfo | None" = None,
        reminder_lead_minutes: int = 10,
        reminder_check_interval_seconds: int = 60,
        cooldown_min: int = 30,
        dnd_enabled: bool = False,
        clock: Callable[[], datetime] = datetime.now,
        scheduler: "BaseScheduler | None" = None,
    ) -> None:
        """
        Args:
            calendar: M_09 CalendarService. sync API (`events_due_within`,
                `get_events`)를 `run_in_executor`로 호출.
            idle_monitor: M_10 IdleMonitor. `on_event(self._on_idle_event)`로 구독.
            send_text: upstream WebSocket `send_text` 콜러블. M_01 AppCore가 per-client
                ws 연결에 바인딩해 주입.
            morning_time: "HH:MM" 포맷. AppConfig.morning_briefing_time을 그대로 전달받는다.
            timezone: APScheduler cron 트리거의 기준 tz. 기본 Asia/Seoul.
            reminder_lead_minutes: event_reminder 리드 타임(분). 기본 10.
            reminder_check_interval_seconds: event reminder 폴링 간격(초). 기본 60.
            cooldown_min: 토픽별 쿨다운(분). 기본 30.
            dnd_enabled: 초기 DND 상태.
            clock: 현재 시각 공급자 (테스트 주입). 기본 `datetime.now`.
            scheduler: APScheduler BaseScheduler 인스턴스 (테스트 주입). None이면
                `AsyncIOScheduler(timezone=timezone)` 기본 생성.

        Raises:
            ValueError: morning_time 포맷 불량, reminder_lead_minutes/cooldown_min 범위 위반.
            TypeError: calendar / idle_monitor / send_text 타입 불량.
        """
        # 타입 검증
        if not callable(send_text):
            raise TypeError(f"send_text must be callable, got {type(send_text)!r}")

        # morning_time 검증
        _hh, _mm = _parse_morning_time(morning_time)  # raises ValueError on invalid

        # 범위 검증
        if not (1 <= reminder_lead_minutes <= 1440):
            raise ValueError(
                f"reminder_lead_minutes는 1~1440 범위여야 합니다: {reminder_lead_minutes}"
            )
        if not (1 <= reminder_check_interval_seconds <= 3600):
            raise ValueError(
                "reminder_check_interval_seconds는 1~3600 범위여야 합니다: "
                f"{reminder_check_interval_seconds}"
            )
        if not (1 <= cooldown_min <= 1440):
            raise ValueError(f"cooldown_min은 1~1440 범위여야 합니다: {cooldown_min}")

        # 파라미터 저장
        self._calendar = calendar
        self._idle_monitor = idle_monitor
        self._send_text = send_text
        self._morning_time = morning_time
        self._morning_hh = _hh
        self._morning_mm = _mm

        # timezone 처리 (런타임 임포트로 TYPE_CHECKING 블록 우회)
        from zoneinfo import ZoneInfo as _ZoneInfo

        self._timezone: _ZoneInfo = timezone if timezone is not None else _ZoneInfo("Asia/Seoul")

        self._reminder_lead_minutes = reminder_lead_minutes
        self._reminder_check_interval_seconds = reminder_check_interval_seconds
        self._cooldown_min = cooldown_min
        self._clock = clock

        # 런타임 상태
        self._external_scheduler = scheduler  # 주입된 스케줄러 (None=내부 생성)
        self._scheduler: "BaseScheduler | None" = scheduler
        self._dnd_enabled: bool = dnd_enabled
        self._enabled: bool = True
        self._started: bool = False
        self._last_emitted_at: dict[ProactiveTopic, datetime] = {}
        self._notified_reminders: set[int] = set()
        self._emit_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """APScheduler 기동 + IdleMonitor 콜백 구독 + 초기 상태 로깅.

        멱등: 이미 start된 상태에서 재호출 시 logger.warning + no-op.
        APScheduler 기동 실패 시 logger.error + _enabled=False (예외 전파 안 함).
        """
        if self._started:
            logger.warning("ProactiveDispatcher.start() called twice; ignoring")
            return

        # 스케줄러 준비
        if self._external_scheduler is None:
            try:
                from apscheduler.schedulers.asyncio import AsyncIOScheduler

                self._scheduler = AsyncIOScheduler(timezone=self._timezone)
            except Exception as exc:
                logger.error(
                    "APScheduler init failed: %s; proactive disabled",
                    exc,
                )
                self._enabled = False
                self._started = True
                return
        else:
            self._scheduler = self._external_scheduler

        # 잡 등록
        try:
            self._register_jobs()
            assert self._scheduler is not None
            self._scheduler.start()
        except Exception as exc:
            logger.error(
                "APScheduler init failed: %s; proactive disabled",
                exc,
            )
            self._enabled = False
            self._started = True
            return

        # IdleMonitor 콜백 구독
        self._idle_monitor.on_event(self._on_idle_event)

        self._started = True
        logger.info(
            f"ProactiveDispatcher started: morning={self._morning_time}, "
            f"cooldown={self._cooldown_min}min, dnd={self._dnd_enabled}"
        )

    async def stop(self) -> None:
        """APScheduler 종료 + IdleMonitor 콜백 해제.

        멱등: 이미 stop되었거나 start되지 않은 상태에서도 예외 없음.
        """
        if not self._started:
            return

        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as exc:
                logger.warning(f"APScheduler shutdown 오류 (무시): {exc!r}")

        try:
            self._idle_monitor.on_event(None)
        except Exception as exc:
            logger.warning(f"idle_monitor.on_event(None) 오류 (무시): {exc!r}")

        self._started = False
        logger.info("ProactiveDispatcher stopped")

    async def emit(
        self,
        topic: ProactiveTopic,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """토픽을 발송 요청.

        Returns:
            True  — send_text 호출 성공 + 쿨다운 기록 갱신됨.
            False — DND / 쿨다운 / send_text 예외 / _enabled=False 로 드롭.

        Raises:
            ValueError: topic이 TOPICS 밖인 경우.
        """
        if not self._enabled:
            return False

        if topic not in TOPICS:
            raise ValueError(f"unknown topic: {topic!r}")

        async with self._emit_lock:
            if self._dnd_enabled:
                logger.debug("emit drop (dnd): topic=%s", topic)
                return False

            now = self._clock()
            last = self._last_emitted_at.get(topic)
            if last is not None:
                elapsed = (now - last).total_seconds()
                # elapsed < 0: 시계 역행 → 쿨다운 만료로 간주 (§10 시계 역행 정책)
                if 0 <= elapsed < self._cooldown_min * 60:
                    logger.debug("emit drop (cooldown): topic=%s, last=%s", topic, last)
                    return False

            text = _compose_message(topic, context or {})
            payload: dict[str, Any] = {
                "type": "ai-speak-signal",
                "text": text,
                "topic": topic,
                "context": context or {},
            }

            try:
                await self._send_text(payload)
            except Exception as exc:
                logger.error(f"send_text 실패: topic={topic}, exc={exc!r}")
                return False

            self._last_emitted_at[topic] = now
            logger.info(f"emit success: topic={topic}")
            return True

    def set_dnd(self, enabled: bool) -> None:
        """방해 금지 모드 토글.

        DND 이중 체크(D-2): 자체 상태 갱신 + M_10에 전파.

        Raises:
            TypeError: enabled가 bool이 아닌 경우.
        """
        if not isinstance(enabled, bool):
            raise TypeError(f"enabled must be bool, got {type(enabled)!r}")
        self._dnd_enabled = enabled
        self._idle_monitor.set_dnd(enabled)
        logger.info(f"DND set to {enabled}")

    # ------------------------------------------------------------------
    # 내부 잡 함수
    # ------------------------------------------------------------------

    async def _job_morning_briefing(self) -> None:
        """매일 morning_time에 오늘의 일정 브리핑을 발송한다."""
        from datetime import timedelta

        now_tz = datetime.now(self._timezone)
        today_start = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(
                None,
                lambda: self._calendar.get_events(today_start, today_end),
            )
        except Exception as exc:
            logger.error(f"morning_briefing: get_events 실패: {exc!r}")
            return

        event_list = [
            {
                "start_hhmm": ev.start.astimezone(self._timezone).strftime("%H:%M"),
                "title": ev.title,
            }
            for ev in events
        ]

        await self.emit("morning_briefing", {"events": event_list})

    async def _job_event_reminder(self) -> None:
        """1분 간격으로 10분 내 시작 이벤트를 조회해 알림을 발송한다."""
        # 과거 이벤트 캐시 청소
        await self._cleanup_notified_reminders()

        try:
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(
                None,
                lambda: self._calendar.events_due_within(self._reminder_lead_minutes),
            )
        except Exception as exc:
            logger.error(f"events_due_within 실패: {exc!r}")
            return

        if not events:
            return

        for ev in events:
            if ev.id in self._notified_reminders:
                continue

            minutes_until = self._minutes_until(ev.start)
            ok = await self.emit(
                "event_reminder",
                {
                    "event_id": ev.id,
                    "title": ev.title,
                    "start": ev.start.isoformat(),
                    "minutes_until": minutes_until,
                },
            )
            if ok:
                self._notified_reminders.add(ev.id)

    async def _on_idle_event(self, topic: str) -> None:
        """M_10 IdleMonitor 콜백. idle_rest / overwork 이벤트 수신."""
        # topic이 ProactiveTopic과 호환되는 문자열임을 타입 체크 없이 전달
        # (D-11: IdleEvent Literal과 동일 문자열)
        if topic in TOPICS:
            await self.emit(topic, context={})
        else:
            logger.warning("_on_idle_event: unknown topic=%r", topic)

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _register_jobs(self) -> None:
        """APScheduler 잡 2종을 등록한다."""
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        assert self._scheduler is not None

        # (A) morning briefing — Cron
        self._scheduler.add_job(
            self._job_morning_briefing,
            trigger=CronTrigger(
                hour=self._morning_hh,
                minute=self._morning_mm,
                timezone=self._timezone,
            ),
            id="morning_briefing",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,  # 10분 지연 허용 (D-4)
        )

        # (B) event reminder — Interval
        self._scheduler.add_job(
            self._job_event_reminder,
            trigger=IntervalTrigger(seconds=self._reminder_check_interval_seconds),
            id="event_reminder",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    def _minutes_until(self, start: datetime) -> int:
        """이벤트 시작까지 남은 분 수를 반환한다. 음수면 0 반환."""
        now = datetime.now(timezone.utc)
        if start.tzinfo is None:
            # tz-naive이면 UTC 가정
            start = start.replace(tzinfo=timezone.utc)
        delta = (start - now).total_seconds()
        return max(0, int(delta // 60))

    async def _cleanup_notified_reminders(self) -> None:
        """이미 시작된 이벤트의 알림 캐시를 정리한다."""
        if not self._notified_reminders:
            return

        now_utc = datetime.now(timezone.utc)
        to_remove: set[int] = set()

        from datetime import timedelta as _timedelta

        for event_id in list(self._notified_reminders):
            try:
                loop = asyncio.get_running_loop()
                eid: int = event_id

                def _fetch(eid: int = eid) -> Any:
                    return self._calendar.get_event(eid)

                ev = await loop.run_in_executor(None, _fetch)
                if ev is None:
                    # 이벤트가 DB에서 사라짐 → 과거로 간주
                    to_remove.add(event_id)
                else:
                    ev_start = ev.start
                    if ev_start.tzinfo is None:
                        ev_start = ev_start.replace(tzinfo=timezone.utc)
                    duration_td = _timedelta(minutes=ev.duration_minutes)
                    if ev_start + duration_td < now_utc:
                        to_remove.add(event_id)
            except Exception as exc:
                logger.debug("cleanup_notified_reminders: get_event(%d) 실패: %s", event_id, exc)

        self._notified_reminders -= to_remove


def _parse_morning_time(morning_time: str) -> tuple[int, int]:
    """ "HH:MM" 포맷을 파싱해 (hour, minute) 튜플을 반환한다.

    Raises:
        ValueError: 포맷 불량 또는 범위 초과.
    """
    if not isinstance(morning_time, str):
        raise ValueError(f"morning_time은 문자열이어야 합니다: {morning_time!r}")
    parts = morning_time.split(":")
    if len(parts) != 2:
        raise ValueError(f"morning_time은 HH:MM 포맷이어야 합니다: {morning_time!r}")
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"morning_time은 HH:MM 포맷이어야 합니다: {morning_time!r}") from exc
    if not (0 <= hh <= 23):
        raise ValueError(f"시간은 0~23 범위여야 합니다: {hh}")
    if not (0 <= mm <= 59):
        raise ValueError(f"분은 0~59 범위여야 합니다: {mm}")
    return hh, mm

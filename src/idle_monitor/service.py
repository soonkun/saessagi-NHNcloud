# src/idle_monitor/service.py
"""IdleMonitor — Windows 유휴·과로 상태 전이 감지기."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from loguru import logger

from idle_monitor.backends.base import _IdleBackend
from idle_monitor.types import IdleEvent, IdleEventCallback


class IdleMonitor:
    """Windows 유휴·과로 상태 전이 감지기.

    책임:
      - 키보드·마우스 입력 유무만 본다 (내용 미기록).
      - 1초 주기 `_tick`으로 상태 전이를 평가.
      - 전이 발생 시 `IdleEventCallback`을 `asyncio.create_task`로 호출(본체 블로킹 방지).

    비책임 (§1.3):
      - 시간 기반 쿨다운 (M_11).
      - WebSocket 송신 (M_11).
      - APScheduler 스케줄 (M_11).
    """

    def __init__(
        self,
        *,
        idle_threshold_min: int = 45,
        overwork_threshold_min: int = 120,
        active_gap_seconds: int = 60,
        poll_interval_seconds: float = 1.0,
        clock: Callable[[], datetime] = datetime.now,
        backend: _IdleBackend | None = None,
    ) -> None:
        """
        Args:
            idle_threshold_min: 무입력이 이 분수 이상 지속되면 `idle_rest` 방출.
                1 이상 1440 이하. 0/음수/1441+ → ValueError.
            overwork_threshold_min: 연속 활동이 이 분수 이상 지속되면 `overwork` 방출.
                1 이상 1440 이하. 범위 밖 → ValueError.
            active_gap_seconds: 연속 활동 판정 간격. 마지막 입력 이후 이 초 이하이면
                "계속 활동 중"으로 간주. 1 이상 3600 이하. 범위 밖 → ValueError.
            poll_interval_seconds: _tick 폴링 주기. 0.1 이상 10.0 이하.
                기본 1.0초. 테스트에서는 0.1로 낮춰 단위 테스트 가속 가능.
            clock: 현재 시각 공급자. 테스트에서 monkeypatch 대신 주입으로 고정.
                기본 `datetime.now`. tz-aware / naive 양쪽 수용하되 일관성은 호출자 책임.
            backend: `_IdleBackend` 인스턴스. None이면 `_select_backend()`가
                (Windows: Pynput → Win32 폴백 / 비Windows: Noop)을 자동 선택.

        Raises:
            ValueError: 위 파라미터 범위 위반.
        """
        if not (1 <= idle_threshold_min <= 1440):
            raise ValueError(f"idle_threshold_min은 1~1440 범위여야 합니다: {idle_threshold_min}")
        if not (1 <= overwork_threshold_min <= 1440):
            raise ValueError(
                f"overwork_threshold_min은 1~1440 범위여야 합니다: {overwork_threshold_min}"
            )
        if not (1 <= active_gap_seconds <= 3600):
            raise ValueError(f"active_gap_seconds는 1~3600 범위여야 합니다: {active_gap_seconds}")
        if not (0.1 <= poll_interval_seconds <= 10.0):
            raise ValueError(
                f"poll_interval_seconds는 0.1~10.0 범위여야 합니다: {poll_interval_seconds}"
            )

        # 파라미터
        self._idle_threshold_min = idle_threshold_min
        self._overwork_threshold_min = overwork_threshold_min
        self._active_gap_seconds = active_gap_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock

        # 런타임 상태
        self._injected_backend: _IdleBackend | None = backend  # 주입된 백엔드 (None=자동)
        self._backend: _IdleBackend | None = None  # start() 후 실제 사용 백엔드
        self._state: Literal["idle", "active"] = "active"
        self._active_since: datetime = clock()
        self._overwork_emitted: bool = False
        self._dnd_enabled: bool = False
        self._callback: IdleEventCallback | None = None
        self._task: asyncio.Task[None] | None = None
        self._started: bool = False

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """백엔드 훅 초기화 + 폴링 Task 생성.

        - 이미 start된 상태에서 재호출 시 logger.warning 1회 + no-op (멱등).
        - 비Windows(Noop 백엔드)에서는 logger.warning("IdleMonitor disabled on %s") 1회 로그
          + 폴링 Task 생성하지 않음.
        - 이벤트 루프가 없는 상태에서 호출하면 RuntimeError.

        Raises:
            RuntimeError: 실행 중인 이벤트 루프가 없을 때.
        """
        if self._started:
            logger.warning("IdleMonitor.start() called twice; ignoring")
            return

        # 이벤트 루프 확인
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError("IdleMonitor.start() must be called within a running event loop")

        # 백엔드 선택 (주입된 경우 그대로 사용, None이면 자동 선택)
        if self._injected_backend is not None:
            self._backend = self._injected_backend
        else:
            from idle_monitor.backends import _select_backend

            self._backend = _select_backend(self._clock)

        # NoopBackend 여부 확인
        from idle_monitor.backends.noop_backend import NoopBackend

        if isinstance(self._backend, NoopBackend):
            # NoopBackend의 start()를 호출해 플랫폼 경고 로그 발행
            self._backend.start()
            logger.info("IdleMonitor started (Noop mode — no events will be emitted)")
            self._started = True
            return

        # 폴링 Task 생성
        self._task = loop.create_task(self._poll_loop())
        self._started = True
        logger.info(f"IdleMonitor started with backend={type(self._backend).__name__}")

    async def stop(self) -> None:
        """폴링 Task 취소 + 백엔드 훅 정리.

        동작:
          1. 폴링 Task에 cancel() + await (CancelledError swallow).
          2. backend.stop() 호출.
          3. _started 플래그 해제.
          4. 멱등 — 이미 stop되었거나 start되지 않은 상태에서도 예외 없음.
        """
        if not self._started:
            return

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"backend.stop() 오류 (무시): {exc!r}")

        self._started = False
        logger.info("IdleMonitor stopped")

    def set_dnd(self, enabled: bool) -> None:
        """방해 금지 모드 토글.

        - True면 _tick에서 상태 전이가 감지되어도 콜백을 호출하지 않는다.
        - False로 복귀해도 이미 놓친 전이는 재방출하지 않는다.

        Raises:
            TypeError: enabled가 bool이 아닌 경우.
        """
        if not isinstance(enabled, bool):
            raise TypeError(f"set_dnd expects bool, got {type(enabled).__name__}")
        self._dnd_enabled = enabled
        logger.debug("IdleMonitor DND: %s", enabled)

    def on_event(self, callback: IdleEventCallback | None) -> None:
        """이벤트 콜백 등록. 덮어쓰기 (단일 슬롯).

        - callback=None을 전달하면 콜백 제거.
        - 여러 번 호출 시 마지막 값만 유효.

        Raises:
            TypeError: callback이 callable도 None도 아닌 경우.
        """
        if callback is not None and not callable(callback):
            raise TypeError("callback must be callable or None")
        self._callback = callback

    def last_input_at(self) -> datetime:
        """마지막 입력 감지 시각 (clock() 기준 동일 tz/naive).

        - start() 이전에는 생성 시점의 clock() 값을 반환.
        - start() 후 backend.last_input_at(now)를 현재 시각으로 조회.
        """
        if self._backend is None:
            return self._active_since
        now = self._clock()
        return self._backend.last_input_at(now)

    def seconds_since_last_input(self) -> float:
        """현재 시각과 last_input_at()의 차이(초, non-negative).

        클록 역행으로 음수가 계산되면 0.0으로 클램프.
        """
        now = self._clock()
        last = self.last_input_at()
        elapsed = (now - last).total_seconds()
        return max(0.0, elapsed)

    def _tick(self, now: datetime | None = None) -> None:
        """단위 테스트/단독 호출을 위한 공개 가시 private 메서드.

        Args:
            now: None이면 self._clock() 호출. 테스트에서는 명시적 datetime 주입.

        동작:
          1. now - last_input_at 계산.
          2. 현재 상태와 경과 시간으로 IDLE/ACTIVE 전이 판정.
          3. 전이 발생 + DND 비활성 + 콜백 존재 시 asyncio.create_task(cb(event)).
          4. 예외는 logger.error로 잡고 루프 생존.
        """
        try:
            if now is None:
                now = self._clock()

            if self._backend is None:
                return

            last = self._backend.last_input_at(now)
            raw_elapsed = (now - last).total_seconds()
            elapsed = max(0.0, raw_elapsed)  # 클록 역행 방어 (D-10)

            idle_threshold_sec = self._idle_threshold_min * 60
            overwork_threshold_sec = self._overwork_threshold_min * 60

            if self._state == "active":
                # (1) active → idle 전이
                if elapsed >= idle_threshold_sec:
                    self._state = "idle"
                    self._overwork_emitted = False
                    logger.debug("IdleMonitor: active→idle (elapsed=%.1fs)", elapsed)
                    self._emit("idle_rest")
                # (2) overwork 판정
                elif (now - self._active_since).total_seconds() >= overwork_threshold_sec:
                    if not self._overwork_emitted:
                        self._overwork_emitted = True
                        logger.debug("IdleMonitor: overwork (active_since=%s)", self._active_since)
                        self._emit("overwork")
            else:  # self._state == "idle"
                # idle → active 복귀: 입력이 있었으면 (elapsed < active_gap_seconds)
                if elapsed < self._active_gap_seconds:
                    self._state = "active"
                    self._active_since = now
                    self._overwork_emitted = False
                    logger.debug("IdleMonitor: idle→active (elapsed=%.1fs)", elapsed)
                    # idle→active는 UX상 조용한 복귀 — 이벤트 emit 없음

        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).error(f"IdleMonitor._tick 내부 예외: {exc!r}")

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _emit(self, event: IdleEvent) -> None:
        """이벤트 방출 — DND 체크 후 asyncio.create_task로 콜백 호출."""
        if self._dnd_enabled:
            logger.debug("IdleMonitor: DND 활성 — 이벤트 드롭: %s", event)
            return
        if self._callback is None:
            return
        asyncio.create_task(self._safe_invoke_callback(event))

    async def _safe_invoke_callback(self, event: IdleEvent) -> None:
        """콜백 안전 호출 — 예외 swallow + logger.warning."""
        if self._callback is None:
            return
        try:
            await self._callback(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(f"idle callback raised: {exc!r}")

    async def _poll_loop(self) -> None:
        """1초 주기 폴링 루프."""
        while True:
            self._tick()
            await asyncio.sleep(self._poll_interval_seconds)

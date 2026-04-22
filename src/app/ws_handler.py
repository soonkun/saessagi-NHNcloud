# src/app/ws_handler.py
"""AppWebSocketHandler — upstream WebSocketHandler를 상속해 신규 메시지 3종 추가."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket
from loguru import logger

from open_llm_vtuber.websocket_handler import WebSocketHandler  # upstream

from .service_context import AppServiceContext

# screenshot-trigger prompt 최대 크기 (256 KiB) — A-5 테스트 대응
_PROMPT_MAX_BYTES = 256 * 1024


@dataclass
class ContinuousCaptureTask:
    """연속 캡처 태스크 메타데이터."""

    client_uid: str
    interval_sec: int
    monitor_index: int
    prompt_template: str
    task: asyncio.Task  # type: ignore[type-arg]
    started_at: datetime = field(default_factory=datetime.now)


class AppWebSocketHandler(WebSocketHandler):  # type: ignore[misc]
    """upstream WebSocketHandler 서브클래스.

    신규 메시지 타입 4종 추가:
    - screenshot-trigger
    - start-continuous-capture
    - stop-continuous-capture
    - set-dnd
    """

    def __init__(self, default_context_cache: AppServiceContext) -> None:
        super().__init__(default_context_cache)
        self._continuous_tasks: dict[str, ContinuousCaptureTask] = {}
        self._tasks_lock: asyncio.Lock = asyncio.Lock()
        # D-13: AppServiceContext._active_ws를 추적하기 위한 참조 보관
        self._app_ctx: AppServiceContext = default_context_cache

    def _init_message_handlers(self) -> dict[str, Any]:
        handlers = super()._init_message_handlers()
        handlers.update(
            {
                "screenshot-trigger": self._handle_screenshot_trigger,
                "start-continuous-capture": self._handle_start_continuous_capture,
                "stop-continuous-capture": self._handle_stop_continuous_capture,
                "set-dnd": self._handle_set_dnd,
            }
        )
        return handlers  # type: ignore[no-any-return]

    async def _send_initial_messages(  # type: ignore[override]
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: object,
    ) -> None:
        """live2d_model이 None인 경우(모델 미배치)를 안전하게 처리."""
        import json

        await websocket.send_text(
            json.dumps({"type": "full-text", "text": "Connection established"})
        )

        live2d = getattr(session_service_context, "live2d_model", None)
        char_cfg = getattr(session_service_context, "character_config", None)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": live2d.model_info if live2d is not None else None,
                    "conf_name": getattr(char_cfg, "conf_name", ""),
                    "conf_uid": getattr(char_cfg, "conf_uid", ""),
                    "client_uid": client_uid,
                }
            )
        )

        await self.send_group_update(websocket, client_uid)
        await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))

    async def handle_new_connection(self, websocket: WebSocket, client_uid: str) -> None:  # type: ignore[override]
        """신규 연결 시 D-13: 가장 최근 연결된 WS를 _active_ws에 기록.

        B1: super() 성공 후에만 _active_ws 갱신. super()가 예외를 던지면
        _active_ws는 이전 상태(None 또는 이전 WS)를 유지한다.
        upstream _cleanup_failed_connection은 client_connections dict만 비우고
        _active_ws는 건드리지 않으므로 본 오버라이드가 직접 관리해야 한다.
        """
        await super().handle_new_connection(websocket, client_uid)
        # super() 성공(예외 없이 반환)한 경우에만 여기 도달
        self._app_ctx._active_ws = websocket
        logger.debug(f"D-13: _active_ws 갱신 client={client_uid}")

    async def handle_disconnect(self, client_uid: str) -> None:
        """연결 끊김 시 연속 캡처 태스크 정리 후 upstream 처리.

        MAJOR-3: _cancel_continuous_task 호출 전 락을 보유해 경쟁 조건 방지.
        B2: _active_ws가 끊기는 WS를 가리키고 있으면 None으로 리셋.
            식별자 비교(is)를 사용해 다중 클라이언트 상황에서도 안전.
        """
        # disconnect 대상 WS를 미리 확보 (super() 호출 전 client_connections에서 읽기)
        disconnecting_ws = getattr(self, "client_connections", {}).get(client_uid)

        async with self._tasks_lock:
            await self._cancel_continuous_task(client_uid)
        try:
            await super().handle_disconnect(client_uid)
        finally:
            # B2: 식별자 비교로 안전하게 리셋
            if disconnecting_ws is not None and self._app_ctx._active_ws is disconnecting_ws:
                self._app_ctx._active_ws = None
                logger.debug(f"D-13: _active_ws None 리셋 client={client_uid}")

    # ------------------------------------------------------------------ #
    #  신규 핸들러                                                          #
    # ------------------------------------------------------------------ #

    async def _handle_screenshot_trigger(
        self,
        websocket: WebSocket,
        client_uid: str,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        """B-1. screenshot-trigger: 즉시 1회 캡처.

        CR-05 옵션 A 적용:
        - capture_once() 사용 (이미 data URL 형식 반환 — base64 인코딩 불필요).
        - monitor_index / region 인자는 M_05b V1에서 지원하지 않으므로 비기본값 시 WARN 1회 후 무시.
        """
        prompt: str = data.get("prompt", "") or ""
        monitor_index: int = data.get("monitor_index", 0) or 0
        region: dict[str, Any] | None = data.get("region")

        # A-5: prompt 크기 상한 (256 KiB)
        if len(prompt.encode("utf-8")) > _PROMPT_MAX_BYTES:
            logger.warning(f"screenshot-trigger prompt 크기 초과: client={client_uid}")
            await websocket.send_json({"type": "error", "message": "prompt too large: max 256 KiB"})
            return

        # CR-05: monitor_index / region 비기본값 경고 (V1 미지원)
        if monitor_index != 0 or region is not None:
            logger.warning(
                "monitor_index/region은 V1에서 무시됨 (primary monitor 전체만 지원). "
                f"monitor_index={monitor_index}, region={region}"
            )

        # MAJOR #2: _handle_conversation_trigger 호출 전에 session 준비 여부 확인
        if client_uid not in self.client_contexts:
            await websocket.send_json(
                {"type": "error", "message": "screenshot_failed: session not ready"}
            )
            return

        svc_ctx = self.default_context_cache
        screenshot_service = getattr(svc_ctx, "screenshot_service", None)
        if screenshot_service is None:
            await websocket.send_json(
                {"type": "error", "message": "screenshot_failed: screenshot_service not available"}
            )
            return

        try:
            # CR-05: capture_once() 사용 — 반환값이 이미 "data:image/png;base64,..." 형식
            data_url: str = await screenshot_service.capture_once()
        except Exception as exc:
            logger.warning(f"screenshot capture 실패: {exc}")
            await websocket.send_json({"type": "error", "message": f"screenshot_failed: {exc}"})
            return

        # upstream 채팅 트리거로 재진입
        await self._handle_conversation_trigger(
            websocket,
            client_uid,
            {
                "type": "text-input",
                "text": prompt,
                "images": [data_url],
            },
        )

    async def _handle_start_continuous_capture(
        self,
        websocket: WebSocket,
        client_uid: str,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        """B-2. start-continuous-capture: N초 간격 반복 캡처 시작."""
        svc_ctx = self.default_context_cache
        app_config = getattr(svc_ctx, "app_config", None)
        default_interval = (
            app_config.screenshot_continuous_interval_sec if app_config is not None else 5
        )

        raw_interval = data.get("interval_sec")
        interval_sec: int = int(raw_interval) if raw_interval is not None else int(default_interval)
        monitor_index: int = int(data.get("monitor_index") or 0)
        region: dict[str, Any] | None = data.get("region")
        prompt_template: str = data.get("prompt_template", "") or ""

        if not (1 <= interval_sec <= 60):
            await websocket.send_json({"type": "error", "message": "interval_sec must be 1..60"})
            return

        # CR-05: monitor_index / region 비기본값 경고 (V1 미지원)
        if monitor_index != 0 or region is not None:
            logger.warning(
                "monitor_index/region은 V1에서 무시됨 (primary monitor 전체만 지원). "
                f"monitor_index={monitor_index}, region={region}"
            )

        async with self._tasks_lock:
            # 이미 실행 중이면 취소 후 재시작
            await self._cancel_continuous_task(client_uid)

            task = asyncio.create_task(
                self._continuous_capture_loop(
                    websocket, client_uid, interval_sec, monitor_index, prompt_template
                )
            )
            self._continuous_tasks[client_uid] = ContinuousCaptureTask(
                client_uid=client_uid,
                interval_sec=interval_sec,
                monitor_index=monitor_index,
                prompt_template=prompt_template,
                task=task,
            )

        logger.info(f"연속 캡처 시작: client={client_uid}, interval={interval_sec}s")
        await websocket.send_json(
            {"type": "continuous-capture-state", "running": True, "interval_sec": interval_sec}
        )

    async def _handle_stop_continuous_capture(
        self,
        websocket: WebSocket,
        client_uid: str,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        """B-3. stop-continuous-capture: 연속 캡처 중단.

        MAJOR-3: 락을 보유한 상태에서 _cancel_continuous_task 호출.
        """
        async with self._tasks_lock:
            await self._cancel_continuous_task(client_uid)
        logger.info(f"연속 캡처 중단: client={client_uid}")
        await websocket.send_json({"type": "continuous-capture-state", "running": False})

    async def _handle_set_dnd(
        self,
        websocket: WebSocket,
        client_uid: str,
        data: dict,  # type: ignore[type-arg]
    ) -> None:
        """B-4. set-dnd: DND(방해 금지) 상태 설정 (CR-10).

        스펙 §B-4 핸들러 동작 4단계를 엄격하게 구현한다:
        1. enabled bool 타입 검사 (int 1/0 포함 거부 — type() is bool 검사).
        2. proactive_dispatcher None 방어.
        3. dispatcher.set_dnd(enabled) 동기 호출, 예외 격리(WS 연결 유지).
        4. 성공 시 dnd-state 응답 송신.
        """
        enabled = data.get("enabled")

        # 1. 엄격 bool 검사: Python bool은 int의 서브클래스이므로 type() is bool 사용
        if type(enabled) is not bool:
            logger.warning("set-dnd payload invalid: enabled=%r", enabled)
            await websocket.send_json({"type": "error", "message": "set-dnd: enabled must be bool"})
            return

        # 2. proactive_dispatcher None 방어
        dispatcher = self._app_ctx.proactive_dispatcher
        if dispatcher is None:
            logger.warning("set-dnd dropped: proactive_dispatcher not initialized")
            await websocket.send_json(
                {"type": "error", "message": "set-dnd: proactive_dispatcher not initialized"}
            )
            return

        # 3. sync 호출 — await 금지 (M_11 §4 def set_dnd(self, enabled: bool) -> None:)
        try:
            dispatcher.set_dnd(enabled)
        except Exception as exc:
            logger.error("set-dnd dispatcher failed: %s", exc)
            await websocket.send_json({"type": "error", "message": "set-dnd: dispatcher failed"})
            return

        # 4. 성공 응답: 프런트 토글 UI의 SSoT 경로
        await websocket.send_json({"type": "dnd-state", "enabled": enabled})

    # ------------------------------------------------------------------ #
    #  내부 유틸                                                            #
    # ------------------------------------------------------------------ #

    async def _cancel_continuous_task(self, client_uid: str) -> None:
        """_continuous_tasks에서 client_uid 태스크를 취소하고 제거."""
        record = self._continuous_tasks.pop(client_uid, None)
        if record is not None and not record.task.done():
            record.task.cancel()
            try:
                await record.task
            except asyncio.CancelledError:
                pass

    async def _continuous_capture_loop(
        self,
        websocket: WebSocket,
        client_uid: str,
        interval_sec: int,
        monitor_index: int,
        prompt_template: str,
    ) -> None:
        """연속 캡처 루프. 3회 연속 실패 시 루프 종료 + error 메시지."""
        consecutive_failures = 0

        svc_ctx = self.default_context_cache
        screenshot_service = getattr(svc_ctx, "screenshot_service", None)

        while True:
            # MAJOR-1: 첫 캡처를 즉시 수행 후 interval_sec 대기 (sleep-first 제거)
            if screenshot_service is None:
                logger.warning("연속 캡처: screenshot_service 없음, 루프 종료")
                try:
                    await websocket.send_json(
                        {"type": "error", "message": "screenshot_failed: service not available"}
                    )
                except Exception:
                    pass
                break

            try:
                # CR-05: capture_once() 사용 — 반환값이 이미 "data:image/png;base64,..." 형식
                data_url: str = await screenshot_service.capture_once()
                consecutive_failures = 0

                await self._handle_conversation_trigger(
                    websocket,
                    client_uid,
                    {
                        "type": "text-input",
                        "text": prompt_template,
                        "images": [data_url],
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                logger.warning(
                    f"연속 캡처 실패 ({consecutive_failures}/3): client={client_uid}, {exc}"
                )
                if consecutive_failures >= 3:
                    logger.error(f"연속 캡처 3회 연속 실패, 루프 종료: client={client_uid}")
                    try:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"continuous capture failed 3 times: {exc}",
                            }
                        )
                    except Exception:
                        pass
                    async with self._tasks_lock:
                        self._continuous_tasks.pop(client_uid, None)
                    break

            await asyncio.sleep(interval_sec)

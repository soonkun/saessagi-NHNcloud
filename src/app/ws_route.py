# src/app/ws_route.py
"""init_app_ws_route — upstream init_client_ws_route의 AppWebSocketHandler 주입 버전."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, WebSocket
from loguru import logger
from starlette.websockets import WebSocketDisconnect

from .service_context import AppServiceContext
from .ws_handler import AppWebSocketHandler


def _make_locked_send(
    websocket: WebSocket,
) -> Callable[[str], "asyncio.Coroutine[None, None, None]"]:
    """websockets.legacy의 동시 쓰기 AssertionError 방지를 위한 직렬화 래퍼.

    websockets.legacy는 _drain_helper에서 동시 두 코루틴이 write 시도 시
    AssertionError를 발생시킨다. asyncio.Lock으로 직렬화해 이를 방지한다.
    원본 send_text를 먼저 저장해 재귀 호출을 방지한다.
    """
    lock = asyncio.Lock()
    _original = websocket.send_text  # 교체 전 원본 바인딩 저장

    async def locked_send_text(data: str) -> None:
        async with lock:
            await _original(data)

    return locked_send_text  # type: ignore[return-value]


def init_app_ws_route(default_context_cache: AppServiceContext) -> APIRouter:
    """AppWebSocketHandler를 사용하는 /client-ws 라우터 반환.

    upstream routes.py의 init_client_ws_route와 동일한 엔드포인트 로직을 재구현.
    upstream 파일 수정 없이 AppWebSocketHandler를 주입하기 위한 유일한 경로.
    """
    router = APIRouter()
    ws_handler = AppWebSocketHandler(default_context_cache)

    @router.websocket("/client-ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """WebSocket endpoint for client connections."""
        await websocket.accept()

        # websockets.legacy 동시 쓰기 버그 방지용 직렬화 래퍼 주입
        websocket.send_text = _make_locked_send(websocket)  # type: ignore[method-assign]

        client_uid = str(uuid4())

        try:
            await ws_handler.handle_new_connection(websocket, client_uid)
            await ws_handler.handle_websocket_communication(websocket, client_uid)
        except WebSocketDisconnect:
            await ws_handler.handle_disconnect(client_uid)
        except Exception as exc:
            logger.error(f"WebSocket 연결 에러: {exc}")
            await ws_handler.handle_disconnect(client_uid)
            raise

    return router

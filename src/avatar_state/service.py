# src/avatar_state/service.py
"""AvatarState 서비스 클래스.

공개 심볼:
  AvatarState, SendTextCallback
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .tag_parser import extract_emotion as _extract_emotion
from .types import (
    CROSSFADE_DEFAULT_MS,
    AvatarEvent,
    Emotion,
    _VALID_EMOTIONS,
)

# send_text 콜러블 타입 별칭 (src/tool_router/screenshot.py L17 컨벤션과 동일)
SendTextCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AvatarState:
    """아바타 감정 상태 추출 + 이벤트 송신 서비스.

    Attributes:
        _default: 초기 감정. 재시작 시 복귀 지점.
        _last_emotion: 마지막으로 송신 성공한 감정.
        _last_speaking: 마지막으로 송신 성공한 speaking 플래그.
        _send_lock: push_event 직렬화용 asyncio.Lock.
    """

    def __init__(self, default: Emotion = "neutral") -> None:
        """
        Args:
            default: 초기 감정. 기본값 "neutral". 8종 Emotion 중 하나.

        Raises:
            ValueError: default가 _VALID_EMOTIONS(8종) 외.
        """
        if default not in _VALID_EMOTIONS:
            _ordered = (
                "neutral",
                "happy",
                "surprised",
                "sad",
                "worried",
                "thinking",
                "sleepy",
                "study",
                "writing",
            )
            raise ValueError(f"default must be one of {_ordered}, got {default!r}")
        self._default: Emotion = default
        self._last_emotion: Emotion = default
        self._last_speaking: bool = False
        self._send_lock: asyncio.Lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # 공개 property (read-only)
    # -----------------------------------------------------------------------

    @property
    def current_emotion(self) -> Emotion:
        """마지막으로 송신 성공한 감정. 한 번도 송신 안 됐으면 default."""
        return self._last_emotion

    @property
    def is_speaking(self) -> bool:
        """마지막으로 송신 성공한 speaking 플래그."""
        return self._last_speaking

    # -----------------------------------------------------------------------
    # 공개 메서드
    # -----------------------------------------------------------------------

    def extract_emotion(self, text: str) -> tuple[str, Emotion | None]:
        """완결된 응답 문자열에서 `[emotion:<key>]` 태그를 추출·제거한다.

        Args:
            text: 파싱할 텍스트. 완결된 스트림 청크여야 한다(§5.2).
                  빈 문자열 허용.

        Returns:
            (clean_text, emotion):
              - clean_text: 모든 매치된 태그가 제거된 텍스트. 공백 보존(§6.4).
              - emotion: 첫 번째 _SPOKEN_EMOTIONS 소속 키.
                  미지/비발화 키(study 포함) → "neutral" 폴백.
                  태그 없음 → None.

        Raises:
            TypeError: text가 str이 아닐 때.
        """
        return _extract_emotion(text)

    async def push_event(
        self,
        event: AvatarEvent,
        send_text: SendTextCallback,
    ) -> None:
        """WebSocket으로 아바타 상태 이벤트를 송신한다.

        동작:
          (1) AvatarEvent 타입 검증.
          (2) _send_lock 획득(동시 호출 순서 보장).
          (3) send_text(payload) await.
          (4) 성공 시 _last_emotion, _last_speaking 갱신.
          (5) 락 해제.

        Args:
            event: AvatarEvent. 생성자에서 이미 검증됨.
            send_text: dict 페이로드를 받아 비동기 전송하는 콜백.

        Raises:
            TypeError: event가 AvatarEvent가 아닐 때.
            send_text가 던지는 모든 예외를 재시도 없이 전파.
            asyncio.CancelledError: 전파 허용.
        """
        if not isinstance(event, AvatarEvent):
            raise TypeError("event must be AvatarEvent")

        payload: dict[str, Any] = {
            "type": "avatar-state",
            "emotion": event.emotion,
            "crossfade_ms": event.crossfade_ms,
            "speaking": event.speaking,
        }

        async with self._send_lock:
            try:
                await send_text(payload)
            except Exception:
                # _last_* 미갱신. 예외 그대로 전파(D-9).
                logger.error(
                    "push_event: send_text 실패 (emotion={!r}). 상태 미갱신.",
                    event.emotion,
                )
                raise
            # 송신 성공 시에만 상태 갱신
            self._last_emotion = event.emotion
            self._last_speaking = event.speaking

    def set_send_text(self, send_text: SendTextCallback | None) -> None:
        """현재 WebSocket 연결의 send_text 콜백을 저장한다.

        ws_handler가 연결 수립/해제 시 호출. 저장된 콜백은 push_emotion에서 사용.
        """
        self._send_text: SendTextCallback | None = send_text

    async def push_emotion(self, emotion: str) -> None:
        """저장된 send_text로 아바타 상태를 전환한다. 콜백 없으면 스킵.

        tool_call 핸들러 등 send_text 없는 컨텍스트에서 사용.
        """
        send_text = getattr(self, "_send_text", None)
        if send_text is None:
            return
        try:
            event = AvatarEvent(emotion=emotion)  # type: ignore[arg-type]
            await self.push_event(event, send_text)
        except Exception:
            pass

    def make_event(
        self,
        emotion: Emotion,
        *,
        crossfade_ms: int = CROSSFADE_DEFAULT_MS,
        speaking: bool = False,
    ) -> AvatarEvent:
        """AvatarEvent 생성 편의 메서드. __post_init__ 검증 경로 재사용.

        Args:
            emotion: 8종 Emotion 중 하나.
            crossfade_ms: 페이드 시간(ms). 기본 250.
            speaking: 립싱크 ON/OFF. 기본 False.

        Returns:
            AvatarEvent 인스턴스.

        Raises:
            ValueError: emotion이 _VALID_EMOTIONS 외 또는 crossfade_ms 범위 밖.
        """
        return AvatarEvent(emotion=emotion, crossfade_ms=crossfade_ms, speaking=speaking)

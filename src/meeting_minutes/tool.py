# src/meeting_minutes/tool.py
"""M_13 MeetingMinutes — ToolRouter 핸들러."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tool_router.types import ToolResult

if TYPE_CHECKING:
    from avatar_state import AvatarState
    from .service import MeetingMinutesService

logger = logging.getLogger(__name__)


async def _set_writing_state(avatar_state: "AvatarState | None") -> None:
    """회의록 생성 중 writing 아바타 상태 전환."""
    if avatar_state is None:
        return
    await avatar_state.push_emotion("writing")


async def _restore_neutral_state(avatar_state: "AvatarState | None") -> None:
    """회의록 생성 완료 후 neutral 복귀."""
    if avatar_state is None:
        return
    await avatar_state.push_emotion("neutral")


async def handle_create_meeting_minutes(
    service: "MeetingMinutesService | None",
    arguments: dict[str, Any],
    avatar_state: "AvatarState | None" = None,
) -> ToolResult:
    """ToolRouter.dispatch가 호출하는 핸들러.

    arguments 검증된 스키마: {"transcript": str(1~50000), "pages": 1|2}
    반환 ToolResult.payload: {"download_url": str, "file_id": str, "expires_at": str}
    """
    if service is None:
        logger.warning("create_meeting_minutes: service가 None (초기화 실패)")
        return ToolResult(
            ok=False,
            error="meeting_minutes_service를 사용할 수 없습니다. 템플릿 파일을 확인하세요.",
            error_code="service_unavailable",
        )

    transcript: str = arguments["transcript"]
    pages: int = arguments["pages"]

    # pages는 Literal[1,2]이나 JSON Schema에서 int로 검증됨
    if pages not in (1, 2):
        return ToolResult(
            ok=False,
            error=f"pages는 1 또는 2여야 합니다: {pages}",
            error_code="invalid_arguments",
        )

    from .errors import MeetingDraftError, MeetingDraftValidationError, MeetingMinutesError

    await _set_writing_state(avatar_state)
    try:
        result = await service.generate(transcript, pages)  # type: ignore[arg-type]
        logger.info(
            f"create_meeting_minutes 성공: file_id={result['file_id']}, "
            f"download_url={result['download_url']}"
        )
        await _restore_neutral_state(avatar_state)
        return ToolResult(ok=True, payload=result)
    except MeetingDraftError as exc:
        logger.error(f"create_meeting_minutes LLM 실패: {exc}")
        return ToolResult(
            ok=False,
            error=str(exc),
            error_code="invalid_llm_response",
        )
    except MeetingDraftValidationError as exc:
        logger.error(f"create_meeting_minutes Schema 위반: {exc}")
        return ToolResult(
            ok=False,
            error=str(exc),
            error_code="schema_violation",
        )
    except MeetingMinutesError as exc:
        logger.error(f"create_meeting_minutes 일반 오류: {exc}")
        return ToolResult(
            ok=False,
            error=str(exc),
            error_code="hwpx_write_failed",
        )
    except Exception as exc:
        logger.exception(f"create_meeting_minutes 예상치 못한 오류: {exc}")
        await _restore_neutral_state(avatar_state)
        return ToolResult(
            ok=False,
            error=f"예상치 못한 오류: {exc}",
            error_code="handler_exception",
        )

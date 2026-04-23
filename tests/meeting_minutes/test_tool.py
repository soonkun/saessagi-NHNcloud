# tests/meeting_minutes/test_tool.py
"""handle_create_meeting_minutes 핸들러 테스트 (B-3 회귀 포함)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_minutes.errors import (
    MeetingDraftError,
    MeetingDraftValidationError,
    MeetingMinutesError,
)
from meeting_minutes.tool import handle_create_meeting_minutes


def _make_service(side_effect=None, return_value=None) -> MagicMock:
    svc = MagicMock()
    if side_effect is not None:
        svc.generate = AsyncMock(side_effect=side_effect)
    else:
        svc.generate = AsyncMock(return_value=return_value)
    return svc


def _make_avatar() -> MagicMock:
    avatar = MagicMock()
    avatar.push_emotion = AsyncMock()
    return avatar


# ── 정상 경로 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_returns_ok_and_payload() -> None:
    """정상 생성 시 ok=True, payload 반환."""
    payload = {
        "file_id": "abc",
        "download_url": "http://127.0.0.1/download/abc",
        "expires_at": "2026-04-24T00:00:00+00:00",
    }
    svc = _make_service(return_value=payload)
    avatar = _make_avatar()

    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is True
    assert result.payload == payload
    # writing → neutral 순서로 각 1회
    calls = [c.args[0] for c in avatar.push_emotion.call_args_list]
    assert calls == ["writing", "neutral"]


@pytest.mark.asyncio
async def test_success_without_avatar_state() -> None:
    """avatar_state=None이면 오류 없이 진행."""
    svc = _make_service(return_value={"file_id": "x", "download_url": "u", "expires_at": "e"})
    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 2})
    assert result.ok is True


# ── 예외 경로 (B-3 회귀) — 모든 경로에서 neutral 복귀 확인 ──


@pytest.mark.asyncio
async def test_meeting_draft_error_restores_neutral() -> None:
    """MeetingDraftError 발생 시에도 push_emotion('neutral') 호출."""
    svc = _make_service(side_effect=MeetingDraftError("LLM 실패"))
    avatar = _make_avatar()

    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is False
    assert result.error_code == "invalid_llm_response"
    calls = [c.args[0] for c in avatar.push_emotion.call_args_list]
    assert "neutral" in calls, "MeetingDraftError 경로에서 neutral 복귀 누락"


@pytest.mark.asyncio
async def test_meeting_draft_validation_error_restores_neutral() -> None:
    """MeetingDraftValidationError 발생 시에도 push_emotion('neutral') 호출."""
    svc = _make_service(side_effect=MeetingDraftValidationError("스키마 위반"))
    avatar = _make_avatar()

    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is False
    assert result.error_code == "schema_violation"
    calls = [c.args[0] for c in avatar.push_emotion.call_args_list]
    assert "neutral" in calls


@pytest.mark.asyncio
async def test_meeting_minutes_error_restores_neutral() -> None:
    """MeetingMinutesError(HWPX 쓰기 실패) 시에도 push_emotion('neutral') 호출."""
    svc = _make_service(side_effect=MeetingMinutesError("디스크 꽉 참"))
    avatar = _make_avatar()

    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is False
    assert result.error_code == "hwpx_write_failed"
    calls = [c.args[0] for c in avatar.push_emotion.call_args_list]
    assert "neutral" in calls


@pytest.mark.asyncio
async def test_unexpected_exception_restores_neutral() -> None:
    """예상치 못한 예외(RuntimeError 등)에서도 push_emotion('neutral') 호출."""
    svc = _make_service(side_effect=RuntimeError("뭔가 이상한 일"))
    avatar = _make_avatar()

    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is False
    assert result.error_code == "handler_exception"
    calls = [c.args[0] for c in avatar.push_emotion.call_args_list]
    assert "neutral" in calls


# ── 입력 검증 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_none_returns_service_unavailable() -> None:
    """service=None이면 service_unavailable 반환 (avatar_state 상태 변경 없음)."""
    avatar = _make_avatar()
    result = await handle_create_meeting_minutes(None, {"transcript": "내용", "pages": 1}, avatar)

    assert result.ok is False
    assert result.error_code == "service_unavailable"
    avatar.push_emotion.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_pages_returns_invalid_arguments() -> None:
    """pages=3(허용 외)이면 invalid_arguments 반환."""
    svc = _make_service(return_value={})
    result = await handle_create_meeting_minutes(svc, {"transcript": "내용", "pages": 3})

    assert result.ok is False
    assert result.error_code == "invalid_arguments"

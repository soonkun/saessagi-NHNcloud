# tests/tool_router/test_dispatch_normal.py
"""정상 케이스 N-1, N-2, N-3, N-4, N-7."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from src.tool_router.router import ToolRouter

from tests.tool_router.conftest import FakeScreenshotService
from tests.tool_router.fakes import (
    FakeEvent,
)

KST = ZoneInfo("Asia/Seoul")


async def test_n1_add_event_success(router: ToolRouter, mock_calendar: MagicMock) -> None:
    """N-1: add_event 성공."""
    result = await router.dispatch(
        "add_event",
        {
            "title": "회의",
            "start": "2026-04-20T15:00:00+09:00",
            "duration_minutes": 60,
        },
    )
    assert result.ok is True
    assert result.payload["id"] == 42
    assert result.payload["duration_minutes"] == 60
    assert result.payload["title"] == "회의"
    # start가 ISO 문자열
    assert "2026-04-20" in result.payload["start"]
    mock_calendar.add_event.assert_called_once()


async def test_n2_get_events_success(
    mock_calendar: MagicMock, fake_screenshot: FakeScreenshotService
) -> None:
    """N-2: get_events 성공 - 2건 반환."""
    event1 = FakeEvent(
        id=1,
        title="미팅1",
        start=datetime(2026, 4, 20, 9, 0, tzinfo=KST),
        duration_minutes=30,
    )
    event2 = FakeEvent(
        id=2,
        title="미팅2",
        start=datetime(2026, 4, 20, 14, 0, tzinfo=KST),
        duration_minutes=60,
    )
    mock_calendar.get_events.return_value = [event1, event2]

    router = ToolRouter(calendar=mock_calendar, rag=MagicMock(), screenshot=fake_screenshot)
    result = await router.dispatch(
        "get_events",
        {
            "start": "2026-04-20T00:00:00+09:00",
            "end": "2026-04-21T00:00:00+09:00",
        },
    )
    assert result.ok is True
    assert result.payload["count"] == 2
    assert len(result.payload["events"]) == 2


async def test_n3_search_docs_success_with_citation(
    router: ToolRouter, mock_rag: MagicMock
) -> None:
    """N-3: search_docs 성공 - 인용 포함.

    하이브리드 검색은 노트 풀(__knowledge__)·문서 풀(None)을 각각 retrieve한다.
    실데이터에선 category로 분리돼 중복이 없으므로, mock도 노트 풀은 비우고
    문서 풀만 해당 문서 hit를 반환하도록 구성한다.
    """
    full = mock_rag.retrieve.return_value
    empty = MagicMock()
    empty.hits = []
    empty.found = False

    def _retrieve(_query: str, _k: int, category: str | None = None):  # type: ignore[no-untyped-def]
        return empty if category == "__knowledge__" else full

    mock_rag.retrieve.side_effect = _retrieve

    result = await router.dispatch(
        "search_docs",
        {"query": "예산 승인 절차"},
    )
    assert result.ok is True
    assert result.payload["found"] is True
    hits = result.payload["hits"]
    assert len(hits) == 1
    assert hits[0]["citation"] == "`예산지침.pdf` 12페이지, '예산 승인 절차' 섹션"
    assert hits[0]["score"] == pytest.approx(0.72)


async def test_n4_take_screenshot_single(
    router: ToolRouter, fake_screenshot: FakeScreenshotService
) -> None:
    """N-4: take_screenshot 단건 성공."""
    result = await router.dispatch("take_screenshot", {})
    assert result.ok is True
    assert result.payload["mode"] == "single"
    assert result.payload["image"].startswith("data:image/png;base64,")


async def test_n7_take_screenshot_continuous_start(
    mock_calendar: MagicMock, mock_rag: MagicMock
) -> None:
    """N-7: take_screenshot 연속 모드 시작 - privacy_warning 발행."""
    send_text_calls: list[dict] = []

    async def mock_send_text(msg: dict) -> None:
        send_text_calls.append(msg)

    fake_ss = FakeScreenshotService(continuous_running=False)
    fake_ss._send_text = mock_send_text

    router = ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_ss)
    result = await router.dispatch(
        "take_screenshot",
        {"continuous": True, "interval_seconds": 5.0},
    )
    assert result.ok is True
    assert result.payload["mode"] == "continuous"
    assert result.payload["state"] == "started"

    # privacy_warning 발행 확인
    assert len(send_text_calls) == 1
    warning = send_text_calls[0]
    assert warning["type"] == "privacy_warning"
    assert "text" in warning
    assert warning["interval_seconds"] == 5.0


async def test_n8_take_screenshot_continuous_default_interval(
    mock_calendar: MagicMock, mock_rag: MagicMock
) -> None:
    """N-8 (MAJOR-4): interval_seconds 미지정 + continuous=True → 기본값 5.0초로 시작."""
    fake_ss = FakeScreenshotService(continuous_running=False)
    router = ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_ss)

    result = await router.dispatch(
        "take_screenshot",
        {"continuous": True},  # interval_seconds 미지정
    )

    assert result.ok is True
    assert result.payload["mode"] == "continuous"
    assert result.payload["state"] == "started"
    assert result.payload["interval_seconds"] == pytest.approx(5.0)

    # start_continuous가 interval_seconds=5.0으로 호출되었는지 검증
    assert len(fake_ss.start_continuous_calls) == 1
    assert fake_ss.start_continuous_calls[0]["interval_seconds"] == pytest.approx(5.0)

# tests/tool_router/test_dispatch_edge.py
"""엣지 케이스 E-1 ~ E-8."""

from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from src.tool_router.router import ToolRouter

from tests.tool_router.conftest import FakeScreenshotService
from tests.tool_router.fakes import (
    FakeRetrievalResult,
)

KST = ZoneInfo("Asia/Seoul")


async def test_e1_calendar_none_add_event(router_no_calendar: ToolRouter) -> None:
    """E-1: CalendarService=None일 때 add_event → service_unavailable."""
    result = await router_no_calendar.dispatch(
        "add_event",
        {
            "title": "회의",
            "start": "2026-04-20T15:00:00+09:00",
            "duration_minutes": 60,
        },
    )
    assert result.ok is False
    assert result.error_code == "service_unavailable"
    assert result.error is not None
    assert result.error.startswith("service_unavailable")


async def test_e2_rag_none_search_docs(router_no_rag: ToolRouter) -> None:
    """E-2: RagService=None일 때 search_docs → service_unavailable."""
    result = await router_no_rag.dispatch(
        "search_docs",
        {"query": "예산"},
    )
    assert result.ok is False
    assert result.error_code == "service_unavailable"
    assert result.error is not None
    assert result.error.startswith("service_unavailable")


async def test_e3_search_docs_default_top_k(router: ToolRouter, mock_rag: MagicMock) -> None:
    """E-3: search_docs top_k 미지정 → 문서 풀 검색이 기본값 8로 호출.

    하이브리드 검색(노트+문서 이중 retrieve)이므로 retrieve가 2회 호출된다:
    노트 풀(category='__knowledge__', top_k//2)·문서 풀(category=None, top_k).
    여기서는 문서 풀 호출이 top_k=8인지 확인한다.
    """
    await router.dispatch(
        "search_docs",
        {"query": "q"},
    )
    # 노트 풀 + 문서 풀 = 2회 호출
    assert mock_rag.retrieve.call_count == 2
    # 문서 풀 호출(category=None)의 top_k가 8인지 확인
    doc_calls = [
        c
        for c in mock_rag.retrieve.call_args_list
        if (c.args[2] if len(c.args) > 2 else c.kwargs.get("category")) is None
    ]
    assert doc_calls, "문서 풀 호출(category=None)이 없음"
    doc_call = doc_calls[0]
    top_k = doc_call.args[1] if len(doc_call.args) > 1 else doc_call.kwargs.get("top_k")
    assert top_k == 8


async def test_e4_continuous_mode_privacy_warning_fields(
    mock_calendar: MagicMock, mock_rag: MagicMock
) -> None:
    """E-4: privacy_warning 이벤트의 type, text, interval_seconds 3필드 모두 존재."""
    send_text_calls: list[dict] = []

    async def mock_send_text(msg: dict) -> None:
        send_text_calls.append(msg)

    fake_ss = FakeScreenshotService(continuous_running=False)
    fake_ss._send_text = mock_send_text

    router = ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_ss)
    await router.dispatch(
        "take_screenshot",
        {"continuous": True, "interval_seconds": 10.0},
    )

    assert len(send_text_calls) == 1
    warning = send_text_calls[0]
    assert "type" in warning
    assert "text" in warning
    assert "interval_seconds" in warning
    assert warning["type"] == "privacy_warning"
    assert warning["interval_seconds"] == 10.0


async def test_e5_get_events_start_greater_than_end(
    router: ToolRouter, mock_calendar: MagicMock
) -> None:
    """E-5: get_events start > end → ok=True, count=0, events=[], CalendarService 호출 없음."""
    result = await router.dispatch(
        "get_events",
        {
            "start": "2026-05-01T00:00:00+09:00",
            "end": "2026-04-01T00:00:00+09:00",
        },
    )
    assert result.ok is True
    assert result.payload["count"] == 0
    assert result.payload["events"] == []
    # CalendarService.get_events는 호출되지 않음
    mock_calendar.get_events.assert_not_called()


async def test_e6_add_event_description_omitted(
    router: ToolRouter, mock_calendar: MagicMock
) -> None:
    """E-6: add_event description 생략 → CalendarService.add_event(description=None) 호출."""
    result = await router.dispatch(
        "add_event",
        {
            "title": "x",
            "start": "2026-04-20T15:00:00+09:00",
            "duration_minutes": 30,
        },
    )
    assert result.ok is True
    mock_calendar.add_event.assert_called_once()
    call_args = mock_calendar.add_event.call_args
    # positional: title, start, duration_minutes, description
    if call_args.args:
        assert call_args.args[3] is None  # description
    else:
        assert call_args.kwargs.get("description") is None


async def test_e7_search_docs_found_false(
    mock_calendar: MagicMock, fake_screenshot: FakeScreenshotService
) -> None:
    """E-7: search_docs found=False → ok=True, found=False, no_match_reason 존재."""
    mock_rag = MagicMock()
    no_match_msg = "등록된 문서에서 답을 찾지 못했습니다"
    mock_rag.retrieve.return_value = FakeRetrievalResult(
        hits=[], found=False, no_match_reason=no_match_msg
    )
    mock_rag.format_citation.return_value = ""

    router = ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_screenshot)
    result = await router.dispatch("search_docs", {"query": "존재하지 않는 문서"})

    assert result.ok is True
    assert result.payload["found"] is False
    assert result.payload["no_match_reason"] == no_match_msg
    assert result.payload["hits"] == []


async def test_e8_continuous_mode_already_running(
    mock_calendar: MagicMock, mock_rag: MagicMock
) -> None:
    """E-8: is_continuous_running=True일 때 continuous=True 재호출 → already_running."""
    fake_ss = FakeScreenshotService(continuous_running=True)

    router = ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_ss)
    result = await router.dispatch(
        "take_screenshot",
        {"continuous": True, "interval_seconds": 10.0},
    )

    assert result.ok is True
    assert result.payload["state"] == "already_running"
    # start_continuous는 호출되지 않음
    assert len(fake_ss.start_continuous_calls) == 0

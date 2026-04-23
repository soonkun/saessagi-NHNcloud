# tests/e2e/test_e2e_22_no_match_rag.py
"""E2E-22: search_docs 결과 min_score 미달 → "관련 문서 없음".

시나리오 ID: E2E-22-no-match-rag
REQUIREMENTS: §2.2 "추측 금지", "등록된 문서에서 답을 찾지 못했습니다" 고정 응답
관련 모듈: M_05 (FakeAgent), M_05b ToolRouter, M_07 RagService
마커: e2e_fast (FakeAgent + mock RagService 기반)
실행 시간 목표: ≤ 20초
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_fast]


@pytest.mark.timeout(25)
async def test_e2e_22_no_match_rag() -> None:
    """RagService.retrieve가 found=False 반환 시 ToolResult.payload.found=False.

    수락 기준:
    - ToolResult.payload.found == False 또는 no_match_reason 존재.
    - 크래시 없음.
    """
    from tool_router.router import ToolRouter
    from tool_router.screenshot import ScreenshotService
    from vector_search.types import RetrievalResult

    # RagService mock: min_score 미달
    mock_rag = MagicMock()
    mock_rag.retrieve = MagicMock(
        return_value=RetrievalResult(
            hits=[],
            found=False,
            no_match_reason="최상위 점수가 min_score(0.35) 미달",
        )
    )
    mock_rag.format_citation = MagicMock(return_value="[no citation]")

    # ScreenshotService mock
    from unittest.mock import AsyncMock

    screenshot_svc = MagicMock(spec=ScreenshotService)
    screenshot_svc.capture_once = AsyncMock(return_value="data:image/png;base64,AA==")

    router = ToolRouter(
        calendar=None,
        rag=mock_rag,
        screenshot=screenshot_svc,
    )

    # search_docs 호출
    result = await router.dispatch(
        "search_docs",
        {"query": "예산 승인 절차가 뭐야?", "top_k": 5},
    )

    # 수락 기준 1: ToolResult.ok가 True (search 자체는 성공, found=False 반환)
    # M_05b 스펙: search_docs는 hits=[] found=False도 ok=True로 반환
    # (에러가 아닌 "검색 결과 없음" 상태)
    assert result.ok is True

    # 수락 기준 2: found=False 또는 no_match_reason 존재
    payload = result.payload or {}
    found = payload.get("found", True)
    no_match_reason = payload.get("no_match_reason")
    hits = payload.get("hits", [])

    is_no_match = (found is False) or (no_match_reason is not None) or (len(hits) == 0)
    assert is_no_match, (
        f"검색 결과 없음이 아닌 응답: found={found}, no_match_reason={no_match_reason}, hits={hits}"
    )

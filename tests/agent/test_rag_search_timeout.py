"""E-80 회귀: RAG 검색이 멈춰도 턴이 무한정 매달리지 않는다."""

import asyncio
import pytest
from agent.upstream_adapter import _RAG_SEARCH_TIMEOUT_SEC


def test_timeout_is_bounded_and_reasonable() -> None:
    """시한이 없거나(=None) 지나치게 길면 사용자에게는 무한 대기로 보인다.
    정상 검색 실측이 10여 초이므로 그보다 넉넉하되 분 단위를 넘지 않아야 한다."""
    assert _RAG_SEARCH_TIMEOUT_SEC is not None
    assert 15.0 <= _RAG_SEARCH_TIMEOUT_SEC <= 120.0


@pytest.mark.asyncio
async def test_wait_for_gives_up_on_stalled_search() -> None:
    """멈춘 검색을 기다리는 방식이 실제로 포기하는지 — 어댑터가 쓰는 것과 같은 패턴."""

    async def never_returns() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(never_returns(), 0.05)

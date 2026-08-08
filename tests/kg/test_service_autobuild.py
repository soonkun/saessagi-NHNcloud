# tests/kg/test_service_autobuild.py
"""M_23 추출 → 구축 자동 연결 (E-95 회귀).

**조용히 실패했다.** 로그에는 "그래프 구축을 자동으로 이어서 시작합니다"가 찍혔는데
`build` 작업 기록이 아예 없었다. 두 가지가 겹쳤다.

1. `start_build`의 동시 실행 방지 가드가 **자동 연결 자신을 막았다.** 호출 지점이 추출
   태스크 **안**이라 그 시점에 `self.running`이 아직 True다.
2. `start_build`는 예외가 아니라 `{"started": False, "reason": ...}`를 돌려주는데
   호출부가 **반환값을 안 봤다.** 그래서 거부당해도 아무 흔적이 안 남았다.

교훈이 코드로 남아야 하는 자리다 — 반환값으로 실패를 알리는 API는 반환값을 봐야 한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from kg.config import KnowledgeGraphConfig
from kg.service import KnowledgeGraphService


class _FakeVectorStore:
    _tbl = None


@pytest.fixture
def svc(tmp_path: Path) -> KnowledgeGraphService:
    cfg = KnowledgeGraphConfig()
    cfg.candidate_db_path = "kg.db"
    return KnowledgeGraphService(
        config=cfg,
        vector_store=_FakeVectorStore(),
        ollama_base_url="http://127.0.0.1:11434",
        root=tmp_path,
    )


@pytest.mark.asyncio
async def test_guard_blocks_manual_build_during_extraction(svc: KnowledgeGraphService) -> None:
    """수동 구축은 추출 중에 거부되어야 한다 — 반쪽짜리 그래프를 막는 가드."""

    async def _never() -> None:
        await asyncio.sleep(30)

    svc._task = asyncio.create_task(_never())  # noqa: SLF001
    try:
        assert svc.running
        result = await svc.start_build()
        assert result["started"] is False
        assert "추출" in result["reason"]
    finally:
        svc._task.cancel()  # noqa: SLF001


@pytest.mark.asyncio
async def test_auto_chain_bypasses_the_guard(svc: KnowledgeGraphService) -> None:
    """**핵심 회귀** — 추출 자신이 부르는 경우에는 가드를 통과해야 한다.

    자동 연결은 추출 태스크 안에서 호출되므로 그 시점에 self.running이 True다.
    이걸 막으면 자동 연결이 영원히 안 된다.
    """

    async def _never() -> None:
        await asyncio.sleep(30)

    svc._task = asyncio.create_task(_never())  # noqa: SLF001
    try:
        assert svc.running
        result = await svc.start_build(after_extraction=True, dry_run=True)
        assert result["started"] is True, f"자동 연결이 가드에 막혔다: {result.get('reason')}"
    finally:
        svc._task.cancel()  # noqa: SLF001
        if svc._build_task is not None:  # noqa: SLF001
            svc._build_stop.set()  # noqa: SLF001
            await asyncio.gather(svc._build_task, return_exceptions=True)  # noqa: SLF001


@pytest.mark.asyncio
async def test_concurrent_build_still_refused(svc: KnowledgeGraphService) -> None:
    """가드를 열었다고 구축 두 개가 겹치면 안 된다."""
    first = await svc.start_build(after_extraction=True, dry_run=True)
    assert first["started"] is True
    second = await svc.start_build(after_extraction=True, dry_run=True)
    assert second["started"] is False
    assert "구축" in second["reason"]
    svc._build_stop.set()  # noqa: SLF001
    if svc._build_task is not None:  # noqa: SLF001
        await asyncio.gather(svc._build_task, return_exceptions=True)  # noqa: SLF001


def test_start_build_signals_failure_by_return_value() -> None:
    """`start_build`는 예외가 아니라 반환값으로 실패를 알린다 — 호출부가 봐야 한다.

    이 성질이 바뀌면(예외를 던지게 되면) 자동 연결 호출부의 검사도 함께 고쳐야 한다.
    """
    import inspect

    src = inspect.getsource(KnowledgeGraphService._run)
    assert 'result.get("started")' in src, "자동 연결이 start_build 반환값을 검사하지 않는다"


# ── Neo4j 적재 누락 (E-98) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_refuses_when_no_graph_store(svc: KnowledgeGraphService) -> None:
    """스토어 없이 실구축을 시작하지 않는다 (E-98).

    예전엔 조용히 dry-run으로 떨어져 **24분을 돌고 Neo4j에는 아무것도 안 쓴 채**
    COMPLETED가 됐다. 설정 사고는 누르는 즉시 알려야 한다.
    """
    r = await svc.start_build(dry_run=False)
    assert r["started"] is False
    assert "Neo4j" in r["reason"]


@pytest.mark.asyncio
async def test_dry_run_still_allowed_without_store(svc: KnowledgeGraphService) -> None:
    """미리보기는 스토어가 없어도 돌아야 한다 — 거부가 정상 경로를 막으면 안 된다."""
    r = await svc.start_build(dry_run=True)
    assert r["started"] is True
    if svc._build_task is not None:  # noqa: SLF001
        await svc._build_task  # noqa: SLF001


@pytest.mark.asyncio
async def test_build_falls_back_to_wired_factory(tmp_path: Path) -> None:
    """호출자가 팩토리를 안 줘도 배선된 것을 쓴다 (E-98).

    라우트가 자기 팩토리를 따로 만들다 None을 넘긴 것이 사고의 직접 원인이었다.
    """
    made: list[int] = []

    def factory() -> object:
        made.append(1)
        raise RuntimeError("연결 시도까지만 확인")

    cfg = KnowledgeGraphConfig()
    cfg.candidate_db_path = "kg.db"
    svc = KnowledgeGraphService(
        config=cfg,
        vector_store=_FakeVectorStore(),
        ollama_base_url="http://127.0.0.1:11434",
        root=tmp_path,
        graph_store_factory=factory,
    )
    r = await svc.start_build(dry_run=False)  # 팩토리를 안 넘긴다
    assert r["started"] is True, r
    if svc._build_task is not None:  # noqa: SLF001
        await svc._build_task  # noqa: SLF001
    assert made, "배선된 팩토리가 쓰이지 않았다 — 또 조용히 dry-run으로 떨어진다"

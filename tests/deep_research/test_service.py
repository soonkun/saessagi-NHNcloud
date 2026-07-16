# tests/deep_research/test_service.py
"""M_20 DeepResearchService 단위 테스트 (스펙 §7) — Fake agent·retriever 기반."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deep_research.prompts import NO_EVIDENCE_REPORT
from deep_research.service import DeepResearchService
from vector_search.types import RetrievalResult, SearchHit


def _hit(chunk_id: str, text: str = "본문", score: float = 0.8, doc: str = "문서A") -> SearchHit:
    return SearchHit(
        doc_id=f"doc-{doc}",
        doc_name=doc,
        category=None,
        page=1,
        section=None,
        chunk_id=chunk_id,
        text=text,
        bbox=None,
        source_path="",
        score=score,
    )


class FakeAgent:
    """고정 응답 agent — complete_json은 질의 목록, complete_text는 보고서."""

    def __init__(
        self,
        plan_queries: list[str] | None = None,
        gap_queries: list[str] | None = None,
        report: str = "## 보고서\n내용 [1]",
        plan_raises: bool = False,
    ) -> None:
        self.plan_queries = plan_queries if plan_queries is not None else ["질의1", "질의2"]
        self.gap_queries = gap_queries if gap_queries is not None else []
        self.report = report
        self.plan_raises = plan_raises
        self.json_calls = 0
        self.text_calls = 0

    async def complete_json(
        self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any
    ) -> dict[str, Any]:
        self.json_calls += 1
        if self.json_calls == 1:  # 첫 호출 = 플래너
            if self.plan_raises:
                raise ValueError("LLM 응답이 유효한 JSON이 아닙니다")
            return {"sub_queries": self.plan_queries}
        return {"sub_queries": self.gap_queries}  # 이후 = 격차 분석

    async def complete_text(self, system_prompt: str, user_prompt: str, **kw: Any) -> str:
        self.text_calls += 1
        return self.report


class FakeGraphRag:
    """hybrid_retrieve fake — 질의별 고정 hits."""

    def __init__(self, hits_by_query: dict[str, list[SearchHit]], available: bool = True) -> None:
        self.hits_by_query = hits_by_query
        self.available = available
        self.queries: list[str] = []

    async def hybrid_retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
        self.queries.append(query)
        hits = self.hits_by_query.get(query, [])
        return RetrievalResult(hits=hits, found=bool(hits), no_match_reason=None)


class FakeRag:
    """벡터-only retrieve fake (sync — executor 경유 호출)."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    def retrieve(self, query: str, top_k: int = 8, **kw: Any) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(hits=self.hits, found=bool(self.hits), no_match_reason=None)


async def _collect(gen: Any) -> list[dict[str, Any]]:
    return [e async for e in gen]


class TestPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_event_order_and_done(self) -> None:
        agent = FakeAgent(plan_queries=["q1", "q2"])
        graph = FakeGraphRag({"q1": [_hit("c1")], "q2": [_hit("c2", doc="문서B")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run("duplication", "과제 내용"))
        stages = [e["stage"] for e in events]

        assert stages[0] == "planning"
        assert "planned" in stages
        assert "searching" in stages
        assert "synthesis" in stages
        assert stages[-1] == "done"
        # searching은 synthesis보다 먼저
        assert stages.index("searching") < stages.index("synthesis")

        done = events[-1]
        assert done["report"] == agent.report
        assert len(done["sources"]) == 2
        assert done["sources"][0]["n"] == 1
        assert done["sub_queries"] == ["q1", "q2"]
        assert agent.text_calls == 1

    @pytest.mark.asyncio
    async def test_duplicate_chunks_merged(self) -> None:
        same = _hit("dup", score=0.5)
        better = _hit("dup", score=0.9)
        agent = FakeAgent(plan_queries=["q1", "q2"])
        graph = FakeGraphRag({"q1": [same], "q2": [better]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        done = (await _collect(svc.run("discovery", "분야")))[-1]
        assert len(done["sources"]) == 1
        assert done["sources"][0]["score"] == 0.9  # 높은 score로 갱신

    @pytest.mark.asyncio
    async def test_vector_only_fallback_when_graph_none(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        rag = FakeRag([_hit("c1")])
        svc = DeepResearchService(agent, rag, graph_rag_service=None)

        events = await _collect(svc.run("proposal", "RFP 내용"))
        stages = [e["stage"] for e in events]
        assert "notice" in stages  # 벡터-only 고지
        assert rag.queries == ["q1"]
        assert events[-1]["stage"] == "done"

    @pytest.mark.asyncio
    async def test_planning_failure_falls_back_to_single_query(self) -> None:
        agent = FakeAgent(plan_raises=True)
        graph = FakeGraphRag({})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run("duplication", "축산 방역 과제"))
        planned = next(e for e in events if e["stage"] == "planned")
        assert planned["sub_queries"] == ["축산 방역 과제"]

    @pytest.mark.asyncio
    async def test_zero_evidence_returns_no_evidence_report_without_synthesis(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({})  # 모든 질의 0건
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run("duplication", "내용"))
        done = events[-1]
        assert done["stage"] == "done"
        assert done["report"] == NO_EVIDENCE_REPORT
        assert done["sources"] == []
        assert agent.text_calls == 0  # 근거 없이는 LLM 종합 호출 금지 (환각 방지)

    @pytest.mark.asyncio
    async def test_gap_queries_extend_search(self) -> None:
        agent = FakeAgent(plan_queries=["q1"], gap_queries=["보완질의"])
        graph = FakeGraphRag({"q1": [_hit("c1")], "보완질의": [_hit("c2")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        done = (await _collect(svc.run("discovery", "분야")))[-1]
        assert "보완질의" in done["sub_queries"]
        assert len(done["sources"]) == 2


class TestGuards:
    @pytest.mark.asyncio
    async def test_invalid_mode_errors(self) -> None:
        svc = DeepResearchService(FakeAgent(), FakeRag([]))
        events = await _collect(svc.run("bogus", "내용"))
        assert events == [{"stage": "error", "message": "알 수 없는 모드: 'bogus'"}]

    @pytest.mark.asyncio
    async def test_empty_input_errors(self) -> None:
        svc = DeepResearchService(FakeAgent(), FakeRag([]))
        events = await _collect(svc.run("duplication", "   "))
        assert events[0]["stage"] == "error"

    @pytest.mark.asyncio
    async def test_concurrent_run_rejected(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])

        slow_started = asyncio.Event()
        release = asyncio.Event()

        class SlowGraph(FakeGraphRag):
            async def hybrid_retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
                slow_started.set()
                await release.wait()
                return RetrievalResult(hits=[_hit("c1")], found=True, no_match_reason=None)

        svc = DeepResearchService(agent, FakeRag([]), SlowGraph({}))

        async def first() -> list[dict[str, Any]]:
            return await _collect(svc.run("duplication", "첫 실행"))

        task = asyncio.create_task(first())
        await slow_started.wait()

        second = await _collect(svc.run("duplication", "두번째"))
        assert second[0]["stage"] == "error"
        assert "진행 중" in second[0]["message"]

        release.set()
        first_events = await task
        assert first_events[-1]["stage"] == "done"

    @pytest.mark.asyncio
    async def test_attachment_text_included_in_input(self) -> None:
        captured: dict[str, str] = {}

        class CapturingAgent(FakeAgent):
            async def complete_json(
                self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any
            ) -> dict[str, Any]:
                captured.setdefault("planner_user", user_prompt)
                return await super().complete_json(system_prompt, user_prompt, json_schema, **kw)

        agent = CapturingAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        await _collect(svc.run("proposal", "RFP 요약", attachment_text="첨부 RFP 전문"))
        assert "첨부 RFP 전문" in captured["planner_user"]

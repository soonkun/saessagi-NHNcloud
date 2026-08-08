# tests/deep_research/test_service.py
"""M_20 DeepResearchService 단위 테스트 (스펙 §7) — Fake agent·retriever 기반."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deep_research.prompts import NO_EVIDENCE_REPORT
from deep_research.service import DeepResearchService, ResearchProfile
from vector_search.types import RetrievalResult, SearchHit


def _profile(
    name: str = "테스트 방", instructions: str = "테스트 지침", **kw: object
) -> ResearchProfile:
    """CR-62: 모드 문자열 대신 프로필을 넘긴다. 방을 만들 필요 없이 지어 쓴다."""
    return ResearchProfile(project_id=name, name=name, instructions=instructions, **kw)  # type: ignore[arg-type]


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
        self.last_system_prompt = ""
        # CR-62: 플래너·격차·종합에 각각 어떤 system이 갔는지 봐야 방 설정 반영을 검증할 수 있다.
        self.system_prompts: list[str] = []

    async def complete_json(
        self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any
    ) -> dict[str, Any]:
        self.json_calls += 1
        self.system_prompts.append(system_prompt)
        if self.json_calls == 1:  # 첫 호출 = 플래너
            if self.plan_raises:
                raise ValueError("LLM 응답이 유효한 JSON이 아닙니다")
            return {"sub_queries": self.plan_queries}
        return {"sub_queries": self.gap_queries}  # 이후 = 격차 분석

    async def complete_text(self, system_prompt: str, user_prompt: str, **kw: Any) -> str:
        self.text_calls += 1
        self.last_system_prompt = system_prompt
        self.system_prompts.append(system_prompt)
        return self.report


class FakeGraphRag:
    """hybrid_retrieve fake — 질의별 고정 hits."""

    def __init__(self, hits_by_query: dict[str, list[SearchHit]], available: bool = True) -> None:
        self.hits_by_query = hits_by_query
        self.available = available
        self.queries: list[str] = []
        self.sources: list[str] = []

    async def hybrid_retrieve(
        self, query: str, top_k: int = 5, source: str = "both"
    ) -> RetrievalResult:
        # E-96: 딥 리서치는 source="docs"로 부른다 (업무 노트 제외). 실제 서비스가
        # 이 인자를 넘기므로 fake도 받아야 한다 — 안 받으면 TypeError가 검색 실패로
        # 삼켜져 "근거 0건"이 되고, 정작 원인은 안 보인다.
        self.queries.append(query)
        self.sources.append(source)
        hits = self.hits_by_query.get(query, [])
        return RetrievalResult(hits=hits, found=bool(hits), no_match_reason=None)


class FakeRag:
    """벡터-only retrieve fake (sync — executor 경유 호출)."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries: list[str] = []
        self.sources: list[str] = []

    def retrieve(self, query: str, top_k: int = 8, **kw: Any) -> RetrievalResult:
        self.queries.append(query)
        self.sources.append(str(kw.get("source", "both")))
        return RetrievalResult(hits=self.hits, found=bool(self.hits), no_match_reason=None)


def _note_hit(chunk_id: str, title: str = "중복성검토 보고서") -> SearchHit:
    """업무 노트에서 온 근거 — 딥 리서치가 자기 출력을 다시 읽는 상황 (E-96)."""
    return SearchHit(
        doc_id=f"__knowledge__:{title}",
        doc_name=title,
        category="__knowledge__",
        page=None,
        section=None,
        chunk_id=chunk_id,
        text="이전 딥 리서치가 생성한 보고서 본문",
        bbox=None,
        source_path="",
        score=0.95,
    )


async def _collect(gen: Any) -> list[dict[str, Any]]:
    return [e async for e in gen]


class TestPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_event_order_and_done(self) -> None:
        agent = FakeAgent(plan_queries=["q1", "q2"])
        graph = FakeGraphRag({"q1": [_hit("c1")], "q2": [_hit("c2", doc="문서B")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run(_profile("duplication"), "과제 내용"))
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

        done = (await _collect(svc.run(_profile("discovery"), "분야")))[-1]
        assert len(done["sources"]) == 1
        assert done["sources"][0]["score"] == 0.9  # 높은 score로 갱신

    @pytest.mark.asyncio
    async def test_vector_only_fallback_when_graph_none(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        rag = FakeRag([_hit("c1")])
        svc = DeepResearchService(agent, rag, graph_rag_service=None)

        events = await _collect(svc.run(_profile("proposal"), "RFP 내용"))
        stages = [e["stage"] for e in events]
        assert "notice" in stages  # 벡터-only 고지
        assert rag.queries == ["q1"]
        assert events[-1]["stage"] == "done"

    @pytest.mark.asyncio
    async def test_planning_failure_falls_back_to_single_query(self) -> None:
        agent = FakeAgent(plan_raises=True)
        graph = FakeGraphRag({})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run(_profile("duplication"), "축산 방역 과제"))
        planned = next(e for e in events if e["stage"] == "planned")
        assert planned["sub_queries"] == ["축산 방역 과제"]

    @pytest.mark.asyncio
    async def test_zero_evidence_returns_no_evidence_report_without_synthesis(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({})  # 모든 질의 0건
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(svc.run(_profile("duplication"), "내용"))
        done = events[-1]
        assert done["stage"] == "done"
        assert done["report"] == NO_EVIDENCE_REPORT
        assert done["sources"] == []
        assert agent.text_calls == 0  # 근거 없이는 LLM 종합 호출 금지 (환각 방지)

    @pytest.mark.asyncio
    async def test_gap_queries_extend_search(self) -> None:
        agent = FakeAgent(plan_queries=["q1"], gap_queries=["보완질의"])
        # 서로 다른 문서 → 참고자료 2건 (gap 질의가 새 문서를 추가)
        graph = FakeGraphRag(
            {"q1": [_hit("c1", doc="문서A")], "보완질의": [_hit("c2", doc="문서B")]}
        )
        svc = DeepResearchService(agent, FakeRag([]), graph)

        done = (await _collect(svc.run(_profile("discovery"), "분야")))[-1]
        assert "보완질의" in done["sub_queries"]
        assert len(done["sources"]) == 2

    async def test_sources_deduped_by_document(self) -> None:
        """같은 문서의 여러 청크는 참고자료 1건으로 (최고 score 대표)."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag(
            {"q1": [_hit("c1", doc="문서A", score=0.7), _hit("c2", doc="문서A", score=0.9)]}
        )
        svc = DeepResearchService(agent, FakeRag([]), graph)
        done = (await _collect(svc.run(_profile("discovery"), "분야")))[-1]
        assert len(done["sources"]) == 1  # 두 청크 → 한 문서
        assert done["sources"][0]["score"] == 0.9  # 최고 score 대표


class TestGuards:
    @pytest.mark.asyncio
    async def test_unknown_project_name_is_not_an_error(self) -> None:
        """CR-62: 모드 화이트리스트를 없앴다. 방 이름이 무엇이든 파이프라인은 돌아야 한다 —
        방은 사용자가 자유롭게 만드는 것이라 '알 수 없는 모드'라는 개념 자체가 없다."""
        svc = DeepResearchService(FakeAgent(), FakeRag([]))
        events = await _collect(svc.run(_profile("사내 교육자료 정리"), "내용"))
        assert events[0]["stage"] != "error"
        assert events[-1]["stage"] == "done"

    @pytest.mark.asyncio
    async def test_empty_input_errors(self) -> None:
        svc = DeepResearchService(FakeAgent(), FakeRag([]))
        events = await _collect(svc.run(_profile("duplication"), "   "))
        assert events[0]["stage"] == "error"

    @pytest.mark.asyncio
    async def test_concurrent_run_waits_in_queue(self) -> None:
        """CR-61: 겹친 요청은 거절하지 않고 줄을 선다.

        여러 사람이 같이 쓰는 상황에서 "이미 진행 중"만 뱉으면 언제 다시 눌러야 할지
        알 수 없고 그냥 실패로 보인다(실제로 5대에서 동시 실행 시 4대가 그랬다).
        """
        agent = FakeAgent(plan_queries=["q1"])

        slow_started = asyncio.Event()
        release = asyncio.Event()

        class SlowGraph(FakeGraphRag):
            async def hybrid_retrieve(
                self, query: str, top_k: int = 5, source: str = "both"
            ) -> RetrievalResult:
                slow_started.set()
                await release.wait()
                return RetrievalResult(hits=[_hit("c1")], found=True, no_match_reason=None)

        svc = DeepResearchService(agent, FakeRag([]), SlowGraph({}))

        async def first() -> list[dict[str, Any]]:
            return await _collect(svc.run(_profile("duplication"), "첫 실행"))

        task = asyncio.create_task(first())
        await slow_started.wait()

        async def second() -> list[dict[str, Any]]:
            return await _collect(svc.run(_profile("duplication"), "두번째"))

        second_task = asyncio.create_task(second())
        # 두 번째는 대기 안내를 받고 기다린다 — 여기서 끝나지 않아야 한다.
        await asyncio.sleep(0.05)
        assert not second_task.done()

        release.set()
        first_events = await task
        assert first_events[-1]["stage"] == "done"

        second_events = await asyncio.wait_for(second_task, timeout=5)
        stages = [e["stage"] for e in second_events]
        assert "queued" in stages, stages
        assert stages[-1] == "done", stages
        queued = next(e for e in second_events if e["stage"] == "queued")
        assert queued["position"] >= 1
        assert "대기" in queued["message"]

    @pytest.mark.asyncio
    async def test_queue_disabled_rejects_immediately(self) -> None:
        """max_queue_size=0이면 예전처럼 즉시 거절 — 설정으로 되돌릴 수 있어야 한다."""
        agent = FakeAgent(plan_queries=["q1"])
        slow_started = asyncio.Event()
        release = asyncio.Event()

        class SlowGraph(FakeGraphRag):
            async def hybrid_retrieve(
                self, query: str, top_k: int = 5, source: str = "both"
            ) -> RetrievalResult:
                slow_started.set()
                await release.wait()
                return RetrievalResult(hits=[_hit("c1")], found=True, no_match_reason=None)

        svc = DeepResearchService(agent, FakeRag([]), SlowGraph({}), max_queue_size=0)
        task = asyncio.create_task(_collect(svc.run(_profile("duplication"), "첫 실행")))
        await slow_started.wait()

        second = await _collect(svc.run(_profile("duplication"), "두번째"))
        assert second[0]["stage"] == "error"
        assert "진행 중" in second[0]["message"]

        release.set()
        await task

    @pytest.mark.asyncio
    async def test_queue_full_is_rejected(self) -> None:
        """대기열 상한을 넘으면 무한정 쌓지 않고 거절한다."""
        agent = FakeAgent(plan_queries=["q1"])
        slow_started = asyncio.Event()
        release = asyncio.Event()

        class SlowGraph(FakeGraphRag):
            async def hybrid_retrieve(
                self, query: str, top_k: int = 5, source: str = "both"
            ) -> RetrievalResult:
                slow_started.set()
                await release.wait()
                return RetrievalResult(hits=[_hit("c1")], found=True, no_match_reason=None)

        svc = DeepResearchService(agent, FakeRag([]), SlowGraph({}), max_queue_size=1)
        first = asyncio.create_task(_collect(svc.run(_profile("duplication"), "첫 실행")))
        await slow_started.wait()
        waiting = asyncio.create_task(_collect(svc.run(_profile("duplication"), "대기 1")))
        await asyncio.sleep(0.05)

        third = await _collect(svc.run(_profile("duplication"), "대기 2"))
        assert third[0]["stage"] == "error"
        assert "대기열이 가득" in third[0]["message"]

        release.set()
        await first
        await asyncio.wait_for(waiting, timeout=5)

    @pytest.mark.asyncio
    async def test_queue_wait_timeout(self) -> None:
        """앞 작업이 끝나지 않으면 영원히 매달리지 않고 안내하고 끝낸다."""
        agent = FakeAgent(plan_queries=["q1"])
        slow_started = asyncio.Event()
        release = asyncio.Event()

        class SlowGraph(FakeGraphRag):
            async def hybrid_retrieve(
                self, query: str, top_k: int = 5, source: str = "both"
            ) -> RetrievalResult:
                slow_started.set()
                await release.wait()
                return RetrievalResult(hits=[_hit("c1")], found=True, no_match_reason=None)

        svc = DeepResearchService(agent, FakeRag([]), SlowGraph({}), max_queue_wait_seconds=0.05)
        first = asyncio.create_task(_collect(svc.run(_profile("duplication"), "첫 실행")))
        await slow_started.wait()

        second = await asyncio.wait_for(
            _collect(svc.run(_profile("duplication"), "두번째")), timeout=30
        )
        assert second[-1]["stage"] == "error"
        assert "기다리기를 멈췄" in second[-1]["message"]

        release.set()
        await first

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

        await _collect(svc.run(_profile("proposal"), "RFP 요약", attachment_text="첨부 RFP 전문"))
        assert "첨부 RFP 전문" in captured["planner_user"]


class TestScopeFilter:
    @pytest.mark.asyncio
    async def test_scope_filters_hits_to_given_docs(self) -> None:
        """scope_doc_ids 지정 시 해당 문서의 hit만 근거로 채택."""
        agent = FakeAgent(plan_queries=["q1"])
        in_scope = _hit("c1", doc="범위문서")
        out_scope = _hit("c2", doc="다른문서", score=0.95)
        graph = FakeGraphRag({"q1": [in_scope, out_scope]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(
            svc.run(_profile("duplication"), "내용", scope_doc_ids=["doc-범위문서"])
        )
        done = events[-1]
        assert done["stage"] == "done"
        assert [s["doc_id"] for s in done["sources"]] == ["doc-범위문서"]
        # 범위 고지 이벤트
        assert any(e["stage"] == "notice" and "범위" in e.get("message", "") for e in events)

    @pytest.mark.asyncio
    async def test_scope_all_filtered_out_yields_no_evidence(self) -> None:
        """범위 밖 hit만 나오면 근거 0건 처리 (환각 방지 경로)."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1", doc="다른문서")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)

        events = await _collect(
            svc.run(_profile("discovery"), "내용", scope_doc_ids=["doc-없는문서"])
        )
        done = events[-1]
        assert done["sources"] == []
        assert agent.text_calls == 0


class TestCustomPrompt:
    """CR-62: 방 지침이 실제 LLM 호출에 반영되는지."""

    @pytest.mark.asyncio
    async def test_custom_instructions_reach_the_llm_call(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        await _collect(svc.run(_profile(instructions="커스텀-마커-XYZ"), "과제 내용"))

        assert "커스텀-마커-XYZ" in agent.last_system_prompt

    @pytest.mark.asyncio
    async def test_safety_rules_survive_custom_override(self) -> None:
        """지침을 갈아끼워도 근거 인용 강제(EVIDENCE_RULES)는 항상 붙어야 한다 —
        그렇지 않으면 사용자가 실수로 환각 억제 장치를 지워버릴 수 있다."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        await _collect(svc.run(_profile(instructions="완전히 새로운 지침"), "RFP 내용"))

        assert "완전히 새로운 지침" in agent.last_system_prompt
        assert "근거 사용 절대 규칙" in agent.last_system_prompt

    @pytest.mark.asyncio
    async def test_empty_instructions_fall_back_to_generic(self) -> None:
        """지침이 비면 **분야를 가정하지 않는** 범용 기본값으로 간다 (CR-62).

        예전에는 모드별 기본값(중복성 검토 어투)으로 폴백해서, 지침을 비운 새 방이
        느닷없이 '냉정한 평가위원'이 되곤 했다."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        await _collect(svc.run(_profile(instructions=""), "분야"))

        assert "조사 보고서를 쓰는 분석가" in agent.last_system_prompt
        assert "평가위원" not in agent.last_system_prompt

    @pytest.mark.asyncio
    async def test_planner_hint_reaches_planner_only(self) -> None:
        """방의 관점 예시는 플래너에만 붙고 종합 프롬프트를 오염시키지 않아야 한다."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        await _collect(
            svc.run(_profile(instructions="종합 지침", planner_hint="힌트-마커-ABC"), "내용")
        )

        assert "힌트-마커-ABC" in agent.system_prompts[0], "플래너에 힌트가 안 갔다"
        assert "힌트-마커-ABC" not in agent.last_system_prompt, "종합에 힌트가 샜다"


class TestSearchSettings:
    """CR-62: 검색 깊이가 방마다 달라야 한다. 예전에는 전역 상수라 못 바꿨다."""

    @pytest.mark.asyncio
    async def test_sub_queries_limit_is_per_project(self) -> None:
        agent = FakeAgent(plan_queries=["q1", "q2", "q3", "q4", "q5", "q6"])
        graph = FakeGraphRag({f"q{i}": [_hit(f"c{i}")] for i in range(1, 7)})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        events = await _collect(svc.run(_profile(sub_queries=2), "내용"))

        planned = next(e for e in events if e["stage"] == "planned")
        assert len(planned["sub_queries"]) == 2, "방의 질의 수 상한이 무시됐다"

    @pytest.mark.asyncio
    async def test_gap_rounds_zero_skips_gap_analysis(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        events = await _collect(svc.run(_profile(gap_rounds=0), "내용"))

        assert not any(e["stage"] == "gap_analysis" for e in events)
        assert events[-1]["stage"] == "done"

    @pytest.mark.asyncio
    async def test_gap_rounds_stop_early_when_nothing_new(self) -> None:
        """새 근거가 안 늘면 라운드를 더 돌 이유가 없다 — LLM 호출 낭비."""
        agent = FakeAgent(plan_queries=["q1"], gap_queries=["q1"])  # 같은 질의 → 신규 0
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        events = await _collect(svc.run(_profile(gap_rounds=3), "내용"))

        assert sum(1 for e in events if e["stage"] == "gap_analysis") == 1


class TestNotesExcluded:
    """E-96 — 업무 노트는 근거로 쓰지 않는다.

    딥 리서치 보고서는 "업무노트로 저장"으로 노트가 되고 벡터 스토어에 들어간다.
    그걸 다시 검색하면 **자기 출력이 자기 근거가 되는 순환**이 생긴다 — LLM이 지어낸
    문장이 다음 보고서에서 `[3]`으로 인용된 사실로 승격된다.
    실측으로 벡터 스토어의 노트 4건이 전부 딥 리서치 산출물이었다.
    """

    @pytest.mark.asyncio
    async def test_retrieval_asks_for_docs_only(self) -> None:
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_hit("c1")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        await _collect(svc.run(_profile(), "내용"))

        assert graph.sources, "검색이 호출되지 않았다"
        assert all(s == "docs" for s in graph.sources), f"노트를 포함해 검색했다: {graph.sources}"

    @pytest.mark.asyncio
    async def test_note_hits_are_filtered_out(self) -> None:
        """검색이 노트를 섞어 와도 근거에서 걸러야 한다 (그래프 경로 방어)."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_note_hit("n1"), _hit("c1", doc="진짜문서")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        done = (await _collect(svc.run(_profile(), "내용")))[-1]

        names = [s["doc_name"] for s in done["sources"]]
        assert "진짜문서" in names
        assert not any("중복성검토" in n for n in names), f"노트가 근거에 섞였다: {names}"

    @pytest.mark.asyncio
    async def test_only_notes_found_yields_no_evidence(self) -> None:
        """노트만 걸리면 근거 0건으로 처리한다 — 순환으로 보고서를 쓰느니 안 쓴다."""
        agent = FakeAgent(plan_queries=["q1"])
        graph = FakeGraphRag({"q1": [_note_hit("n1"), _note_hit("n2", "다른 보고서")]})
        svc = DeepResearchService(agent, FakeRag([]), graph)
        done = (await _collect(svc.run(_profile(), "내용")))[-1]

        assert done["sources"] == []
        assert done["report"] == NO_EVIDENCE_REPORT

    @pytest.mark.asyncio
    async def test_vector_only_path_also_excludes_notes(self) -> None:
        """그래프 미가용 폴백 경로도 같아야 한다."""
        agent = FakeAgent(plan_queries=["q1"])
        rag = FakeRag([_hit("c1")])
        svc = DeepResearchService(agent, rag, None)
        await _collect(svc.run(_profile(), "내용"))

        assert all(s == "docs" for s in rag.sources), f"벡터 경로가 노트를 포함했다: {rag.sources}"

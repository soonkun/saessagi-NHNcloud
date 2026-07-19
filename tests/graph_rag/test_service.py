# tests/graph_rag/test_service.py
"""GraphRagService 단위 테스트 — CR-30 Project + 역할 키워드 스키마.

(구) 범용 엔티티 파이프라인 테스트는 CR-30에서 스키마와 함께 폐기·대체됐다.
"""

from __future__ import annotations

from typing import Any

import pytest

from graph_rag.extractor import EntityExtractor
from graph_rag.service import GraphRagService
from graph_rag.types import KeywordMention, ProjectInfo
from vector_search.types import RetrievalResult, SearchHit

from .fakes import FakeCompleteJson, FakeGraphStore, FakeVectorStore, make_row


def _hit(chunk_id: str, doc_id: str = "docV", score: float = 0.8) -> SearchHit:
    return SearchHit(
        doc_id=doc_id,
        doc_name="벡터문서.pdf",
        category=None,
        page=1,
        section=None,
        chunk_id=chunk_id,
        text=f"벡터 청크 {chunk_id}",
        bbox=None,
        source_path="",
        score=score,
    )


class FakeRagService:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def retrieve(self, query: str, top_k: int = 8, **kw: Any) -> RetrievalResult:
        self.calls.append(query)
        return self.result


def _make_service(
    graph: FakeGraphStore | None = None,
    rows: list[dict[str, Any]] | None = None,
    vector_result: RetrievalResult | None = None,
    responses: dict[str, dict[str, Any]] | None = None,
) -> tuple[GraphRagService, FakeGraphStore, FakeRagService]:
    graph = graph or FakeGraphStore()
    vstore = FakeVectorStore(rows or [])
    rag = FakeRagService(
        vector_result or RetrievalResult(hits=[], found=False, no_match_reason="없음")
    )
    svc = GraphRagService(
        graph_store=graph,
        vector_store=vstore,
        extractor=EntityExtractor(complete_json=FakeCompleteJson(responses or {})),
        rag_service=rag,
        max_hops=2,
    )
    return svc, graph, rag


def _seed_keywords(graph: FakeGraphStore) -> None:
    """docG(가루쌀 과제: 키워드 2) / docH(스마트팜: 키워드 1)."""
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="docG", title="가루쌀 미강유 과제", project_no="PJ0123"),
        [
            KeywordMention(
                doc_id="docG", raw_term="가루쌀 미강유", role="research_target", confidence=0.9
            ),
            KeywordMention(
                doc_id="docG", raw_term="저장기간 품질변화", role="problem", confidence=0.8
            ),
        ],
    )
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="docH", title="스마트팜 열환경"),
        [
            KeywordMention(
                doc_id="docH", raw_term="스마트팜 센서", role="technology", confidence=0.7
            )
        ],
    )


_ROWS = [
    make_row("c1", doc_id="docG", text="가루쌀 미강유 본문 1"),
    make_row("c2", doc_id="docG", text="가루쌀 저장기간 본문 2"),
    make_row("c3", doc_id="docH", text="스마트팜 센서 본문"),
]


# ── graph_retrieve (CR-30: 키워드 → 과제 → 청크) ─────────────────────────────


@pytest.mark.asyncio
async def test_graph_retrieve_matches_keywords_to_doc_chunks() -> None:
    """정상: 키워드 매칭 → 소속 문서의 청크가 hit로 반환, evidence에 문서+키워드."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    hits, evidence = await svc.graph_retrieve("가루쌀 미강유 관련 과제 찾아줘", top_k=5)
    assert {h.chunk_id for h in hits} & {"c1", "c2"}
    assert evidence is not None
    assert any(n.kind == "document" for n in evidence.nodes)
    assert any(n.kind == "keyword" and n.type == "research_target" for n in evidence.nodes)


@pytest.mark.asyncio
async def test_graph_retrieve_no_match_returns_empty() -> None:
    """엣지: 매칭 키워드 0건 → 빈 결과."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    hits, evidence = await svc.graph_retrieve("전혀무관한질의어", top_k=5)
    assert hits == [] and evidence is None


@pytest.mark.asyncio
async def test_graph_retrieve_empty_query() -> None:
    """엣지: 빈 질의/1자 단어 → 빈 결과."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    assert (await svc.graph_retrieve("", top_k=5))[0] == []


@pytest.mark.asyncio
async def test_graph_retrieve_missing_chunks_skipped() -> None:
    """엣지: 키워드는 매칭됐지만 벡터 스토어에 청크가 없는 문서는 조용히 스킵."""
    svc, graph, _ = _make_service(rows=[make_row("c3", doc_id="docH", text="스마트팜")])
    _seed_keywords(graph)  # docG 청크는 벡터 스토어에 없음
    hits, _ = await svc.graph_retrieve("가루쌀 미강유", top_k=5)
    assert all(h.doc_id != "docG" for h in hits)


@pytest.mark.asyncio
async def test_graph_retrieve_scores_by_keyword_count() -> None:
    """정상: 매칭 키워드가 많은 문서의 청크가 더 높은 점수."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    # docG 키워드 2개("가루쌀 미강유"+"저장기간 품질변화") vs docH 1개 매칭
    hits, _ = await svc.graph_retrieve("가루쌀 미강유 저장기간 품질변화 스마트팜 센서", top_k=5)
    score_g = max((h.score for h in hits if h.doc_id == "docG"), default=0.0)
    score_h = max((h.score for h in hits if h.doc_id == "docH"), default=0.0)
    assert score_g >= score_h > 0.0


# ── hybrid_retrieve ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_fuses_vector_and_graph() -> None:
    """정상: RRF 융합 — 양쪽 상위 청크가 결과에 공존."""
    vec = RetrievalResult(
        hits=[_hit("v1"), _hit("v2", score=0.7)], found=True, no_match_reason=None
    )
    svc, graph, _ = _make_service(rows=_ROWS, vector_result=vec)
    _seed_keywords(graph)
    result = await svc.hybrid_retrieve("가루쌀 미강유 이야기", top_k=4)
    ids = {h.chunk_id for h in result.hits}
    assert "v1" in ids  # 벡터 유래
    assert ids & {"c1", "c2"}  # 그래프 유래
    assert result.found is True


@pytest.mark.asyncio
async def test_hybrid_graph_rescues_vector_miss() -> None:
    """정상: 벡터 found=False여도 그래프 hit가 있으면 found=True (구제)."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    result = await svc.hybrid_retrieve("가루쌀 미강유", top_k=3)
    assert result.found is True
    assert result.hits


@pytest.mark.asyncio
async def test_hybrid_fallback_when_store_down() -> None:
    """엣지: 그래프 저장소 다운 → 벡터 결과 그대로 (예외 없음)."""
    vec = RetrievalResult(hits=[_hit("v1")], found=True, no_match_reason=None)
    svc, graph, rag = _make_service(
        graph=FakeGraphStore(alive=False), rows=_ROWS, vector_result=vec
    )
    result = await svc.hybrid_retrieve("가루쌀", top_k=3)
    assert [h.chunk_id for h in result.hits] == ["v1"]
    assert rag.calls  # 벡터 경로는 호출됨


@pytest.mark.asyncio
async def test_hybrid_stores_evidence() -> None:
    """정상: 융합 후 latest_evidence에 근거 서브그래프 보관."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_keywords(graph)
    await svc.hybrid_retrieve("가루쌀 미강유 과제", top_k=3)
    ev = svc.latest_evidence()
    assert ev is not None
    assert ev.chunk_ids


# ── 인덱싱 (CR-30: 문서 단위 추출) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_document_extracts_project_and_keywords() -> None:
    """정상: 문서 인덱싱 → 과제 속성(title/project_no) + 역할 키워드 저장."""
    resp = {
        "title": "가루쌀 미강유 기능성분 구명",
        "rfp_no": "RFP-23-01",
        "project_no": "PJ0123",
        "keywords": [
            {"term": "가루쌀 미강유", "role": "research_target", "confidence": 0.9},
            {"term": "저장기간 품질변화", "role": "problem", "confidence": 0.8},
        ],
    }
    svc, graph, _ = _make_service(
        rows=[make_row("c1", doc_id="d1", text="가루쌀 미강유 연구에 대한 문서 본문. " * 3)],
        responses={"가루쌀": resp},
    )
    status = await svc.index_document("d1")
    assert status.state == "done"
    proj = graph.projects["d1"]
    assert proj.title == "가루쌀 미강유 기능성분 구명"
    assert proj.rfp_no == "RFP-23-01" and proj.project_no == "PJ0123"
    kws = graph.keywords_for_doc("d1")
    assert {k.raw_term for k in kws} == {"가루쌀 미강유", "저장기간 품질변화"}
    # 원시 키워드 보존 + 정규화 전 상태
    assert all(k.normalization_status == "raw" and k.normalized_term == "" for k in kws)


@pytest.mark.asyncio
async def test_index_document_reindex_replaces_keywords() -> None:
    """정상: 재인덱싱은 문서 키워드를 교체 (멱등 — 중복 누적 없음)."""
    resp1 = {"title": "T", "keywords": [{"term": "옛 키워드", "role": "technology"}]}
    resp2 = {"title": "T", "keywords": [{"term": "새 키워드", "role": "technology"}]}
    rows = [make_row("c1", doc_id="d1", text="공통 본문 텍스트입니다. " * 3)]
    svc1, graph, _ = _make_service(rows=rows, responses={"공통": resp1})
    await svc1.index_document("d1")
    svc2 = GraphRagService(
        graph_store=graph,
        vector_store=FakeVectorStore(rows),
        extractor=EntityExtractor(complete_json=FakeCompleteJson({"공통": resp2})),
        rag_service=FakeRagService(RetrievalResult(hits=[], found=False, no_match_reason="x")),
    )
    await svc2.index_document("d1")
    kws = graph.keywords_for_doc("d1")
    assert [k.raw_term for k in kws] == ["새 키워드"]


@pytest.mark.asyncio
async def test_index_note_uses_note_parent() -> None:
    """정상: __knowledge__ 카테고리 doc_id → Note 노드 생성 유지."""
    resp = {"title": "주간회의", "keywords": []}
    svc, graph, _ = _make_service(
        rows=[
            make_row(
                "n1",
                doc_id="__knowledge__:weekly",
                text="주간 회의 내용을 정리한 업무 노트다. " * 2,
                category="__knowledge__",
                doc_name="주간회의",
            )
        ],
        responses={"회의": resp},
    )
    status = await svc.index_document("__knowledge__:weekly")
    assert status.state == "done"
    assert "weekly" in graph.notes


@pytest.mark.asyncio
async def test_index_document_store_down_fails_gracefully() -> None:
    """엣지: 저장소 다운 → failed 상태, 예외 없음."""
    svc, _, _ = _make_service(graph=FakeGraphStore(alive=False), rows=_ROWS)
    status = await svc.index_document("docG")
    assert status.state == "failed"


@pytest.mark.asyncio
async def test_index_document_llm_failure_yields_empty_keywords() -> None:
    """엣지: LLM 실패 → 키워드 0건으로 완료 (제목은 doc_name 폴백, 전체 실패 금지)."""
    fake = FakeCompleteJson({})
    fake.fail = True
    svc, graph, _ = _make_service(rows=[make_row("c1", doc_id="d1", text="본문 텍스트. " * 5)])
    svc._extractor = EntityExtractor(complete_json=fake)  # type: ignore[arg-type]
    status = await svc.index_document("d1")
    assert status.state == "done"
    assert graph.projects["d1"].title == "문서.pdf"  # doc_name 폴백
    assert graph.keywords_for_doc("d1") == []


# ── CR-30 키워드 정규화 (노드 병합 없음) ─────────────────────────────────────


class _NormalizeLLM:
    def __init__(self, groups: list[list[str]]) -> None:
        self.groups = groups

    async def __call__(
        self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any
    ) -> dict[str, Any]:
        return {"groups": self.groups}


@pytest.mark.asyncio
async def test_normalize_updates_terms_without_merging_nodes() -> None:
    """정규화: normalized_term만 갱신 — raw_term·문서별 노드 보존 (전역 병합 금지)."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="A"),
        [KeywordMention(doc_id="d1", raw_term="가루쌀 미강유", role="research_target")],
    )
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="d2", title="B"),
        [KeywordMention(doc_id="d2", raw_term="미강유", role="research_target")],
    )
    svc, _, _ = _make_service(graph=graph)
    svc._extractor = EntityExtractor(  # type: ignore[assignment]
        complete_json=_NormalizeLLM([["가루쌀 미강유", "미강유"]])  # type: ignore[arg-type]
    )

    result = await svc.normalize_entities()
    assert result["merged"] == 2  # 두 문서의 언급 모두 갱신
    kws = graph.all_keywords()
    assert len(kws) == 2  # 노드 수 불변 (병합 없음)
    assert all(k.normalized_term == "가루쌀 미강유" for k in kws)
    assert all(k.normalization_status == "normalized" for k in kws)
    assert {k.raw_term for k in kws} == {"가루쌀 미강유", "미강유"}  # raw 보존


@pytest.mark.asyncio
async def test_normalize_store_down_returns_error() -> None:
    """정규화: 저장소 미연결이면 error 반환."""
    svc, _, _ = _make_service(graph=FakeGraphStore(alive=False))
    result = await svc.normalize_entities()
    assert result["merged"] == 0
    assert "error" in result


# ── CR-26/30 인덱싱 중단(graceful) · 그래프 초기화 ───────────────────────────


@pytest.mark.asyncio
async def test_cancel_indexing_marks_pending_cancelled() -> None:
    """중단: 대기 중 작업이 cancelled로 표시되고 큐가 비워진다 (graceful)."""
    svc, graph, _ = _make_service()
    svc.schedule_index_document("doc-a")
    svc.schedule_index_document("doc-b")
    n = svc.cancel_indexing()
    assert n >= 1
    states = {s["doc_id"]: s["state"] for s in svc.index_statuses()}
    assert all(st == "cancelled" for st in states.values())
    # 재스케줄 시 중단 플래그 해제 → pending으로 재진입
    svc.schedule_index_document("doc-a")
    states2 = {s["doc_id"]: s["state"] for s in svc.index_statuses()}
    assert states2["doc-a"] == "pending"
    svc.cancel_indexing()


@pytest.mark.asyncio
async def test_clear_graph_wipes_store_and_statuses() -> None:
    """초기화: 저장소 전체 삭제 + 진행 상태 정리, 삭제 전 stats 반환."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="doc-1", title="문서1"),
        [KeywordMention(doc_id="doc-1", raw_term="키워드", role="technology")],
    )
    svc, _, _ = _make_service(graph=graph)
    svc.schedule_index_document("doc-1")

    before = await svc.clear_graph()
    assert before["keywords"] == 1
    assert graph.stats()["keywords"] == 0
    assert svc.index_statuses() == []


# ── CR-30 시험 인덱싱 모드 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_index_returns_extraction_results() -> None:
    """시험 모드: N건 인덱싱 후 문서별 추출 결과 + stats 반환."""
    resp = {"title": "T", "keywords": [{"term": "시험 키워드", "role": "outcome", "confidence": 0.5}]}
    rows = [
        make_row("c1", doc_id="d1", text="시험 본문 하나입니다. " * 3),
        make_row("c2", doc_id="d2", text="시험 본문 둘입니다. " * 3),
    ]
    svc, graph, _ = _make_service(rows=rows, responses={"시험": resp})
    # _all_doc_ids는 vstore._tbl을 요구 — Fake에는 없으므로 직접 주입
    svc._all_doc_ids = lambda: ["d1", "d2"]  # type: ignore[method-assign]

    result = await svc.test_index(limit=1)
    assert len(result["results"]) == 1
    r0 = result["results"][0]
    assert r0["state"] == "done"
    assert r0["keywords"][0]["raw_term"] == "시험 키워드"
    assert result["stats"]["keywords"] == 1



# ── CR-31: 문서(과제) 검색 — 키워드는 신호, 결과는 문서만 ────────────────────


@pytest.mark.asyncio
async def test_search_documents_by_keyword_not_by_title() -> None:
    """핵심: 제목에 없는 용어라도 문서 키워드에 있으면 그 문서가 검색된다."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="주요 채소작물 안정생산 알고리즘 개발"),  # 제목에 '디지털트윈' 없음
        [KeywordMention(doc_id="d1", raw_term="디지털트윈", role="technology")],
    )
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="d2", title="무관한 과제"),
        [KeywordMention(doc_id="d2", raw_term="센서", role="technology")],
    )
    svc, _, _ = _make_service(graph=graph)
    docs = await svc.search_documents("디지털트윈")
    # 결과는 문서 하나 — 키워드 노드가 아니라 그 키워드를 가진 과제
    assert [d["doc_id"] for d in docs] == ["d1"]
    assert docs[0]["title_match"] is False
    assert "디지털트윈" in docs[0]["matched_keywords"]


@pytest.mark.asyncio
async def test_search_documents_title_match() -> None:
    """제목 일치도 잡힌다."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(ProjectInfo(doc_id="d1", title="디지털트윈 기반 농기계"), [])
    svc, _, _ = _make_service(graph=graph)
    docs = await svc.search_documents("디지털트윈")
    assert docs[0]["doc_id"] == "d1" and docs[0]["title_match"] is True


@pytest.mark.asyncio
async def test_search_documents_store_down_empty() -> None:
    svc, _, _ = _make_service(graph=FakeGraphStore(alive=False))
    assert await svc.search_documents("디지털트윈") == []

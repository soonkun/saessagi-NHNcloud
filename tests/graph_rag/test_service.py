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

from .fakes import FakeCompleteJson, FakeEmbedder, FakeGraphStore, FakeVectorStore, make_row


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
    resp = {
        "title": "T",
        "keywords": [{"term": "시험 키워드", "role": "outcome", "confidence": 0.5}],
    }
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
        ProjectInfo(
            doc_id="d1", title="주요 채소작물 안정생산 알고리즘 개발"
        ),  # 제목에 '디지털트윈' 없음
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


# ── CR-34: 공유 키워드 기반 문서-문서 연관 엣지 (조회 시점 파생) ─────────────────


def _kw(doc_id: str, raw: str, role: str = "technology", norm: str = "") -> KeywordMention:
    return KeywordMention(doc_id=doc_id, raw_term=raw, role=role, normalized_term=norm)


def test_snapshot_links_docs_sharing_a_keyword() -> None:
    """정상: 같은 용어·역할을 공유하는 두 문서에 related 엣지(weight=공유 수)."""
    g = FakeGraphStore()
    g.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "디지털트윈")])
    g.upsert_project_bundle(ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "디지털트윈")])
    snap = g.snapshot()
    related = [e for e in snap.edges if e.kind == "related"]
    assert len(related) == 1
    e = related[0]
    assert {e.source, e.target} == {"d1", "d2"} and e.weight == 1.0


def test_snapshot_no_link_when_no_shared_keyword() -> None:
    """엣지: 공유 용어가 없으면 related 엣지 없음 (방사형 독립 유지)."""
    g = FakeGraphStore()
    g.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "디지털트윈")])
    g.upsert_project_bundle(ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "스마트팜")])
    assert [e for e in g.snapshot().edges if e.kind == "related"] == []


def test_snapshot_weight_counts_shared_terms() -> None:
    """정상: 두 문서가 2개 용어를 공유하면 weight=2."""
    g = FakeGraphStore()
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="A"),
        [_kw("d1", "디지털트윈"), _kw("d1", "센서", role="problem")],
    )
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d2", title="B"),
        [_kw("d2", "디지털트윈"), _kw("d2", "센서", role="problem")],
    )
    related = [e for e in g.snapshot().edges if e.kind == "related"]
    assert len(related) == 1 and related[0].weight == 2.0


def test_snapshot_normalized_term_bridges_variants() -> None:
    """정상: raw_term은 달라도 normalized_term이 같으면 연결된다 (정규화 효과)."""
    g = FakeGraphStore()
    g.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "AI", norm="인공지능")])
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "인공지능", norm="인공지능")]
    )
    assert len([e for e in g.snapshot().edges if e.kind == "related"]) == 1


def test_snapshot_skips_generic_high_fanout_term() -> None:
    """엣지: 너무 흔한 용어(16개 문서 공유)는 변별력 없어 링크 제외 (fanout 15)."""
    g = FakeGraphStore()
    for i in range(16):
        g.upsert_project_bundle(
            ProjectInfo(doc_id=f"d{i}", title=str(i)), [_kw(f"d{i}", "인공지능")]
        )
    assert [e for e in g.snapshot().edges if e.kind == "related"] == []


def test_snapshot_role_scoped_sharing() -> None:
    """엣지: 같은 용어라도 역할이 다르면 별개 개념으로 보고 연결하지 않는다."""
    g = FakeGraphStore()
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "센서", role="technology")]
    )
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "센서", role="problem")]
    )
    assert [e for e in g.snapshot().edges if e.kind == "related"] == []


# ── CR-35: 증분 인덱싱 · 증분 정규화 ──────────────────────────────────────────


def test_existing_doc_ids_and_mark_processed() -> None:
    """store: 인덱싱된 문서 목록 조회 + 처리표시(병합 안 돼도 normalized로)."""
    g = FakeGraphStore()
    g.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "센서")])
    assert set(g.existing_doc_ids()) == {"d1"}
    kid = g.all_keywords()[0].id
    assert g.mark_keywords_processed([kid]) == 1
    k = g.keywords[kid]
    assert k.normalization_status == "normalized" and k.normalized_term == "센서"


@pytest.mark.asyncio
async def test_reindex_missing_schedules_only_new_docs() -> None:
    """증분 인덱싱: 벡터 스토어 doc − 그래프 doc 만 스케줄."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "센서")])
    graph.upsert_project_bundle(ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "센서")])
    svc, _, _ = _make_service(graph=graph)
    svc._all_doc_ids = lambda: ["d1", "d2", "d3", "d4"]  # type: ignore[method-assign]
    count = await svc.reindex_missing()
    assert count == 2  # d3, d4 만 (d1,d2 는 이미 그래프에 있음)


@pytest.mark.asyncio
async def test_normalize_incremental_attaches_new_to_anchor() -> None:
    """증분 정규화: 새(raw) 키워드만, 기존 대표어(normalized_term)에 흡수."""
    graph = FakeGraphStore()
    # docB: 이미 정규화된 앵커 '인공지능'
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="docB", title="B"),
        [
            KeywordMention(
                doc_id="docB",
                raw_term="인공지능",
                role="technology",
                normalized_term="인공지능",
                normalization_status="normalized",
            )
        ],
    )
    # docA: 새 표기 'AI' (raw)
    graph.upsert_project_bundle(ProjectInfo(doc_id="docA", title="A"), [_kw("docA", "AI")])
    # LLM은 'AI'를 앵커 '인공지능' 군집에 붙인다
    svc, _, _ = _make_service(responses={"AI": {"groups": [["인공지능", "AI"]]}})
    svc._graph = graph  # type: ignore[assignment]
    result = await svc.normalize_entities(only_new=True)
    assert result["merged"] == 1
    ai = next(k for k in graph.all_keywords() if k.raw_term == "AI")
    assert ai.normalized_term == "인공지능"  # 앵커가 대표어로 채택됨


@pytest.mark.asyncio
async def test_normalize_incremental_skips_when_no_new() -> None:
    """증분 정규화: 새 키워드가 없으면 LLM 호출 자체를 건너뛴다."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="A"),
        [
            KeywordMention(
                doc_id="d1",
                raw_term="센서",
                role="technology",
                normalized_term="센서",
                normalization_status="normalized",
            )
        ],
    )
    llm = FakeCompleteJson({})
    svc = GraphRagService(
        graph_store=graph,
        vector_store=FakeVectorStore([]),
        extractor=EntityExtractor(complete_json=llm),
        rag_service=FakeRagService(RetrievalResult(hits=[], found=False, no_match_reason="")),
        max_hops=2,
    )
    result = await svc.normalize_entities(only_new=True)
    assert result["merged"] == 0 and llm.calls == []


# ── CR-36: 임베딩 기반 대규모 정규화 ──────────────────────────────────────────


def _svc_with_embedder(graph: FakeGraphStore, embedder: FakeEmbedder) -> GraphRagService:
    return GraphRagService(
        graph_store=graph,
        vector_store=FakeVectorStore([]),
        extractor=EntityExtractor(complete_json=FakeCompleteJson({})),
        rag_service=FakeRagService(RetrievalResult(hits=[], found=False, no_match_reason="")),
        max_hops=2,
        embedder=embedder,
    )


@pytest.mark.asyncio
async def test_normalize_embedding_merges_variants_not_distinct() -> None:
    """임베딩 정규화(전체): 같은 개념 표기 변형은 묶고 다른 개념은 분리."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(ProjectInfo(doc_id="d1", title="A"), [_kw("d1", "AI")])
    graph.upsert_project_bundle(ProjectInfo(doc_id="d2", title="B"), [_kw("d2", "인공지능")])
    graph.upsert_project_bundle(ProjectInfo(doc_id="d3", title="C"), [_kw("d3", "머신러닝")])
    emb = FakeEmbedder({"AI": "ai", "인공지능": "ai", "머신러닝": "ml"})
    svc = _svc_with_embedder(graph, emb)
    result = await svc.normalize_entities(only_new=False)
    kws = {k.raw_term: k for k in graph.all_keywords()}
    # AI 와 인공지능은 같은 normalized_term (한 개념으로 병합)
    assert kws["AI"].normalized_term == kws["인공지능"].normalized_term != ""
    assert result["merged"] >= 2
    # 머신러닝은 별개 — AI 개념에 붙지 않음
    assert kws["머신러닝"].normalized_term != kws["AI"].normalized_term


@pytest.mark.asyncio
async def test_normalize_embedding_scales_past_llm_cap() -> None:
    """임베딩 정규화는 300개 캡 없이 수백 용어를 처리한다 (구 LLM 캡 회귀 방지)."""
    graph = FakeGraphStore()
    concept: dict[str, str] = {}
    # 400개 문서: 200개는 개념 X의 변형, 200개는 각자 고유
    for i in range(200):
        t = f"엑스변형{i}"
        concept[t] = "conceptX"
        graph.upsert_project_bundle(ProjectInfo(doc_id=f"x{i}", title="x"), [_kw(f"x{i}", t)])
    for i in range(200):
        graph.upsert_project_bundle(
            ProjectInfo(doc_id=f"u{i}", title="u"), [_kw(f"u{i}", f"고유{i}")]
        )
    svc = _svc_with_embedder(graph, FakeEmbedder(concept))
    result = await svc.normalize_entities(only_new=False)
    kws = {k.raw_term: k for k in graph.all_keywords()}
    reps = {kws[f"엑스변형{i}"].normalized_term for i in range(200)}
    assert len(reps) == 1 and next(iter(reps)) != ""  # 200개 변형이 한 대표어로
    assert result["merged"] >= 200


@pytest.mark.asyncio
async def test_normalize_embedding_incremental_attaches_to_anchor() -> None:
    """임베딩 증분: 새 표기가 기존 정규화 대표어(앵커) 군집에 흡수."""
    graph = FakeGraphStore()
    graph.upsert_project_bundle(
        ProjectInfo(doc_id="docB", title="B"),
        [
            KeywordMention(
                doc_id="docB",
                raw_term="인공지능",
                role="technology",
                normalized_term="인공지능",
                normalization_status="normalized",
            )
        ],
    )
    graph.upsert_project_bundle(ProjectInfo(doc_id="docA", title="A"), [_kw("docA", "AI")])
    emb = FakeEmbedder({"AI": "ai", "인공지능": "ai"})
    svc = _svc_with_embedder(graph, emb)
    result = await svc.normalize_entities(only_new=True)
    ai = next(k for k in graph.all_keywords() if k.raw_term == "AI")
    assert ai.normalized_term == "인공지능" and result["merged"] == 1


@pytest.mark.asyncio
async def test_normalize_embedding_no_chaining() -> None:
    """회귀: leader 군집화는 A~B~C 연쇄(transitive)로 무관 용어를 병합하지 않는다.

    termA~termB=0.85, termB~termC=0.80(둘 다 임계 0.78 초과)이지만 termA~termC=0.5.
    single-linkage였다면 셋 다 한 blob이 됐다. leader 군집화는 termC를 termA(leader)와만
    비교해 분리한다.
    """
    graph = FakeGraphStore()
    for t in ("termA", "termB", "termC"):
        graph.upsert_project_bundle(ProjectInfo(doc_id=f"d_{t}", title=t), [_kw(f"d_{t}", t)])
    vecs = {
        "termA": [1.0, 0.0, 0.0],
        "termB": [0.85, 0.527, 0.0],  # ·A=0.85
        "termC": [0.5, 0.72, 0.48],  # ·A=0.50, ·B=0.80
    }
    svc = _svc_with_embedder(graph, FakeEmbedder(vectors=vecs))
    await svc.normalize_entities(only_new=False)
    kws = {k.raw_term: k for k in graph.all_keywords()}
    assert kws["termA"].normalized_term == kws["termB"].normalized_term  # 직접 유사 → 병합
    assert kws["termC"].normalized_term != kws["termA"].normalized_term  # 연쇄 병합 안 됨


# ── CR-37: 문서 중심 포커스 서브그래프 ────────────────────────────────────────


def test_doc_focus_snapshot_center_keywords_and_connections() -> None:
    """중심 문서 + 그 키워드 + 공유 키워드로 이어진 문서만 포함(무관 문서 제외)."""
    g = FakeGraphStore()
    g.upsert_project_bundle(
        ProjectInfo(doc_id="d1", title="센터"),
        [_kw("d1", "디지털트윈"), _kw("d1", "센서", role="problem")],
    )
    g.upsert_project_bundle(ProjectInfo(doc_id="d2", title="연결"), [_kw("d2", "디지털트윈")])
    g.upsert_project_bundle(ProjectInfo(doc_id="d3", title="무관"), [_kw("d3", "스마트팜")])
    snap = g.doc_focus_snapshot("d1")
    node_ids = {n.id for n in snap.nodes}
    assert "d1" in node_ids and "d2" in node_ids and "d3" not in node_ids
    assert any(n.kind == "keyword" for n in snap.nodes)  # 중심 문서 키워드 포함
    related = [e for e in snap.edges if e.kind == "related"]
    assert any({e.source, e.target} == {"d1", "d2"} for e in related)
    assert any(e.kind == "has_keyword" and e.source == "d1" for e in snap.edges)


def test_doc_focus_snapshot_missing_doc_empty() -> None:
    """존재하지 않는 문서 → 빈 스냅샷."""
    assert FakeGraphStore().doc_focus_snapshot("nope").nodes == []


@pytest.mark.asyncio
async def test_doc_focus_service_store_down_empty() -> None:
    """저장소 다운 → 빈 스냅샷(예외 없음)."""
    svc, _, _ = _make_service(graph=FakeGraphStore(alive=False))
    snap = await svc.doc_focus("d1")
    assert snap.nodes == [] and snap.edges == []

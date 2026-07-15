# tests/graph_rag/test_service.py
"""M_19 GraphRagService 단위 테스트 (스펙 §9)."""

from __future__ import annotations

from typing import Any

import pytest

from graph_rag.extractor import EntityExtractor
from graph_rag.service import GraphRagService
from graph_rag.types import ChunkLink, Entity, Relation
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


def _seed_graph(graph: FakeGraphStore) -> None:
    """A기관 -[주관]-> B사업 -[참여]-* C대학. 청크 c1(A,B), c2(B,C), c3(C)."""
    ents = [
        Entity(id="a기관:조직", name="A기관", type="조직"),
        Entity(id="b사업:사업", name="B사업", type="사업"),
        Entity(id="c대학:조직", name="C대학", type="조직"),
    ]
    graph.upsert_entities(ents)
    graph.upsert_relations(
        [
            Relation(source_id="a기관:조직", target_id="b사업:사업", type="주관"),
            Relation(source_id="b사업:사업", target_id="c대학:조직", type="참여"),
        ]
    )
    graph.link_chunks(
        [
            ChunkLink("a기관:조직", "c1"),
            ChunkLink("b사업:사업", "c1"),
            ChunkLink("b사업:사업", "c2"),
            ChunkLink("c대학:조직", "c2"),
            ChunkLink("c대학:조직", "c3"),
        ],
        parent_id="docG",
        parent_kind="document",
    )


_ROWS = [
    make_row("c1", doc_id="docG", text="A기관과 B사업"),
    make_row("c2", doc_id="docG", text="B사업과 C대학"),
    make_row("c3", doc_id="docG", text="C대학 단독"),
]


# ── graph_retrieve ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_retrieve_expands_hops() -> None:
    """정상: 'A기관' 질의 → 2홉 확장으로 C대학 청크까지 도달."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    hits, evidence = await svc.graph_retrieve("A기관 관련해서 알려줘", top_k=5)
    chunk_ids = {h.chunk_id for h in hits}
    assert "c1" in chunk_ids and "c2" in chunk_ids  # 1홉(B사업) 청크 포함
    assert evidence is not None
    assert any(n.kind == "document" for n in evidence.nodes)


@pytest.mark.asyncio
async def test_graph_retrieve_no_match_returns_empty() -> None:
    """엣지: 매칭 엔티티 0건 → 빈 결과."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    hits, evidence = await svc.graph_retrieve("전혀무관한질의어", top_k=5)
    assert hits == [] and evidence is None


@pytest.mark.asyncio
async def test_graph_retrieve_empty_query() -> None:
    """엣지: 빈 질의/1자 단어 → 빈 결과."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    assert (await svc.graph_retrieve("", top_k=5))[0] == []
    assert (await svc.graph_retrieve("가 나 다", top_k=5))[0] == []


@pytest.mark.asyncio
async def test_graph_retrieve_orphan_chunk_ignored() -> None:
    """엣지: LanceDB에서 삭제된 청크(고아 링크)는 무시."""
    svc, graph, _ = _make_service(rows=[make_row("c1", doc_id="docG")])  # c2, c3 없음
    _seed_graph(graph)
    hits, _ = await svc.graph_retrieve("A기관", top_k=5)
    assert {h.chunk_id for h in hits} == {"c1"}


@pytest.mark.asyncio
async def test_graph_retrieve_scores_by_entity_count() -> None:
    """정상: 연결 엔티티 수가 많은 청크가 더 높은 점수."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    hits, _ = await svc.graph_retrieve("B사업", top_k=5)
    by_id = {h.chunk_id: h.score for h in hits}
    assert by_id["c1"] >= by_id["c3"] or by_id["c2"] >= by_id["c3"]


# ── hybrid_retrieve ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_fuses_vector_and_graph() -> None:
    """정상: RRF 융합 — 양쪽 상위 청크가 결과에 공존."""
    vec = RetrievalResult(
        hits=[_hit("v1"), _hit("v2", score=0.7)], found=True, no_match_reason=None
    )
    svc, graph, _ = _make_service(rows=_ROWS, vector_result=vec)
    _seed_graph(graph)
    result = await svc.hybrid_retrieve("A기관 B사업 이야기", top_k=4)
    ids = {h.chunk_id for h in result.hits}
    assert "v1" in ids  # 벡터 유래
    assert ids & {"c1", "c2"}  # 그래프 유래
    assert result.found is True


@pytest.mark.asyncio
async def test_hybrid_graph_rescues_vector_miss() -> None:
    """정상: 벡터 found=False여도 그래프 hit가 있으면 found=True (구제)."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    result = await svc.hybrid_retrieve("A기관", top_k=3)
    assert result.found is True
    assert result.hits


@pytest.mark.asyncio
async def test_hybrid_fallback_when_store_down() -> None:
    """엣지: 그래프 저장소 다운 → 벡터 결과 그대로 (예외 없음)."""
    vec = RetrievalResult(hits=[_hit("v1")], found=True, no_match_reason=None)
    svc, graph, rag = _make_service(
        graph=FakeGraphStore(alive=False), rows=_ROWS, vector_result=vec
    )
    result = await svc.hybrid_retrieve("A기관", top_k=3)
    assert result is not None
    assert [h.chunk_id for h in result.hits] == ["v1"]
    assert rag.calls  # 벡터 경로는 호출됨


@pytest.mark.asyncio
async def test_hybrid_stores_evidence() -> None:
    """정상: 융합 후 latest_evidence에 근거 서브그래프 보관."""
    svc, graph, _ = _make_service(rows=_ROWS)
    _seed_graph(graph)
    await svc.hybrid_retrieve("A기관과 B사업의 관계", top_k=3)
    ev = svc.latest_evidence()
    assert ev is not None
    assert ev.query.startswith("A기관")
    assert ev.chunk_ids


# ── 인덱싱 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_document_builds_graph() -> None:
    """정상: 문서 인덱싱 → 엔티티·관계·링크·부모 노드 생성."""
    resp = {
        "entities": [
            {"name": "A기관", "type": "조직"},
            {"name": "B사업", "type": "사업"},
        ],
        "relations": [{"source": "A기관", "target": "B사업", "type": "주관"}],
    }
    svc, graph, _ = _make_service(
        rows=[make_row("c1", doc_id="d1", text="A기관이 B사업을 주관한다. " * 3)],
        responses={"A기관": resp},
    )
    status = await svc.index_document("d1")
    assert status.state == "done"
    assert status.done_chunks == 1
    assert "a기관:조직" in graph.entities
    assert ("a기관:조직", "b사업:사업", "주관") in graph.relations
    assert ("a기관:조직", "c1") in graph.chunk_links
    assert "d1" in graph.documents


@pytest.mark.asyncio
async def test_index_note_uses_note_parent() -> None:
    """정상: __knowledge__ 카테고리 doc_id → Note 부모 노드."""
    resp = {"entities": [{"name": "회의", "type": "기타"}], "relations": []}
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
async def test_index_document_extraction_failures_skip() -> None:
    """엣지: 추출 0건 청크는 skipped로 집계, 문서는 done."""
    svc, _, _ = _make_service(
        rows=[make_row("c1", doc_id="d1", text="추출될 것 없는 텍스트 본문. " * 2)]
    )
    status = await svc.index_document("d1")
    assert status.state == "done"
    assert status.skipped_chunks == 1


@pytest.mark.asyncio
async def test_schedule_duplicate_ignored() -> None:
    """엣지: 같은 doc_id 중복 스케줄은 무시 (pending 중)."""
    svc, _, _ = _make_service(rows=_ROWS)
    svc.schedule_index_document("dup")
    svc.schedule_index_document("dup")
    assert svc._queue.qsize() == 1  # noqa: SLF001 — 큐 내부 확인
    if svc._worker is not None:  # noqa: SLF001
        svc._worker.cancel()  # noqa: SLF001


# ── 삭제 연쇄 ─────────────────────────────────────────────────────────────────


def test_delete_document_cascades() -> None:
    """정상: 문서 삭제 → 청크 링크 제거 + 고아 엔티티 정리."""
    graph = FakeGraphStore()
    _seed_graph(graph)
    graph.upsert_document("docG", "그래프문서.pdf")
    svc, _, _ = _make_service(graph=graph)
    svc.delete_document("docG")
    assert graph.stats()["chunks"] == 0
    assert graph.stats()["entities"] == 0

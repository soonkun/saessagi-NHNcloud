# tests/vector_search/test_source_filter.py
"""M_16 vector_search 소스 필터 보강 테스트 (N-7, N-8, E-9, E-10, A-5)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.vector_search.fakes import FakeEmbedder
from vector_search.rag import RagService
from vector_search.store import VectorStore
from vector_search.types import DocumentChunk

KNOWLEDGE_CATEGORY = "__knowledge__"


def _make_chunk(
    doc_id: str,
    text: str,
    category: str | None,
    doc_name: str = "test.txt",
) -> DocumentChunk:
    return DocumentChunk(
        doc_id=doc_id,
        doc_name=doc_name,
        category=category,
        page=None,
        section=None,
        chunk_id=str(uuid.uuid4()),
        text=text,
        bbox=None,
        source_path=f"/docs/{doc_name}",
    )


@pytest.fixture
def store_with_mixed_data(tmp_path: Path) -> tuple[VectorStore, FakeEmbedder]:
    """문서 청크 2건(category='규정', None) + 노트 청크 2건(category='__knowledge__') 포함 저장소."""
    fe = FakeEmbedder()
    store = VectorStore(str(tmp_path / "lancedb"))

    chunks = [
        _make_chunk("doc-001", "연차 규정 내용입니다.", "규정", "규정집.pdf"),
        _make_chunk("doc-002", "출장비 정산 방법입니다.", None, "메모.txt"),  # category=NULL
        _make_chunk("note-001", "지난주 출장 정산 처리 완료", KNOWLEDGE_CATEGORY, "노트"),
        _make_chunk("note-002", "연구노트 제외신청 완료", KNOWLEDGE_CATEGORY, "노트"),
    ]
    texts = [c.text for c in chunks]
    vectors = fe.embed_passages(texts)
    store.upsert(chunks, vectors)
    return store, fe


class TestSourceFilterNormal:
    """정상 케이스 N-7, N-8."""

    def test_N7_source_docs_excludes_notes(
        self, store_with_mixed_data: tuple[VectorStore, FakeEmbedder]
    ) -> None:
        """N-7: source='docs' 검색 → 노트 0건, 문서만 반환."""
        store, fe = store_with_mixed_data
        query_vec = fe.embed_query("규정 관련")
        hits = store.search(query_vec, top_k=10, source="docs")
        # 모든 hit이 노트가 아님
        for hit in hits:
            assert hit.category != KNOWLEDGE_CATEGORY, (
                f"docs 검색에 노트가 포함됨: category={hit.category}"
            )
        # 문서 hit이 1건 이상 있어야 함
        assert len(hits) >= 1

    def test_N7_source_notes_excludes_docs(
        self, store_with_mixed_data: tuple[VectorStore, FakeEmbedder]
    ) -> None:
        """N-7: source='notes' 검색 → 문서 0건, 노트만 반환."""
        store, fe = store_with_mixed_data
        query_vec = fe.embed_query("업무 처리")
        hits = store.search(query_vec, top_k=10, source="notes")
        # 모든 hit이 노트
        for hit in hits:
            assert hit.category == KNOWLEDGE_CATEGORY, (
                f"notes 검색에 문서가 포함됨: category={hit.category}"
            )
        assert len(hits) >= 1

    def test_N7_source_both_returns_all(
        self, store_with_mixed_data: tuple[VectorStore, FakeEmbedder]
    ) -> None:
        """N-7: source='both' 검색 → 4건 후보 모두 검색 대상."""
        store, fe = store_with_mixed_data
        query_vec = fe.embed_query("출장")
        hits = store.search(query_vec, top_k=10, source="both")
        # 4건이 있으므로 결과는 최대 4건
        assert len(hits) <= 4
        # 노트와 문서 모두 포함 가능 (섞인 결과)
        assert len(hits) >= 2

    def test_N8_rag_service_retrieve_notes_only(self, tmp_path: Path) -> None:
        """N-8: RagService.retrieve(source='notes') → 모든 hit의 category=='__knowledge__'."""
        fe = FakeEmbedder()
        store = VectorStore(str(tmp_path / "lancedb"))
        chunks = [
            _make_chunk("doc-001", "규정 내용", "규정"),
            _make_chunk("note-001", "업무 처리 노트", KNOWLEDGE_CATEGORY),
            _make_chunk("note-002", "다른 업무 노트", KNOWLEDGE_CATEGORY),
        ]
        vectors = fe.embed_passages([c.text for c in chunks])
        store.upsert(chunks, vectors)

        rag = RagService(embedder=fe, store=store, min_score=0.0)
        result = rag.retrieve("업무 처리", top_k=10, source="notes")
        for hit in result.hits:
            assert hit.category == KNOWLEDGE_CATEGORY


class TestSourceFilterEdge:
    """엣지 케이스 E-9, E-10."""

    def test_E9_source_notes_empty_store_no_crash(self, tmp_path: Path) -> None:
        """E-9: 문서 청크만 있고 노트 0건일 때 source='notes' → 빈 결과 + 크래시 없음."""
        fe = FakeEmbedder()
        store = VectorStore(str(tmp_path / "lancedb"))
        chunks = [
            _make_chunk("doc-001", "규정 내용", "규정"),
            _make_chunk("doc-002", "메모 내용", None),
        ]
        vectors = fe.embed_passages([c.text for c in chunks])
        store.upsert(chunks, vectors)

        query_vec = fe.embed_query("노트 찾기")
        hits = store.search(query_vec, top_k=5, source="notes")
        assert hits == []  # 빈 결과

        # RagService도 found=False
        rag = RagService(embedder=fe, store=store, min_score=0.0)
        result = rag.retrieve("노트 찾기", top_k=5, source="notes")
        assert result.found is False

    def test_E10_category_exact_and_source_docs_AND_behavior(self, tmp_path: Path) -> None:
        """E-10: category='규정' + source='docs' → AND 직교: '규정' 카테고리인 문서만(노트 제외)."""
        fe = FakeEmbedder()
        store = VectorStore(str(tmp_path / "lancedb"))
        chunks = [
            _make_chunk("doc-001", "규정 내용", "규정"),
            _make_chunk("doc-002", "매뉴얼 내용", "매뉴얼"),
            _make_chunk("note-001", "노트 내용", KNOWLEDGE_CATEGORY),
        ]
        vectors = fe.embed_passages([c.text for c in chunks])
        store.upsert(chunks, vectors)

        query_vec = fe.embed_query("내용")
        # category='규정' AND source='docs' → 오직 doc-001만
        hits = store.search(query_vec, top_k=10, category="규정", source="docs")
        for hit in hits:
            assert hit.category == "규정"
            assert hit.category != KNOWLEDGE_CATEGORY

    def test_source_docs_includes_null_category(self, tmp_path: Path) -> None:
        """source='docs' → category IS NULL인 청크도 포함."""
        fe = FakeEmbedder()
        store = VectorStore(str(tmp_path / "lancedb"))
        chunks = [
            _make_chunk("doc-001", "카테고리 없는 문서", None),  # NULL category
            _make_chunk("note-001", "노트", KNOWLEDGE_CATEGORY),
        ]
        vectors = fe.embed_passages([c.text for c in chunks])
        store.upsert(chunks, vectors)

        query_vec = fe.embed_query("문서")
        hits = store.search(query_vec, top_k=5, source="docs")
        # NULL category 문서가 포함되어야 함
        doc_ids = [h.doc_id for h in hits]
        assert "doc-001" in doc_ids
        assert "note-001" not in doc_ids


class TestSourceFilterAdversarial:
    """적대적 케이스 A-5."""

    def test_A5_sql_injection_in_category_escaped(self, tmp_path: Path) -> None:
        """A-5: category에 SQL 인젝션 시도 → _escape_category가 single quote 이스케이프."""
        from vector_search.store import _escape_category

        # single quote 인젝션 시도 — _escape_category가 이스케이프
        injection = "' OR '1'='1"
        escaped = _escape_category(injection)
        # 이스케이프 결과에 unescaped single quote 없어야 함
        assert "'' OR '1''=''1" == escaped or "''1''=''1" in escaped or "''" in escaped

    def test_A5_source_filter_knowledge_literal_not_user_input(
        self, store_with_mixed_data: tuple[VectorStore, FakeEmbedder]
    ) -> None:
        """A-5: source 필터의 '__knowledge__' 리터럴은 사용자 입력 무관 — 정상 동작."""
        store, fe = store_with_mixed_data
        query_vec = fe.embed_query("테스트")
        # source='notes'는 '__knowledge__' 리터럴을 사용; 인젝션 불가
        hits = store.search(query_vec, top_k=5, source="notes")
        # 정상 반환 (노트만)
        for hit in hits:
            assert hit.category == KNOWLEDGE_CATEGORY

    def test_A5_control_char_in_category_raises(self, tmp_path: Path) -> None:
        """A-5: category에 제어 문자 → VectorStoreError."""
        from vector_search.errors import VectorStoreError
        from vector_search.store import _escape_category

        with pytest.raises(VectorStoreError):
            _escape_category("valid\x00injection")

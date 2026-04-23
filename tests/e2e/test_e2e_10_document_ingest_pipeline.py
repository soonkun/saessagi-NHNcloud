# tests/e2e/test_e2e_10_document_ingest_pipeline.py
"""E2E-10: M_06 DocumentIngest 전체 파이프라인.

시나리오 ID: E2E-10-document-ingest-pipeline
REQUIREMENTS: §2.1 문서 등록 (HWPX 지원), §2.2 질의응답 (인용 포맷)
관련 모듈: M_06 DocumentIngest, M_07 VectorSearch (VectorStore + RagService)
마커: e2e_fast (BGE-M3 모델 없이 FakeEmbedder로 실행)
실행 시간 목표: ≤ 15초

시나리오:
    합성 HWPX 픽스처를 실제 DocumentIngest 파이프라인으로 인제스트하고,
    VectorStore에 청크가 저장됐음을 검증하며, RagService.retrieve로 검색,
    재-ingest 후에도 중복이 없음(멱등성)을 확인한다.

수락 기준:
    AC-10-1: ingest_file() 반환값 > 0 (청크 수).
    AC-10-2: VectorStore.search() 결과에 해당 doc_name("sample_2011.hwpx") 포함.
    AC-10-3: 재-ingest 후 총 row 수 == 첫 인제스트 후 row 수 (중복 없음).
    AC-10-4: RagService.retrieve()가 관련 쿼리에 found=True 반환.
    AC-10-5: RagService.format_citation() 결과가 `<doc_name>` 백틱 패턴 포함.

FakeEmbedder 전략:
    - 동일 텍스트를 query로 사용하면 passage vector와 cosine sim = 1.0 → found=True.
    - 첫 번째 청크 텍스트를 exact 쿼리로 사용해 found=True 조건을 만족시킨다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# FakeEmbedder 경로 보장
_TESTS_ROOT = Path(__file__).parent.parent
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_fast]

# HWPX 픽스처: tests/document_ingest/fixtures/sample_2011.hwpx
_HWPX_FIXTURE = Path(__file__).parent.parent / "document_ingest" / "fixtures" / "sample_2011.hwpx"
# HWPX 파일의 첫 번째 청크 텍스트 (파서 결과에서 확인, 멱등성·쿼리 앵커로 사용)
_FIRST_CHUNK_TEXT = "2011 네임스페이스 첫 번째 단락입니다."


@pytest.mark.timeout(15)
async def test_e2e_10_document_ingest_pipeline(tmp_path: Path) -> None:
    """M_06 DocumentIngest 전체 파이프라인 E2E 검증.

    HWPX → ingest → VectorStore → search → citation 포맷 + 멱등성 회귀 가드.
    FakeEmbedder를 사용해 BGE-M3 없이 실행 가능(e2e_fast).
    """
    # ── 전제 조건 확인 ────────────────────────────────────────────────
    if not _HWPX_FIXTURE.exists():
        pytest.skip(
            reason=f"HWPX 픽스처 없음: {_HWPX_FIXTURE} — "
            "tests/document_ingest/fixtures/sample_2011.hwpx가 필요합니다."
        )

    from tests.vector_search.fakes import FakeEmbedder
    from vector_search.store import VectorStore
    from vector_search.rag import RagService
    from document_ingest.ingest import DocumentIngest

    # ── 픽스처 구성 ───────────────────────────────────────────────────
    fake_embedder = FakeEmbedder()
    vector_store = VectorStore(
        db_path=str(tmp_path / "vector_store"),
        table="e2e_10_chunks",
    )
    rag_service = RagService(
        embedder=fake_embedder,
        store=vector_store,
        min_score=0.35,
    )
    ingest = DocumentIngest(
        embedder=fake_embedder,
        store=vector_store,
        chunk_chars=800,
        overlap_chars=100,
        embed_batch_size=32,
    )

    # ── AC-10-1: ingest_file() 반환값 > 0 ────────────────────────────
    chunk_count_first = await ingest.ingest_file(
        str(_HWPX_FIXTURE),
        category="테스트",
    )
    assert chunk_count_first > 0, (
        f"AC-10-1 FAIL: ingest_file()이 0 청크를 반환했습니다. 파일: {_HWPX_FIXTURE}"
    )

    # ── AC-10-2: VectorStore.search() 결과에 doc_name 포함 ───────────
    # FakeEmbedder: 첫 번째 청크 텍스트 == exact query → cosine sim 1.0
    query_vec = fake_embedder.embed_query(_FIRST_CHUNK_TEXT)
    hits = vector_store.search(query_vec=query_vec, top_k=8)

    assert len(hits) > 0, "AC-10-2 FAIL: VectorStore.search() 결과가 비어있습니다."
    doc_names_in_hits = {h.doc_name for h in hits}
    assert "sample_2011.hwpx" in doc_names_in_hits, (
        f"AC-10-2 FAIL: 검색 결과에 'sample_2011.hwpx'가 없습니다. "
        f"발견된 doc_names: {doc_names_in_hits}"
    )

    # ── AC-10-4: RagService.retrieve() found=True ─────────────────────
    # FakeEmbedder로 exact query → cosine sim 1.0 → score 1.0 >= min_score 0.35
    retrieval = rag_service.retrieve(query=_FIRST_CHUNK_TEXT, top_k=5)
    assert retrieval.found is True, (
        f"AC-10-4 FAIL: RagService.retrieve()가 found=False를 반환했습니다. "
        f"no_match_reason={retrieval.no_match_reason!r}, "
        f"top_score={retrieval.hits[0].score if retrieval.hits else 'N/A'}"
    )
    assert len(retrieval.hits) > 0, "AC-10-4 FAIL: retrieval.hits가 비어있습니다."

    # ── AC-10-5: format_citation()이 백틱 패턴 포함 ───────────────────
    top_hit = retrieval.hits[0]
    citation = rag_service.format_citation(top_hit)
    assert "`sample_2011.hwpx`" in citation, (
        f"AC-10-5 FAIL: format_citation() 결과에 '`sample_2011.hwpx`'가 없습니다. "
        f"실제 결과: {citation!r}"
    )

    # ── AC-10-3: 재-ingest 멱등성 (중복 없음) ────────────────────────
    # 인제스트 전 row 수 기록
    arrow_before = vector_store._tbl.to_arrow()
    row_count_after_first = len(arrow_before)

    # 동일 파일 재-ingest
    chunk_count_second = await ingest.ingest_file(
        str(_HWPX_FIXTURE),
        category="테스트",
    )

    # 재-ingest 후 row 수
    arrow_after = vector_store._tbl.to_arrow()
    row_count_after_second = len(arrow_after)

    assert row_count_after_second == row_count_after_first, (
        f"AC-10-3 FAIL: 재-ingest 후 row 수가 달라졌습니다 (중복 발생). "
        f"첫 ingest 후={row_count_after_first}, 재-ingest 후={row_count_after_second}. "
        f"첫 ingest 청크 수={chunk_count_first}, 재-ingest 청크 수={chunk_count_second}."
    )
    assert chunk_count_second > 0, (
        f"AC-10-3 FAIL: 재-ingest 시 chunk_count가 0입니다 "
        f"(DocumentIngest가 새 청크를 생성하지 않음)."
    )

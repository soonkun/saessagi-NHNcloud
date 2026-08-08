# tests/vector_search/test_cap_per_document.py
"""CR-63 문서당 청크 상한.

**왜 필요했나**: RFP만 올리던 시절에는 문서당 청크가 적어 top_k가 자연히 여러 문서로
흩어졌다. 청크 50~60개짜리 완결보고서가 들어오면서 한 문서가 상위를 쓸어갔다.
실측: `기후변화 대응방안…` 질의에서 5청크가 문서 **2건**에서 나왔다(3+2).
"""

from __future__ import annotations

from vector_search.rag import cap_per_document
from vector_search.types import SearchHit


def _hit(doc: str, chunk: str, score: float = 0.9) -> SearchHit:
    return SearchHit(
        doc_id=doc,
        doc_name=f"{doc}.pdf",
        chunk_id=chunk,
        text=f"{chunk} 본문",
        score=score,
        page=1,
        section=None,
        bbox=None,
        source_path=f"/{doc}.pdf",
        category=None,
    )


def test_caps_dominant_document() -> None:
    """실측 분포 [5,2,2,...]를 재현 — 1위 문서가 5청크를 먹는다."""
    hits = [_hit("A", f"a{i}") for i in range(5)] + [_hit("B", f"b{i}") for i in range(2)]
    out = cap_per_document(hits, 2)
    assert [h.doc_id for h in out] == ["A", "A", "B", "B"]


def test_preserves_ranking_order() -> None:
    """재정렬이 아니라 걸러내기다 — 남은 것의 상대 순서는 그대로여야 한다."""
    hits = [_hit("A", "a1"), _hit("B", "b1"), _hit("A", "a2"), _hit("C", "c1"), _hit("A", "a3")]
    out = cap_per_document(hits, 2)
    assert [h.chunk_id for h in out] == ["a1", "b1", "a2", "c1"]


def test_zero_means_no_limit() -> None:
    """0은 '제한 없음'이다 — 실수로 전부 버리면 근거가 사라진다."""
    hits = [_hit("A", f"a{i}") for i in range(6)]
    assert cap_per_document(hits, 0) == hits
    assert cap_per_document(hits, -1) == hits


def test_empty_and_missing_doc_id() -> None:
    """doc_id가 빈 hit도 한 무리로 묶여 상한을 받는다 (터지지 않는 것이 핵심)."""
    assert cap_per_document([], 3) == []
    hits = [_hit("", f"x{i}") for i in range(4)]
    assert len(cap_per_document(hits, 2)) == 2


def test_diversity_improves_at_same_budget() -> None:
    """같은 예산(10청크)에서 문서 수가 늘어난다 — CR-63의 목적 그 자체."""
    ranked = (
        [_hit("A", f"a{i}") for i in range(5)]
        + [_hit("B", f"b{i}") for i in range(2)]
        + [_hit("C", "c1"), _hit("D", "d1"), _hit("E", "e1"), _hit("F", "f1")]
    )
    before = {h.doc_id for h in ranked[:10]}
    after = {h.doc_id for h in cap_per_document(ranked, 2)[:10]}
    assert len(after) > len(before), f"다양성이 늘지 않았다: {before} → {after}"

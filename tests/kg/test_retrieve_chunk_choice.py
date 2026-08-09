# tests/kg/test_retrieve_chunk_choice.py
"""E-102: 그래프가 고른 문서에서 **어떤 청크**를 가져오는가.

예전에는 `get_chunks_by_doc_id(doc, limit=2)`로 문서의 앞 2청크를 집었다. 정렬이
없어 사실상 표지와 제출문이다 — 질문이 무엇이든 같은 두 청크가 갔다.
실측으로 최종 근거의 28%가 문서 1~2페이지였다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kg.retrieve import EntityMatch, KgRetriever
from vector_search.types import SearchHit


def _hit(doc: str, chunk: str, page: int, text: str) -> SearchHit:
    return SearchHit(
        doc_id=doc,
        doc_name=f"{doc}.pdf",
        chunk_id=chunk,
        text=text,
        score=0.8,
        page=page,
        section=None,
        bbox=None,
        source_path=f"/{doc}.pdf",
        category=None,
    )


class _Store:
    """앞청크는 표지, 문서내 검색은 본문을 돌려주는 가짜 저장소."""

    def __init__(self) -> None:
        self.front_calls: list[str] = []
        self.indoc_calls: list[str] = []

    def get_chunks_by_doc_id(self, doc_id: str, limit: int = 2) -> list[dict[str, Any]]:
        self.front_calls.append(doc_id)
        return [
            {
                "doc_id": doc_id,
                "doc_name": "x.pdf",
                "chunk_id": "c1",
                "page": 1,
                "text": "완결과제 최종보고서 (과제번호 : PJ012614)",
                "source_path": "/x.pdf",
            },
            {
                "doc_id": doc_id,
                "doc_name": "x.pdf",
                "chunk_id": "c2",
                "page": 2,
                "text": "제 출  농촌진흥청장 귀하",
                "source_path": "/x.pdf",
            },
        ][:limit]

    def search_in_doc(self, query_vec: Any, doc_id: str, top_k: int = 2) -> list[SearchHit]:
        self.indoc_calls.append(doc_id)
        return [_hit(doc_id, "c47", 47, "논물관리 다중물떼기 처리구의 메탄 배출량은 …")]


class _OldStore(_Store):
    """`search_in_doc`이 없던 시절의 저장소 — 폴백이 살아 있는지 본다."""

    search_in_doc = None  # type: ignore[assignment]

    def __getattribute__(self, name: str) -> Any:
        if name == "search_in_doc":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _Graph:
    def __init__(self, matches: list[EntityMatch]) -> None:
        self._matches = matches

    def _run(self, *a: Any, **k: Any) -> list[dict[str, Any]]:  # noqa: D401
        return [
            {
                "canonical_id": m.canonical_id,
                "canonical_name": m.canonical_name,
                "entity_type": m.entity_type,
                "df": m.document_frequency,
                "is_boilerplate": m.is_boilerplate,
                "doc_ids": m.doc_ids,
            }
            for m in self._matches
        ]


def _retriever() -> KgRetriever:
    m = EntityMatch("ce_1", "다중물떼기", "TECHNOLOGY", 2, False, ["D1"])
    return KgRetriever(_Graph([m]), total_documents=12070)


def test_uses_in_document_search_when_vector_given() -> None:
    """질의 벡터가 있으면 문서 **안에서** 관련 청크를 고른다."""
    store = _Store()
    rows, _ = asyncio.run(
        _retriever().retrieve("다중물떼기 메탄 저감", store, top_k=2, query_vec=[0.1] * 8)
    )
    assert store.indoc_calls, "문서내 검색을 안 썼다"
    assert not store.front_calls, "앞청크 폴백이 불필요하게 호출됐다"
    assert rows and rows[0]["page"] == 47, f"표지를 집었다: {rows[0]['page']}"
    assert "메탄" in rows[0]["text"]


def test_falls_back_without_query_vector() -> None:
    """벡터가 없으면 예전 방식 — 근거가 아예 없는 것보다는 낫다."""
    store = _Store()
    rows, _ = asyncio.run(_retriever().retrieve("다중물떼기", store, top_k=2))
    assert store.front_calls and not store.indoc_calls
    assert rows and rows[0]["page"] == 1


def test_falls_back_when_store_lacks_method() -> None:
    """옛 저장소(메서드 없음)와도 동작해야 한다."""
    store = _OldStore()
    rows, _ = asyncio.run(_retriever().retrieve("다중물떼기", store, top_k=2, query_vec=[0.1] * 8))
    assert store.front_calls and rows


def test_row_shape_matches_both_paths() -> None:
    """두 경로가 같은 형태를 내야 호출자가 분기 없이 처리한다."""
    keys_vec = set(
        asyncio.run(_retriever().retrieve("다중물떼기", _Store(), top_k=1, query_vec=[0.1] * 8))[0][
            0
        ]
    )
    keys_front = set(asyncio.run(_retriever().retrieve("다중물떼기", _Store(), top_k=1))[0][0])
    missing = keys_front - keys_vec
    assert not missing, f"문서내검색 경로에 빠진 필드: {missing}"

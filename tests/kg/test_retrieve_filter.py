# tests/kg/test_retrieve_filter.py
"""CR-72 그래프 검색 제약이 Cypher까지 도달하는지.

연도·문서종류는 **벡터 스토어가 지킬 수 없다**(스키마에 필드가 없다). 그래서 이 경로가
유일한 통로다 — 여기서 새면 조건이 아무 데도 적용되지 않는다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kg.retrieve import KgRetriever


class _SpyGraph:
    """Cypher와 파라미터를 붙잡아 둔다."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self.cypher = ""

    def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.cypher = cypher
        self.params = params
        return []


def _retriever() -> tuple[KgRetriever, _SpyGraph]:
    g = _SpyGraph()
    return KgRetriever(g, total_documents=12070), g  # type: ignore[arg-type]


def test_year_reaches_cypher_params() -> None:
    r, g = _retriever()
    r.find_entities(["기후변화"], year_from=2021, year_to=2025)
    assert g.params["year_from"] == 2021 and g.params["year_to"] == 2025
    assert "d.year >= $year_from" in g.cypher and "d.year <= $year_to" in g.cypher


def test_document_type_and_entity_types_reach_cypher() -> None:
    r, g = _retriever()
    r.find_entities(["기후변화"], document_type="FINAL_REPORT", entity_types=("TECHNOLOGY",))
    assert g.params["doc_type"] == "FINAL_REPORT"
    assert g.params["entity_types"] == ["TECHNOLOGY"]
    assert "d.document_type = $doc_type" in g.cypher
    assert "c.entity_type IN $entity_types" in g.cypher


def test_no_filter_passes_nulls() -> None:
    """제약이 없으면 전부 NULL — 조건이 통과하도록 한 쿼리로 쓴다(분기 없음)."""
    r, g = _retriever()
    r.find_entities(["기후변화"])
    assert g.params["year_from"] is None
    assert g.params["doc_type"] is None
    assert g.params["entity_types"] is None


def test_entity_type_filter_is_inside_first_match() -> None:
    """유형 필터는 후보를 고르는 첫 MATCH에 있어야 한다.

    `LIMIT` 뒤에 걸면 상위 60개를 뽑은 **다음** 걸러서, 원하는 유형이 그 안에 없으면
    결과가 0이 된다.
    """
    r, g = _retriever()
    r.find_entities(["기후변화"], entity_types=("METHOD",))
    before_limit = g.cypher.split("LIMIT $limit")[0]
    assert "c.entity_type IN $entity_types" in before_limit


def test_retrieve_forwards_filter() -> None:
    """`retrieve`가 필터를 `find_entities`까지 그대로 넘기는지."""

    class _Filt:
        year_from, year_to, document_type, entity_types = 2021, None, None, ()
        is_empty = False

        def describe(self) -> str:
            return "2021년 이후"

    r, g = _retriever()
    asyncio.run(r.retrieve("기후변화 대응", vstore=None, top_k=5, filt=_Filt()))
    assert g.params["year_from"] == 2021

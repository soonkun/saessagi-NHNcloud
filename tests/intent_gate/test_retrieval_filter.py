# tests/intent_gate/test_retrieval_filter.py
"""CR-72 검색 제약 — 분류기가 뽑고, 라우팅이 그래프 전용을 정한다.

**왜 필요한가**: "최근 5년 자료로"라고 물어도 그 조건이 아무 데도 적용되지 않았다.
벡터 스토어 스키마에 연도가 없어서(doc_id·doc_name·category·page·section·chunk_id·
text·bbox·source_path·vector) 하이브리드로는 원리적으로 지킬 수 없다.
사용자는 걸러진 줄 알고 답을 읽는다 — 틀린 답을 걸러진 답으로 위장하는 셈이다.
"""

from __future__ import annotations

from datetime import datetime

from intent_gate.classifier import _parse_filter
from intent_gate.routing import decide_with_confidence
from intent_gate.types import IntentResult, RetrievalFilter

THIS_YEAR = datetime.now().year


def _result(filt: RetrievalFilter | None) -> IntentResult:
    return IntentResult(
        intent="doc_query",
        confidence=0.9,
        reason="테스트",
        source="llm",
        needs_search=True,
        retrieval_filter=filt,
    )


# ── 분류기 파싱 ───────────────────────────────────────────────────────────────


def test_recent_years_becomes_absolute() -> None:
    """ "최근 5년"은 **코드가** 연도로 바꾼다 — 모델에게 날짜 산술을 시키면 틀린다."""
    f = _parse_filter({"recent_years": 5})
    assert f is not None
    assert f.year_from == THIS_YEAR - 4  # 올해 포함 5개 연도
    assert f.year_to is None


def test_absolute_years_pass_through() -> None:
    f = _parse_filter({"year_from": 2018, "year_to": 2022})
    assert f is not None and (f.year_from, f.year_to) == (2018, 2022)


def test_no_constraint_returns_none() -> None:
    """제약이 없으면 None — 지금까지의 하이브리드 동작이 유지된다."""
    assert _parse_filter({"recent_years": None, "entity_types": []}) is None
    assert _parse_filter({}) is None


def test_missing_field_returns_none() -> None:
    """경량 분류기가 필드를 통째로 빠뜨려도 안전하게 떨어진다."""
    assert _parse_filter(None) is None
    assert _parse_filter("최근 5년") is None


def test_nonsense_years_are_dropped() -> None:
    """오타·헛값만 버린다."""
    assert _parse_filter({"year_from": 1200}) is None
    assert _parse_filter({"year_from": 9999}) is None


def test_future_year_is_kept() -> None:
    """미래 연도를 버리면 **제약이 조용히 무시된다** — 이 기능의 핵심을 깨뜨린다.

    실측 사고: 상한을 `올해+1`로 두었더니 "2030년 이후 자료" 질문에서 필터가 사라져
    전체 검색이 돌았다. 없는 범위면 0건이 나오고 "그 범위에 자료가 없다"고 답해야 한다.
    """
    f = _parse_filter({"year_from": 2030})
    assert f is not None and f.year_from == 2030


def test_reversed_range_is_dropped() -> None:
    """from > to는 결과가 항상 0건이 되므로 제약을 버린다."""
    assert _parse_filter({"year_from": 2022, "year_to": 2018}) is None


def test_unknown_enum_values_are_dropped() -> None:
    """화이트리스트에 없는 값은 무시한다 — Cypher에 그대로 흘리지 않는다."""
    f = _parse_filter({"document_type": "논문", "entity_types": ["HACK", "TECHNOLOGY"]})
    assert f is not None
    assert f.document_type is None
    assert f.entity_types == ("TECHNOLOGY",)


def test_bool_is_not_int() -> None:
    """`True`가 1로 새어 들어가면 엉뚱한 연도가 된다."""
    assert _parse_filter({"recent_years": True}) is None


# ── 그래프 전용 판정 ──────────────────────────────────────────────────────────


def test_year_forces_graph_only() -> None:
    """연도는 벡터가 못 지킨다 → 그래프 전용."""
    d = decide_with_confidence(_result(RetrievalFilter(year_from=2021)))
    assert d.graph_only is True
    assert d.retrieval_filter is not None and d.retrieval_filter.year_from == 2021


def test_document_type_forces_graph_only() -> None:
    d = decide_with_confidence(_result(RetrievalFilter(document_type="FINAL_REPORT")))
    assert d.graph_only is True


def test_entity_type_alone_keeps_hybrid() -> None:
    """유형만 있으면 하이브리드 유지 — 두 경로가 실측 0% 겹쳐 상호보완이다."""
    d = decide_with_confidence(_result(RetrievalFilter(entity_types=("TECHNOLOGY",))))
    assert d.graph_only is False
    assert d.retrieval_filter is not None


def test_no_filter_keeps_hybrid() -> None:
    """제약 없는 질문은 지금과 똑같이 동작해야 한다 (회귀)."""
    d = decide_with_confidence(_result(None))
    assert d.graph_only is False and d.retrieval_filter is None


def test_filter_survives_every_routing_branch() -> None:
    """저신뢰·후속 등 어느 분기로 가도 제약이 사라지면 안 된다.

    분기가 여섯 군데라 각 return에 필드를 더하면 한 곳만 빠뜨려도 조용히 사라진다.
    """
    filt = RetrievalFilter(year_from=2021)
    for intent, conf in [("doc_query", 0.2), ("chat", 0.2), ("followup", 0.9), ("note_save", 0.9)]:
        r = IntentResult(
            intent=intent,  # type: ignore[arg-type]
            confidence=conf,
            reason="t",
            source="llm",
            needs_search=True,
            retrieval_filter=filt,
        )
        d = decide_with_confidence(r)
        assert d.retrieval_filter == filt, f"{intent}/{conf}에서 제약이 사라졌다"


# ── 표시 문구 ─────────────────────────────────────────────────────────────────


def test_describe_is_human_readable() -> None:
    """사용자가 어떤 범위로 좁혔는지 알아야 결과가 적은 이유를 안다."""
    assert RetrievalFilter(year_from=2021).describe() == "2021년 이후"
    assert RetrievalFilter(year_from=2018, year_to=2022).describe() == "2018~2022년"
    assert "완결보고서" in RetrievalFilter(document_type="FINAL_REPORT").describe()

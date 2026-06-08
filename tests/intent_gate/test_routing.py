# tests/intent_gate/test_routing.py
"""M_16 IntentGate routing.decide / decide_with_confidence 단위 테스트."""

from __future__ import annotations

import pytest

from intent_gate.routing import RoutingDecision, decide, decide_with_confidence
from intent_gate.types import IntentResult


# ── Helper ─────────────────────────────────────────────────────────────────────


def _result(intent: str, confidence: float = 0.9, source: str = "llm") -> IntentResult:
    return IntentResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        reason="테스트",
        source=source,  # type: ignore[arg-type]
    )


# ── 정상 케이스: 매핑 표 전수 ──────────────────────────────────────────────────


def test_N1_calendar_add_no_rag_hint_add_event():
    """N-1: calendar_add → inject_rag=False, tool_hint에 'add_event', autonomous=False."""
    dec = decide_with_confidence(_result("calendar_add"))
    assert dec.inject_rag is False
    assert "add_event" in (dec.tool_hint or "")
    assert dec.autonomous is False
    assert dec.rag_source == "both"


def test_N2_calendar_query_no_rag_hint_get_events():
    """N-2: calendar_query → inject_rag=False, tool_hint에 'get_events'."""
    dec = decide_with_confidence(_result("calendar_query"))
    assert dec.inject_rag is False
    assert "get_events" in (dec.tool_hint or "")
    assert dec.autonomous is False


def test_N3_doc_query_rag_on_docs():
    """N-3: doc_query → inject_rag=True, rag_source='docs', tool_hint에 'search_docs'."""
    dec = decide_with_confidence(_result("doc_query"))
    assert dec.inject_rag is True
    assert dec.rag_source == "docs"
    assert "search_docs" in (dec.tool_hint or "")
    assert dec.autonomous is False


def test_N4_note_save_no_rag_hint_save():
    """N-4: note_save → inject_rag=False, tool_hint에 'save_knowledge_note'."""
    dec = decide_with_confidence(_result("note_save"))
    assert dec.inject_rag is False
    assert "save_knowledge_note" in (dec.tool_hint or "")
    assert dec.autonomous is False


def test_N5_work_query_rag_on_notes():
    """N-5: work_query → inject_rag=True, rag_source='notes'."""
    dec = decide_with_confidence(_result("work_query"))
    assert dec.inject_rag is True
    assert dec.rag_source == "notes"
    assert "search_docs" in (dec.tool_hint or "")
    assert dec.autonomous is False


def test_N6_chat_no_rag_no_hint():
    """N-6: chat → inject_rag=False, tool_hint=None."""
    dec = decide_with_confidence(_result("chat"))
    assert dec.inject_rag is False
    assert dec.tool_hint is None
    assert dec.autonomous is False


# ── 엣지 케이스: 저신뢰 폴백 ──────────────────────────────────────────────────


def test_E1_lowconf_calendar_add_autonomous():
    """E-1: confidence=0.40, intent=calendar_add, source=fallback_lowconf → autonomous=True."""
    result = _result("calendar_add", confidence=0.40, source="fallback_lowconf")
    dec = decide_with_confidence(result, confidence_threshold=0.55)
    assert dec.autonomous is True
    assert dec.rag_source == "both"


def test_E1_lowconf_calendar_add_via_llm_source():
    """E-1 변형: source=llm, confidence<threshold, 비-RAG 라벨 → autonomous=True."""
    result = _result("calendar_add", confidence=0.40, source="llm")
    dec = decide_with_confidence(result, confidence_threshold=0.55)
    assert dec.autonomous is True
    assert dec.rag_source == "both"


def test_E2_lowconf_doc_query_source_fallback_both():
    """E-2: confidence=0.40, intent=doc_query, source=llm → rag_source='both', autonomous=False."""
    result = _result("doc_query", confidence=0.40, source="llm")
    dec = decide_with_confidence(result, confidence_threshold=0.55)
    assert dec.inject_rag is True
    assert dec.rag_source == "both"
    assert dec.autonomous is False


def test_E3_lowconf_work_query_source_fallback_both():
    """E-3: confidence=0.40, intent=work_query → rag_source='both', autonomous=False."""
    result = _result("work_query", confidence=0.40, source="llm")
    dec = decide_with_confidence(result, confidence_threshold=0.55)
    assert dec.inject_rag is True
    assert dec.rag_source == "both"
    assert dec.autonomous is False


def test_E4_unknown_intent_as_chat_no_rag():
    """E-4: intent=chat(라벨 외 처리 후), source=llm, high conf → RAG off."""
    # classifier에서 chat으로 강등된 후 routing에 들어오는 시나리오
    result = _result("chat", confidence=0.85, source="llm")
    dec = decide_with_confidence(result)
    assert dec.inject_rag is False
    assert dec.tool_hint is None


def test_fallback_error_autonomous():
    """fallback_error → autonomous=True."""
    result = _result("chat", confidence=0.0, source="fallback_error")
    dec = decide_with_confidence(result)
    assert dec.autonomous is True
    assert dec.inject_rag is False  # legacy_rag_triggered 기본값 False


def test_fallback_disabled_autonomous():
    """fallback_disabled → autonomous=True."""
    result = _result("chat", confidence=0.0, source="fallback_disabled")
    dec = decide_with_confidence(result)
    assert dec.autonomous is True


def test_legacy_rag_triggered_propagated_to_inject_rag():
    """autonomous=True 폴백 시 legacy_rag_triggered가 inject_rag에 반영됨."""
    result = _result("chat", confidence=0.0, source="fallback_error")
    dec = decide_with_confidence(result, legacy_rag_triggered=True)
    assert dec.autonomous is True
    assert dec.inject_rag is True


def test_decide_simple_function():
    """기본 decide() 함수 — confidence threshold 없음, 고신뢰 경로만."""
    result = _result("doc_query", confidence=0.9, source="llm")
    dec = decide(result)
    assert dec.inject_rag is True
    assert dec.rag_source == "docs"


def test_RoutingDecision_is_frozen():
    """RoutingDecision은 frozen dataclass."""
    dec = RoutingDecision(inject_rag=True, rag_source="docs", tool_hint="hint", autonomous=False)
    with pytest.raises((AttributeError, TypeError)):
        dec.inject_rag = False  # type: ignore[misc]


# ── M_17 확장: answer_guide 필드 + prompt_overrides ────────────────────────────


def test_M17_N6_doc_query_with_prompt_overrides_has_answer_guide() -> None:
    """M_17 N-6: doc_query 턴에서 prompt_overrides가 있으면 answer_guide가 설정된다."""
    result = _result("doc_query", confidence=0.9, source="llm")
    overrides = {"doc_query_answer": "표로 정리해서 답변하세요"}
    dec = decide_with_confidence(result, prompt_overrides=overrides)
    assert dec.answer_guide == "표로 정리해서 답변하세요"
    assert dec.inject_rag is True


def test_M17_E1_empty_doc_query_answer_normalizes_to_none() -> None:
    """M_17 E-1: 빈 doc_query_answer → answer_guide=None (미주입)."""
    result = _result("doc_query", confidence=0.9, source="llm")
    overrides = {"doc_query_answer": ""}
    dec = decide_with_confidence(result, prompt_overrides=overrides)
    assert dec.answer_guide is None


def test_M17_E2_prompt_overrides_none_all_answer_guide_none() -> None:
    """M_17 E-2: prompt_overrides=None → 모든 의도에서 answer_guide=None. M_16 기존 동작 동일."""
    for intent in ("doc_query", "work_query", "note_save", "calendar_add", "chat"):
        result = _result(intent, confidence=0.9, source="llm")
        dec = decide_with_confidence(result, prompt_overrides=None)
        assert dec.answer_guide is None, f"{intent}: answer_guide는 None이어야 함"


def test_M17_E5_calendar_and_chat_no_answer_guide() -> None:
    """M_17 E-5: calendar/chat 의도 → answer_guide=None (doc/work/note 외 키 미주입)."""
    overrides = {
        "doc_query_answer": "표",
        "work_query_answer": "노트",
        "knowledge_note": "저장",
    }
    for intent in ("calendar_add", "calendar_query", "chat"):
        result = _result(intent, confidence=0.9, source="llm")
        dec = decide_with_confidence(result, prompt_overrides=overrides)
        assert dec.answer_guide is None, f"{intent}: answer_guide는 None이어야 함"


def test_M17_work_query_with_overrides_has_answer_guide() -> None:
    """work_query 의도에서 work_query_answer 지침이 answer_guide로 설정된다."""
    result = _result("work_query", confidence=0.9, source="llm")
    overrides = {"work_query_answer": "내 업무 노트 기반으로 답하세요"}
    dec = decide_with_confidence(result, prompt_overrides=overrides)
    assert dec.answer_guide == "내 업무 노트 기반으로 답하세요"


def test_M17_note_save_with_overrides_has_answer_guide() -> None:
    """note_save 의도에서 knowledge_note 지침이 answer_guide로 설정된다."""
    result = _result("note_save", confidence=0.9, source="llm")
    overrides = {"knowledge_note": "업무 노트 형식으로 저장"}
    dec = decide_with_confidence(result, prompt_overrides=overrides)
    assert dec.answer_guide == "업무 노트 형식으로 저장"


def test_M17_RoutingDecision_has_answer_guide_field() -> None:
    """RoutingDecision에 answer_guide 필드가 있다."""
    dec = RoutingDecision(
        inject_rag=True,
        rag_source="docs",
        tool_hint="hint",
        autonomous=False,
        answer_guide="가이드",
    )
    assert dec.answer_guide == "가이드"


def test_M17_RoutingDecision_answer_guide_default_none() -> None:
    """RoutingDecision.answer_guide 기본값은 None이다."""
    dec = RoutingDecision(inject_rag=False, rag_source="both", tool_hint=None, autonomous=False)
    assert dec.answer_guide is None

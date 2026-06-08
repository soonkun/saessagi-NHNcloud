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

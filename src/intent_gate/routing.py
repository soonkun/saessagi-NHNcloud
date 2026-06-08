# src/intent_gate/routing.py
"""M_16 IntentGate 라우팅 결정 순수 함수."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .types import IntentResult, RagSource

logger = logging.getLogger(__name__)

# ── tool_hint 문구 상수 ────────────────────────────────────────────────────────

_HINT_CALENDAR_ADD = (
    "사용자가 일정 등록을 요청했습니다. 반드시 add_event 도구를 호출하세요. "
    "시작 시각은 ISO 8601(+09:00)로 변환."
)
_HINT_CALENDAR_QUERY = (
    "사용자가 일정 조회를 요청했습니다. get_events 도구로 해당 기간을 조회하세요."
)
_HINT_DOC_QUERY = (
    "사용자가 사내 공용 문서·규정에 대해 질문했습니다. "
    "주입된 [관련 문서 검색 결과]를 근거로 답하고, 부족하면 search_docs를 호출하세요."
)
_HINT_NOTE_SAVE = (
    "사용자가 처리한 업무를 보고했습니다. save_knowledge_note 도구로 노트를 저장하세요."
)
_HINT_WORK_QUERY = (
    "사용자가 자신의 업무이력(개인 노트)에 대해 질문했습니다. "
    "주입된 [관련 노트 검색 결과]를 근거로 답하고, 부족하면 search_docs를 호출하세요."
)

_TOOL_HINTS: dict[str, str | None] = {
    "calendar_add": _HINT_CALENDAR_ADD,
    "calendar_query": _HINT_CALENDAR_QUERY,
    "doc_query": _HINT_DOC_QUERY,
    "note_save": _HINT_NOTE_SAVE,
    "work_query": _HINT_WORK_QUERY,
    "chat": None,
}


@dataclass(frozen=True)
class RoutingDecision:
    """라우팅 결정 결과."""

    inject_rag: bool  # _augment_with_rag가 벡터 검색·주입을 수행할지
    rag_source: RagSource  # "docs" | "notes" | "both" — retrieve에 넘길 소스 필터
    tool_hint: str | None  # LLM 시스템 메시지에 1줄로 주입할 도구 유도 지시 (None이면 미주입)
    autonomous: bool  # True면 게이트가 강제하지 않고 LLM 자율 (fallback 경로)


def decide(result: IntentResult, *, legacy_rag_triggered: bool = False) -> RoutingDecision:
    """IntentResult → RoutingDecision 순수 함수.

    스펙 §라우팅 규칙의 매핑 표를 결정론적으로 구현한다.
    threshold 없이 호출 가능한 공개 API — decide_with_confidence(threshold=0.0)에 위임한다
    (즉, confidence 값이 threshold 미만으로 떨어지지 않아 저신뢰 폴백이 발생하지 않음).

    Args:
        result: 분류기가 반환한 IntentResult.
        legacy_rag_triggered: 자율 모드 폴백 시 레거시 키워드 휴리스틱 결과.
            (autonomous=True일 때만 inject_rag에 반영됨)

    Returns:
        RoutingDecision.
    """
    return decide_with_confidence(
        result,
        confidence_threshold=0.0,
        legacy_rag_triggered=legacy_rag_triggered,
    )


def decide_with_confidence(
    result: IntentResult,
    *,
    confidence_threshold: float = 0.55,
    legacy_rag_triggered: bool = False,
) -> RoutingDecision:
    """confidence_threshold를 반영한 확장 라우팅 결정 함수.

    스펙 §저신뢰 폴백 — doc_query/work_query의 저신뢰 소스 폴백 구현.

    Args:
        result: 분류기가 반환한 IntentResult.
        confidence_threshold: 저신뢰 판정 임계값.
        legacy_rag_triggered: 자율 모드 폴백 시 레거시 키워드 결과.

    Returns:
        RoutingDecision.
    """
    # 분류기 실패/비활성/비-RAG 저신뢰 → 전면 자율 폴백
    if result.source != "llm":
        logger.debug(
            "IntentGate decide_with_confidence: source=%s → autonomous 폴백",
            result.source,
        )
        return RoutingDecision(
            inject_rag=legacy_rag_triggered,
            rag_source="both",
            tool_hint=None,
            autonomous=True,
        )

    intent = result.intent

    # doc_query / work_query
    if intent in ("doc_query", "work_query"):
        if result.confidence < confidence_threshold:
            # 소스 저신뢰 폴백: RAG는 켜되 둘 다 검색
            rag_source: RagSource = "both"
            logger.info(
                "IntentGate: intent=%s, conf=%.2f < threshold=%.2f → rag_source=both (소스 폴백)",
                intent,
                result.confidence,
                confidence_threshold,
            )
        else:
            rag_source = "docs" if intent == "doc_query" else "notes"

        hint = _TOOL_HINTS[intent]
        return RoutingDecision(
            inject_rag=True,
            rag_source=rag_source,
            tool_hint=hint,
            autonomous=False,
        )

    # 비-RAG 라벨(calendar_add, calendar_query, note_save, chat)
    # 저신뢰이면 전면 자율 폴백
    if result.confidence < confidence_threshold:
        logger.info(
            "IntentGate: intent=%s, conf=%.2f < threshold=%.2f (비-RAG 라벨) → autonomous 폴백",
            intent,
            result.confidence,
            confidence_threshold,
        )
        return RoutingDecision(
            inject_rag=legacy_rag_triggered,
            rag_source="both",
            tool_hint=None,
            autonomous=True,
        )

    # 고신뢰 라벨
    return RoutingDecision(
        inject_rag=False,
        rag_source="both",
        tool_hint=_TOOL_HINTS[intent],
        autonomous=False,
    )

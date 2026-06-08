# src/agent_prompts/registry.py
"""M_17 AgentInstructions — PromptRegistry 메타 + effective_prompt."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from .defaults import DOC_QUERY_ANSWER_GUIDE, KNOWLEDGE_NOTE_GUIDE, WORK_QUERY_ANSWER_GUIDE

logger = logging.getLogger(__name__)

PromptKey = Literal[
    "persona",
    "knowledge_note",
    "doc_query_answer",
    "work_query_answer",
    "intent_classify",
    "meeting_minutes",
]

PROMPT_KEYS: tuple[PromptKey, ...] = (
    "persona",
    "knowledge_note",
    "doc_query_answer",
    "work_query_answer",
    "intent_classify",
    "meeting_minutes",
)


@dataclass(frozen=True)
class PromptMeta:
    """키별 메타데이터."""

    key: PromptKey
    label: str
    risk: Literal["low", "medium", "high"]
    apply_path: Literal["agent_reinit", "gate_injection", "set_custom_prompt", "classifier_reload"]


_REGISTRY: dict[PromptKey, PromptMeta] = {
    "persona": PromptMeta(
        key="persona",
        label="대화 페르소나",
        risk="medium",
        apply_path="agent_reinit",
    ),
    "knowledge_note": PromptMeta(
        key="knowledge_note",
        label="업무노트 작성 지침",
        risk="low",
        apply_path="gate_injection",
    ),
    "doc_query_answer": PromptMeta(
        key="doc_query_answer",
        label="자료질의 답변 지침",
        risk="low",
        apply_path="gate_injection",
    ),
    "work_query_answer": PromptMeta(
        key="work_query_answer",
        label="업무질의 답변 지침",
        risk="low",
        apply_path="gate_injection",
    ),
    "intent_classify": PromptMeta(
        key="intent_classify",
        label="의도 분류 기준 (고급)",
        risk="high",
        apply_path="classifier_reload",
    ),
    "meeting_minutes": PromptMeta(
        key="meeting_minutes",
        label="회의록 작성 지침",
        risk="low",
        apply_path="set_custom_prompt",
    ),
}


def get_meta(key: PromptKey) -> PromptMeta:
    """키에 대한 메타데이터를 반환한다."""
    return _REGISTRY[key]


def get_risk(key: PromptKey) -> Literal["low", "medium", "high"]:
    """키의 위험도를 반환한다."""
    return _REGISTRY[key].risk


def get_label(key: PromptKey) -> str:
    """키의 한국어 레이블을 반환한다."""
    return _REGISTRY[key].label


def get_default(key: PromptKey) -> str | None:
    """키의 기본값 상수를 반환한다.

    persona는 코드 상수 기본값이 없어 None을 반환.
    나머지는 각 모듈의 상수를 반환.
    """
    if key == "persona":
        return None
    if key == "knowledge_note":
        return KNOWLEDGE_NOTE_GUIDE
    if key == "doc_query_answer":
        return DOC_QUERY_ANSWER_GUIDE
    if key == "work_query_answer":
        return WORK_QUERY_ANSWER_GUIDE
    if key == "intent_classify":
        try:
            from intent_gate.prompts import SYSTEM_PROMPT

            return str(SYSTEM_PROMPT)
        except ImportError:
            logger.warning("intent_gate.prompts 임포트 실패")
            return ""
    if key == "meeting_minutes":
        try:
            from meeting_minutes.prompts import SYSTEM_PROMPT

            return str(SYSTEM_PROMPT)
        except ImportError:
            logger.warning("meeting_minutes.prompts 임포트 실패")
            return ""
    return None


def effective_prompt(
    key: PromptKey,
    app_config: Any,
    character_config: Any,
) -> str:
    """현재 실효 프롬프트를 반환한다.

    커스텀(agent_prompts[key])이 있으면 우선, 빈값이면 default.
    persona는 character_config.persona_prompt를 반환.

    Args:
        key: 지침 키.
        app_config: AppConfig 인스턴스 (None이면 default 반환).
        character_config: upstream character_config (persona 키에 사용).

    Returns:
        현재 실효 프롬프트 문자열.
    """
    if key == "persona":
        if character_config is not None:
            try:
                return str(character_config.persona_prompt)
            except AttributeError:
                pass
        return ""

    # 커스텀 값 조회
    custom: str = ""
    if app_config is not None:
        try:
            agent_prompts = getattr(app_config, "agent_prompts", None)
            if agent_prompts is not None:
                custom = getattr(agent_prompts, key, "") or ""
        except Exception as exc:
            logger.debug("agent_prompts 조회 실패 (key=%s): %s", key, exc)

    if custom.strip():
        return custom

    # 기본값 반환
    default = get_default(key)
    return default or ""

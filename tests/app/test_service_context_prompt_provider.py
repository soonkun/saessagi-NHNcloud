# tests/app/test_service_context_prompt_provider.py
"""_make_prompt_provider 클로저 팩토리 — 주입 경로 계약 검증.

Critic 이슈 #1/#2: _prompt_provider가 effective_prompt(기본값 폴백)를 사용하면
커스텀 0건(신규 설치) 상태에서도 기본 상수가 반환되어 doc_query/work_query/note_save
모든 턴에 [작성 지침]이 강제 주입된다 → M_16 회귀 0 계약 위반.

수정: 클로저는 raw 커스텀 값만 반환, 빈 문자열 → 라우팅에서 None 정규화 → 미주입.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# 헬퍼: mock AppConfig 생성
# ---------------------------------------------------------------------------


def _make_app_config(
    doc_query_answer: str = "",
    work_query_answer: str = "",
    knowledge_note: str = "",
) -> Any:
    """테스트용 AppConfig-like 객체 생성."""
    ap = MagicMock()
    ap.doc_query_answer = doc_query_answer
    ap.work_query_answer = work_query_answer
    ap.knowledge_note = knowledge_note

    cfg = MagicMock()
    cfg.agent_prompts = ap
    return cfg


# ---------------------------------------------------------------------------
# 핵심 계약 테스트 (Critic #1/#2)
# ---------------------------------------------------------------------------


def test_prompt_provider_empty_custom_returns_empty_strings() -> None:
    """커스텀 0건(신규 설치 상태) → 세 키 모두 빈 문자열 반환.

    이 테스트가 버그를 잡는 근거:
    - 버그 상태(effective_prompt 사용): 빈 커스텀 → 기본 상수(DOC_QUERY_ANSWER_GUIDE 등) 반환
    - 수정 후(raw 커스텀만): 빈 커스텀 → 빈 문자열 반환 → 이 테스트 통과
    """
    from app.service_context import _make_prompt_provider

    cfg = _make_app_config()  # 세 키 모두 ""
    provider = _make_prompt_provider(cfg)
    result = provider()

    assert result["doc_query_answer"] == "", (
        f"doc_query_answer should be '' (no custom), got {result['doc_query_answer']!r}"
    )
    assert result["work_query_answer"] == "", (
        f"work_query_answer should be '' (no custom), got {result['work_query_answer']!r}"
    )
    assert result["knowledge_note"] == "", (
        f"knowledge_note should be '' (no custom), got {result['knowledge_note']!r}"
    )


def test_prompt_provider_none_app_config_returns_empty_dict() -> None:
    """app_config=None → 빈 dict 반환."""
    from app.service_context import _make_prompt_provider

    provider = _make_prompt_provider(None)
    result = provider()
    assert result == {}


def test_prompt_provider_custom_value_returned() -> None:
    """커스텀 설정 시 그 값이 반환됨."""
    from app.service_context import _make_prompt_provider

    cfg = _make_app_config(
        doc_query_answer="표로 정리해주세요",
        work_query_answer="간결하게 답해주세요",
        knowledge_note="핵심만 저장해주세요",
    )
    provider = _make_prompt_provider(cfg)
    result = provider()

    assert result["doc_query_answer"] == "표로 정리해주세요"
    assert result["work_query_answer"] == "간결하게 답해주세요"
    assert result["knowledge_note"] == "핵심만 저장해주세요"


def test_prompt_provider_whitespace_only_normalizes_to_empty() -> None:
    """공백만 있는 커스텀 → strip 후 빈 문자열."""
    from app.service_context import _make_prompt_provider

    cfg = _make_app_config(
        doc_query_answer="   ",
        work_query_answer="\t\n",
        knowledge_note="  ",
    )
    provider = _make_prompt_provider(cfg)
    result = provider()

    assert result["doc_query_answer"] == ""
    assert result["work_query_answer"] == ""
    assert result["knowledge_note"] == ""


def test_prompt_provider_does_not_use_default_constants() -> None:
    """커스텀이 없을 때 기본 상수(DOC_QUERY_ANSWER_GUIDE 등)를 반환하지 않아야 함.

    이것이 Critic #1 핵심: effective_prompt는 빈 커스텀 → 기본 상수 반환하므로
    주입 경로에서 사용 금지. raw 커스텀만 사용해야 한다.
    """
    from agent_prompts.defaults import (
        DOC_QUERY_ANSWER_GUIDE,
        KNOWLEDGE_NOTE_GUIDE,
        WORK_QUERY_ANSWER_GUIDE,
    )
    from app.service_context import _make_prompt_provider

    cfg = _make_app_config()  # 세 키 모두 ""
    provider = _make_prompt_provider(cfg)
    result = provider()

    # 기본 상수가 반환되어서는 안 됨
    assert result["doc_query_answer"] != DOC_QUERY_ANSWER_GUIDE, (
        "클로저가 DOC_QUERY_ANSWER_GUIDE를 반환함 — effective_prompt 사용 버그"
    )
    assert result["work_query_answer"] != WORK_QUERY_ANSWER_GUIDE, (
        "클로저가 WORK_QUERY_ANSWER_GUIDE를 반환함 — effective_prompt 사용 버그"
    )
    assert result["knowledge_note"] != KNOWLEDGE_NOTE_GUIDE, (
        "클로저가 KNOWLEDGE_NOTE_GUIDE를 반환함 — effective_prompt 사용 버그"
    )


# ---------------------------------------------------------------------------
# 미주입 경로 계약 테스트 (adapter 연결 검증, Critic #2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_custom_no_injection_in_adapter() -> None:
    """커스텀 0건 → adapter chat() 시 INPUT에 [작성 지침] 미주입.

    이 테스트가 M_16 회귀 0을 보장한다:
    - prompt_provider가 세 키 모두 "" 반환
    - routing이 answer_guide=None 정규화
    - adapter가 [작성 지침] 텍스트를 INPUT에 삽입하지 않음
    """
    from unittest.mock import AsyncMock, MagicMock

    from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

    from app.service_context import _make_prompt_provider
    from agent.upstream_adapter import BasicMemoryAgentAdapter
    from intent_gate.types import IntentResult

    # 커스텀 0건 config
    cfg = _make_app_config()
    provider = _make_prompt_provider(cfg)

    # doc_query 의도 mock classifier
    mock_classifier = MagicMock()
    mock_classifier._confidence_threshold = 0.55
    mock_classifier.classify = AsyncMock(
        return_value=IntentResult(
            intent="doc_query",
            confidence=0.9,
            reason="테스트",
            source="llm",
        )
    )

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

    captured: list[Any] = []

    async def capture_chat(input_data: Any) -> Any:
        captured.append(input_data)
        from agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = capture_chat

    adapter = BasicMemoryAgentAdapter(
        mock_agent,
        rag_service=None,
        intent_classifier=mock_classifier,
        prompt_provider=provider,
    )

    batch = BatchInput(texts=[TextData(source=TextSource.INPUT, content="자료 알려줘")])
    async for _ in adapter.chat(batch):
        pass

    assert len(captured) == 1
    texts = captured[0].texts or []
    contents = [t.content for t in texts if t.source == TextSource.INPUT]
    assert not any("[작성 지침]" in c for c in contents), (
        f"커스텀 0건인데도 [작성 지침]이 주입됨: {contents}"
    )


@pytest.mark.asyncio
async def test_custom_set_injection_in_adapter() -> None:
    """커스텀 설정 → adapter chat() 시 INPUT에 [작성 지침] 주입됨."""
    from unittest.mock import AsyncMock, MagicMock

    from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

    from app.service_context import _make_prompt_provider
    from agent.upstream_adapter import BasicMemoryAgentAdapter
    from intent_gate.types import IntentResult

    cfg = _make_app_config(doc_query_answer="표로 정리")
    provider = _make_prompt_provider(cfg)

    mock_classifier = MagicMock()
    mock_classifier._confidence_threshold = 0.55
    mock_classifier.classify = AsyncMock(
        return_value=IntentResult(
            intent="doc_query",
            confidence=0.9,
            reason="테스트",
            source="llm",
        )
    )

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

    captured: list[Any] = []

    async def capture_chat(input_data: Any) -> Any:
        captured.append(input_data)
        from agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = capture_chat

    adapter = BasicMemoryAgentAdapter(
        mock_agent,
        rag_service=None,
        intent_classifier=mock_classifier,
        prompt_provider=provider,
    )

    batch = BatchInput(texts=[TextData(source=TextSource.INPUT, content="자료 알려줘")])
    async for _ in adapter.chat(batch):
        pass

    assert len(captured) == 1
    texts = captured[0].texts or []
    contents = [t.content for t in texts if t.source == TextSource.INPUT]
    assert any("[작성 지침] 표로 정리" in c for c in contents), (
        f"커스텀 설정했는데 [작성 지침]이 없음: {contents}"
    )

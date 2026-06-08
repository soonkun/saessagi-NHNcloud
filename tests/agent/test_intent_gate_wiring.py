# tests/agent/test_intent_gate_wiring.py
"""M_16 upstream_adapter + IntentGate 통합(wiring) 테스트.

실제 LLM 호출 없이 결정론적으로 동작한다.
FakeAgent/FakeRag 패턴 사용.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

from src.agent.upstream_adapter import BasicMemoryAgentAdapter
from src.intent_gate.types import IntentResult


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _make_text_batch(text: str) -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


def _make_intent_result(
    intent: str,
    confidence: float = 0.9,
    source: str = "llm",
) -> IntentResult:
    return IntentResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        reason="테스트 고정값",
        source=source,  # type: ignore[arg-type]
    )


def _make_fake_inner_agent(response_text: str = "OK") -> MagicMock:
    """upstream AgentInterface처럼 동작하는 간단한 mock."""
    from src.agent.events import EndOfTurn, TextChunk

    async def _fake_chat(input_data: BatchInput) -> AsyncIterator[Any]:
        yield TextChunk(text=response_text)
        yield EndOfTurn()

    mock_agent = MagicMock()
    mock_agent.complete_json = AsyncMock()
    mock_agent.chat = _fake_chat
    mock_agent.aclose = AsyncMock()
    return mock_agent


def _make_fake_rag_service() -> MagicMock:
    """RagService mock — retrieve 호출 인자를 spy할 수 있다."""
    mock_rag = MagicMock()
    mock_rag.retrieve = MagicMock(return_value=MagicMock(found=False, hits=[]))
    return mock_rag


def _make_fake_classifier(intent_result: IntentResult) -> MagicMock:
    """IntentClassifier mock — classify가 항상 고정 결과를 반환한다."""
    mock_clf = MagicMock()
    mock_clf.classify = AsyncMock(return_value=intent_result)
    mock_clf._confidence_threshold = 0.55
    return mock_clf


async def _run_chat(adapter: BasicMemoryAgentAdapter, text: str) -> list[Any]:
    results = []
    async for item in adapter.chat(_make_text_batch(text)):
        results.append(item)
    return results


# ── 정상 케이스 ───────────────────────────────────────────────────────────────


class TestWiringNormal:
    """classifier=None / 고신뢰 의도별 RAG 소스 라우팅 검증."""

    @pytest.mark.asyncio
    async def test_classifier_none_retrieve_called_with_source_both(self) -> None:
        """classifier=None → _augment_with_rag가 retrieve를 source='both'로 호출(레거시 동작 유지)."""
        inner = _make_fake_inner_agent()
        rag = _make_fake_rag_service()

        # 키워드 트리거를 포함한 텍스트 → 레거시 RAG가 실행됨
        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=None)
        await _run_chat(adapter, "출장비 정산 방법 알려줘")

        rag.retrieve.assert_called_once()
        _, call_kwargs = rag.retrieve.call_args
        assert call_kwargs.get("source", "both") == "both"

    @pytest.mark.asyncio
    async def test_doc_query_high_conf_retrieve_source_docs(self) -> None:
        """intent=doc_query(고신뢰) → retrieve가 source='docs'로 호출됨."""
        inner = _make_fake_inner_agent()
        rag = _make_fake_rag_service()
        clf = _make_fake_classifier(_make_intent_result("doc_query", confidence=0.9))

        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=clf)
        await _run_chat(adapter, "연차 규정 뭐야?")

        rag.retrieve.assert_called_once()
        _, call_kwargs = rag.retrieve.call_args
        assert call_kwargs.get("source") == "docs", (
            f"doc_query 고신뢰는 source='docs'여야 하지만 {call_kwargs.get('source')!r}"
        )

    @pytest.mark.asyncio
    async def test_work_query_high_conf_retrieve_source_notes(self) -> None:
        """intent=work_query(고신뢰) → retrieve가 source='notes'로 호출됨."""
        inner = _make_fake_inner_agent()
        rag = _make_fake_rag_service()
        clf = _make_fake_classifier(_make_intent_result("work_query", confidence=0.9))

        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=clf)
        await _run_chat(adapter, "내가 지난주에 뭐 처리했지?")

        rag.retrieve.assert_called_once()
        _, call_kwargs = rag.retrieve.call_args
        assert call_kwargs.get("source") == "notes", (
            f"work_query 고신뢰는 source='notes'여야 하지만 {call_kwargs.get('source')!r}"
        )

    @pytest.mark.asyncio
    async def test_calendar_add_no_retrieve_tool_hint_injected(self) -> None:
        """intent=calendar_add → inject_rag=False(retrieve 미호출), tool_hint가 INPUT으로 삽입됨."""
        inner = _make_fake_inner_agent()
        rag = _make_fake_rag_service()
        clf = _make_fake_classifier(_make_intent_result("calendar_add", confidence=0.95))

        # chat()에서 augment 후 inner.chat에 넘겨지는 BatchInput을 캡처
        captured_inputs: list[BatchInput] = []
        original_chat = inner.chat

        async def capturing_chat(input_data: BatchInput) -> AsyncIterator[Any]:
            captured_inputs.append(input_data)
            async for item in original_chat(input_data):
                yield item

        inner.chat = capturing_chat

        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=clf)
        await _run_chat(adapter, "이번주 수요일 13시 30분에 팀 업무회의가 있어")

        # retrieve는 호출되지 않아야 함
        rag.retrieve.assert_not_called()

        # tool_hint가 INPUT으로 삽입되었는지 확인
        assert len(captured_inputs) == 1
        texts = captured_inputs[0].texts or []
        hint_texts = [t for t in texts if t.from_name == "의도게이트"]
        assert len(hint_texts) == 1, (
            f"의도게이트 hint가 정확히 1건이어야 하지만 {len(hint_texts)}건"
        )
        assert "add_event" in hint_texts[0].content, (
            f"calendar_add hint에 'add_event'가 없음: {hint_texts[0].content!r}"
        )

    @pytest.mark.asyncio
    async def test_classify_exception_fallback_to_legacy_and_chat_continues(self) -> None:
        """분류 중 예외 발생 → chat 전체가 죽지 않고 _last_routing=None → 레거시 경로로 동작."""
        inner = _make_fake_inner_agent("정상 응답")
        rag = _make_fake_rag_service()

        # classify가 예외를 던지는 classifier
        mock_clf = MagicMock()
        mock_clf.classify = AsyncMock(side_effect=RuntimeError("LLM 연결 실패"))
        mock_clf._confidence_threshold = 0.55

        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=mock_clf)

        # 예외가 전파되지 않고 정상 응답이 나와야 함
        results = await _run_chat(adapter, "출장비 정산 방법 알려줘")

        # _last_routing이 None으로 폴백
        assert adapter._last_routing is None

        # 응답 텍스트가 있어야 함 (chat이 죽지 않음)
        texts = [str(r) for r in results]
        assert any("정상 응답" in t for t in texts), f"chat이 죽은 것으로 보임: {texts}"


# ── 엣지 케이스: tool_hint 중복·누락 없음 ────────────────────────────────────


class TestToolHintExactlyOne:
    """tool_hint가 정확히 1건만 삽입(중복/누락 없음)."""

    @pytest.mark.asyncio
    async def test_calendar_add_tool_hint_exactly_one(self) -> None:
        """calendar_add 고신뢰 → tool_hint 1건만 삽입, 중복 없음."""
        inner = _make_fake_inner_agent()
        rag = _make_fake_rag_service()
        clf = _make_fake_classifier(_make_intent_result("calendar_add", confidence=0.95))

        captured_inputs: list[BatchInput] = []
        original_chat = inner.chat

        async def capturing_chat(input_data: BatchInput) -> AsyncIterator[Any]:
            captured_inputs.append(input_data)
            async for item in original_chat(input_data):
                yield item

        inner.chat = capturing_chat

        adapter = BasicMemoryAgentAdapter(inner, rag_service=rag, intent_classifier=clf)
        await _run_chat(adapter, "내일 오후 3시 회의 잡아줘")

        texts = captured_inputs[0].texts or []
        hint_texts = [t for t in texts if t.from_name == "의도게이트"]
        assert len(hint_texts) == 1, f"hint가 1건이어야 하지만 {len(hint_texts)}건: {hint_texts}"
        # hint가 사용자 메시지보다 앞에 있어야 함
        hint_idx = texts.index(hint_texts[0])
        user_texts = [
            i
            for i, t in enumerate(texts)
            if t.source == TextSource.INPUT and t.from_name != "의도게이트"
        ]
        assert all(hint_idx < ui for ui in user_texts), (
            "tool_hint가 사용자 메시지보다 앞에 있어야 함"
        )

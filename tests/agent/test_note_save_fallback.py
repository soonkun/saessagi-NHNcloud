# tests/agent/test_note_save_fallback.py
"""E-45 회귀 테스트 — note_save 의도인데 LLM이 도구 호출을 건너뛴 턴의 강제 저장."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

from src.agent.upstream_adapter import BasicMemoryAgentAdapter
from src.intent_gate.types import IntentResult


def _make_text_batch(text: str) -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


def _make_classifier(intent: str, confidence: float = 0.95) -> MagicMock:
    result = IntentResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        reason="테스트 고정값",
        source="llm",  # type: ignore[arg-type]
    )
    clf = MagicMock()
    clf.classify = AsyncMock(return_value=result)
    clf._confidence_threshold = 0.55
    return clf


def _make_inner_agent_no_tool_call(reply: str) -> MagicMock:
    """텍스트만 내고 도구를 호출하지 않는 (환각) agent mock."""
    from src.agent.events import EndOfTurn, TextChunk

    async def _fake_chat(input_data: BatchInput) -> AsyncIterator[Any]:
        yield TextChunk(text=reply)
        yield EndOfTurn()

    agent = MagicMock()
    agent.chat = _fake_chat
    agent.complete_json = AsyncMock(
        return_value={
            "title": "회의 결과보고서 작성",
            "summary": "## 상황\n회의 결과보고서를 작성함",
            "tags": ["회의"],
        }
    )
    agent.aclose = AsyncMock()
    return agent


def _make_tool_router(ok: bool = True) -> MagicMock:
    router = MagicMock()
    result = MagicMock()
    result.ok = ok
    result.payload = {
        "slug": "회의-결과보고서-작성",
        "title": "회의 결과보고서 작성",
        "note_marker": "[[note:회의-결과보고서-작성]]",
    }
    result.error = None if ok else "boom"
    router.dispatch = AsyncMock(return_value=result)
    return router


async def _collect(adapter: Any, text: str) -> list[Any]:
    return [item async for item in adapter.chat(_make_text_batch(text))]


USER_MSG = (
    "[첨부 자료: 회의결과보고서_fdc67831.hwpx (doc_id: 회의결과보고서_fdc67831.hwpx_7bf0958b)]\n"
    "오늘 회의 결과보고서를 작성했어."
)


@pytest.mark.asyncio
async def test_note_save_intent_without_tool_call_triggers_forced_save() -> None:
    """note_save 고신뢰 분류 + 도구 미호출 → ToolRouter.dispatch로 강제 저장 (E-45)."""
    router = _make_tool_router()
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_no_tool_call("노트로 저장해 두었어요! (사실은 저장 안 함)"),
        intent_classifier=_make_classifier("note_save"),
        tool_router=router,
    )
    outputs = await _collect(adapter, USER_MSG)

    router.dispatch.assert_awaited_once()
    name, args = router.dispatch.await_args.args
    assert name == "save_knowledge_note"
    assert args["related_docs"] == ["회의결과보고서_fdc67831.hwpx_7bf0958b"]
    assert args["summary"].startswith("## 상황")

    # 사용자에게 저장 완료 + 노트 마커가 전달돼야 한다
    texts = [o.display_text.text for o in outputs if hasattr(o, "display_text")]
    assert any("[[note:회의-결과보고서-작성]]" in t for t in texts)


@pytest.mark.asyncio
async def test_chat_intent_does_not_trigger_forced_save() -> None:
    """일반 chat 의도면 도구 미호출이어도 강제 저장하지 않는다."""
    router = _make_tool_router()
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_no_tool_call("안녕하세요!"),
        intent_classifier=_make_classifier("chat"),
        tool_router=router,
    )
    await _collect(adapter, "안녕?")
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_call_present_skips_forced_save() -> None:
    """LLM이 실제로 save_knowledge_note를 호출했으면 폴백이 중복 실행되지 않는다."""
    from src.agent.events import EndOfTurn, TextChunk, ToolCallStart

    async def _chat_with_tool(input_data: BatchInput) -> AsyncIterator[Any]:
        yield ToolCallStart(tool_id="t1", name="save_knowledge_note", arguments="{}")
        yield TextChunk(text="저장 완료!")
        yield EndOfTurn()

    agent = MagicMock()
    agent.chat = _chat_with_tool
    agent.complete_json = AsyncMock()
    agent.aclose = AsyncMock()

    router = _make_tool_router()
    adapter = BasicMemoryAgentAdapter(
        agent,
        intent_classifier=_make_classifier("note_save"),
        tool_router=router,
    )
    await _collect(adapter, USER_MSG)
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_confidence_autonomous_does_not_force_save() -> None:
    """저신뢰(autonomous 폴백) 분류에서는 강제 저장하지 않는다."""
    router = _make_tool_router()
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_no_tool_call("음, 알겠어요."),
        intent_classifier=_make_classifier("note_save", confidence=0.3),
        tool_router=router,
    )
    await _collect(adapter, USER_MSG)
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_failure_is_swallowed() -> None:
    """폴백 저장 실패는 사용자 응답 흐름을 깨지 않는다."""
    router = _make_tool_router(ok=False)
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_no_tool_call("정리했어요!"),
        intent_classifier=_make_classifier("note_save"),
        tool_router=router,
    )
    outputs = await _collect(adapter, USER_MSG)
    assert len(outputs) > 0  # 예외 없이 스트림 완료


def _make_inner_agent_failed_tool_call(reply: str) -> MagicMock:
    """도구를 호출했지만 결과가 실패(ok=False)인 agent mock — E-46 케이스.

    실사례: gemma가 save_knowledge_note를 호출하면서 필수 title을 누락해
    invalid_arguments로 거부됨 (2026-06-12 20:44 로그).
    """
    from src.agent.events import EndOfTurn, TextChunk, ToolCallResult, ToolCallStart

    async def _fake_chat(input_data: BatchInput) -> AsyncIterator[Any]:
        yield ToolCallStart(tool_id="t1", name="save_knowledge_note", arguments="{}")
        yield ToolCallResult(
            tool_id="t1",
            name="save_knowledge_note",
            ok=False,
            content="invalid_arguments at <root>: 'title' is a required property",
        )
        yield TextChunk(text=reply)
        yield EndOfTurn()

    agent = MagicMock()
    agent.chat = _fake_chat
    agent.complete_json = AsyncMock(
        return_value={
            "title": "회의 결과보고서 작성",
            "summary": "## 상황\n회의 결과보고서를 작성함",
            "tags": ["회의"],
        }
    )
    agent.aclose = AsyncMock()
    return agent


@pytest.mark.asyncio
async def test_e46_tool_call_failed_triggers_fallback() -> None:
    """E-46: 도구를 호출했지만 실패한 note_save 턴도 강제 저장 폴백이 발동한다."""
    router = _make_tool_router(ok=True)
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_failed_tool_call("노트로 저장해 두었어요!"),
        intent_classifier=_make_classifier("note_save"),
        tool_router=router,
    )
    await _collect(adapter, USER_MSG)
    router.dispatch.assert_awaited_once()
    assert router.dispatch.await_args.args[0] == "save_knowledge_note"


@pytest.mark.asyncio
async def test_e46_successful_tool_call_no_fallback() -> None:
    """저장이 성공한 턴엔 폴백이 발동하지 않는다 (결과 기준 추적 확인)."""
    from src.agent.events import EndOfTurn, TextChunk, ToolCallResult, ToolCallStart

    async def _fake_chat(input_data: BatchInput) -> AsyncIterator[Any]:
        yield ToolCallStart(tool_id="t1", name="save_knowledge_note", arguments="{}")
        yield ToolCallResult(tool_id="t1", name="save_knowledge_note", ok=True, content="ok")
        yield TextChunk(text="저장했어요!")
        yield EndOfTurn()

    agent = MagicMock()
    agent.chat = _fake_chat
    agent.aclose = AsyncMock()

    router = _make_tool_router(ok=True)
    adapter = BasicMemoryAgentAdapter(
        agent,
        intent_classifier=_make_classifier("note_save"),
        tool_router=router,
    )
    await _collect(adapter, USER_MSG)
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_intent_announce_emits_character_state() -> None:
    """의도분류 직후 캐릭터 상태 태그 + 안내음 메시지가 즉시 방출된다."""
    adapter = BasicMemoryAgentAdapter(
        _make_inner_agent_no_tool_call("네, 알겠어요"),
        intent_classifier=_make_classifier("doc_query"),
        tool_router=_make_tool_router(ok=True),
    )
    outs = await _collect(adapter, "업무편람에서 토양 관리 담당 부서 알려줘")
    sentence_outs = [o for o in outs if hasattr(o, "display_text")]
    assert sentence_outs, "SentenceOutput이 없습니다"
    first = sentence_outs[0]
    assert "[study]" in first.display_text.text
    assert "자료를 찾아볼게요" in first.tts_text
    # 마지막 본문 메시지엔 [neutral] 복귀 태그
    assert "[neutral]" in sentence_outs[-1].display_text.text

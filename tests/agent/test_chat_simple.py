# tests/agent/test_chat_simple.py
"""단순 스트리밍 경로 테스트 (N-2, E-1, E-4, E-8, A-7, A-8)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

from src.agent.events import AgentError, EndOfTurn, TextChunk

from .conftest import _build_agent_async


def make_text_batch(text: str = "안녕") -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


async def collect_events(agent: Any, batch: BatchInput) -> list[Any]:
    """chat() 이벤트 전부 수집."""
    events = []
    async for ev in agent.chat(batch):
        events.append(ev)
    return events


# N-2: 단순 텍스트 스트리밍
@pytest.mark.asyncio
async def test_simple_text_streaming() -> None:
    """N-2: use_mcpp=False, 빈 chunk 포함 스트림 → TextChunk 3개 + EndOfTurn."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        for chunk in ["안", "녕", "하세요", ""]:
            yield chunk

    agent._simple_stream = mock_stream

    events = await collect_events(agent, make_text_batch("안녕"))

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    eot = [e for e in events if isinstance(e, EndOfTurn)]

    assert len(text_chunks) == 3
    assert text_chunks[0].text == "안"
    assert text_chunks[1].text == "녕"
    assert text_chunks[2].text == "하세요"
    assert len(eot) == 1
    assert eot[0].assistant_text_total == "안녕하세요"


# E-1: 빈 입력
@pytest.mark.asyncio
async def test_empty_input_no_llm_call() -> None:
    """E-1: texts=[], images=None → AgentError + EndOfTurn, LLM 호출 없음."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    call_count = 0

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        nonlocal call_count
        call_count += 1
        yield "should not reach"

    agent._simple_stream = mock_stream
    batch = BatchInput(texts=[])

    events = await collect_events(agent, batch)

    assert call_count == 0
    assert len(events) == 2
    assert isinstance(events[0], AgentError)
    assert events[0].code == "empty_response"
    assert isinstance(events[1], EndOfTurn)
    assert events[1].assistant_text_total == ""


# E-1 보완: images=[] 도 빈 입력으로 처리
@pytest.mark.asyncio
async def test_empty_input_empty_images_list() -> None:
    """E-1 보완: texts=[], images=[] → AgentError + EndOfTurn (MAJOR-3 수정 검증)."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    call_count = 0

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        nonlocal call_count
        call_count += 1
        yield "should not reach"

    agent._simple_stream = mock_stream
    batch = BatchInput(texts=[], images=[])

    events = await collect_events(agent, batch)

    assert call_count == 0
    assert isinstance(events[0], AgentError)
    assert events[0].code == "empty_response"


# E-4: 빈 chunk 드롭
@pytest.mark.asyncio
async def test_empty_chunks_dropped() -> None:
    """E-4: 첫 토큰이 빈 chunk, 중간에도 빈 chunk → 드롭."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        for chunk in ["", "반", "", "갑", "", "습니다"]:
            yield chunk

    agent._simple_stream = mock_stream

    events = await collect_events(agent, make_text_batch("안녕"))
    text_chunks = [e for e in events if isinstance(e, TextChunk)]

    assert len(text_chunks) == 3
    assert [e.text for e in text_chunks] == ["반", "갑", "습니다"]


# E-8: 지연 응답 (정상 완료)
@pytest.mark.asyncio
async def test_delayed_response_completes_normally() -> None:
    """E-8: 첫 응답이 지연 후 정상 완료."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        await asyncio.sleep(0.05)  # 짧은 지연
        yield "지"
        yield "금"

    agent._simple_stream = mock_stream

    events = await collect_events(agent, make_text_batch("지금"))
    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    eot = [e for e in events if isinstance(e, EndOfTurn)]

    assert len(text_chunks) == 2
    assert len(eot) == 1
    assert eot[0].assistant_text_total == "지금"


# A-7: backend 5xx 에러 문자열
@pytest.mark.asyncio
async def test_backend_error_string_converted_to_event() -> None:
    """A-7: upstream이 'Error calling the chat endpoint: ...' yield → AgentError(backend_unreachable) + EndOfTurn."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "Error calling the chat endpoint: Connection error."

    agent._simple_stream = mock_stream

    events = await collect_events(agent, make_text_batch("안녕"))

    error_events = [e for e in events if isinstance(e, AgentError)]
    eot_events = [e for e in events if isinstance(e, EndOfTurn)]

    assert len(error_events) == 1
    assert error_events[0].code == "backend_unreachable"
    assert len(eot_events) == 1
    assert eot_events[0].assistant_text_total == ""


# A-8: 매우 긴 입력
@pytest.mark.asyncio
async def test_very_long_input_no_truncation() -> None:
    """A-8: 10만자 텍스트 → 자체 트리밍 없이 LLM 호출."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    long_text = "가" * 100_000
    called_messages: list[Any] = []

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        if args:
            called_messages.extend(args[0])
        yield "응답"

    agent._simple_stream = mock_stream

    batch = BatchInput(texts=[TextData(source=TextSource.INPUT, content=long_text)])
    events = await collect_events(agent, batch)

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    assert len(text_chunks) >= 1
    # upstream에 전달된 메시지에 long_text가 포함됨
    assert any(long_text in str(m) for m in called_messages)

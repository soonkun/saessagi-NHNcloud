# tests/agent/test_adapter.py
"""BasicMemoryAgentAdapter 동작 테스트."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

from src.agent.events import (
    EndOfTurn,
)
from src.agent.upstream_adapter import BasicMemoryAgentAdapter

from .conftest import _build_agent_async


def make_text_batch(text: str = "안녕") -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


@pytest.mark.asyncio
async def test_adapter_text_chunk_flattened() -> None:
    """TextChunk → str yield."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "안녕하세요"

    agent._simple_stream = mock_stream

    adapter = BasicMemoryAgentAdapter(agent)
    results = []
    async for item in adapter.chat(make_text_batch("안녕")):
        results.append(item)

    assert any("안녕하세요" in str(r) for r in results)


@pytest.mark.asyncio
async def test_adapter_agent_error_as_text() -> None:
    """AgentError → '[오류: ...]' 텍스트 yield."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "Error calling the chat endpoint: down"

    agent._simple_stream = mock_stream

    adapter = BasicMemoryAgentAdapter(agent)
    results = []
    async for item in adapter.chat(make_text_batch("안녕")):
        results.append(item)

    # 에러 텍스트가 포함되어 있어야 함
    assert any("[오류:" in str(r) for r in results)


@pytest.mark.asyncio
async def test_adapter_end_of_turn_not_yielded() -> None:
    """EndOfTurn → 어댑터에서 yield하지 않음 (스트림 종료)."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "응답"

    agent._llm.chat_completion = mock_stream

    adapter = BasicMemoryAgentAdapter(agent)
    results = []
    async for item in adapter.chat(make_text_batch("안녕")):
        results.append(item)

    # EndOfTurn 이벤트가 스트림에 없음
    assert not any(isinstance(r, EndOfTurn) for r in results)


@pytest.mark.asyncio
async def test_adapter_tool_call_start_as_dict() -> None:
    """ToolCallStart → dict yield."""
    from .fakes import make_fake_tool_executor, make_fake_tool_manager

    tm = make_fake_tool_manager()
    te = make_fake_tool_executor()
    agent = await _build_agent_async(tool_manager=tm, tool_executor=te, use_mcpp=True)

    async def mock_tool_loop(messages: Any, tools: Any) -> AsyncIterator[Any]:
        yield {
            "type": "tool_call_status",
            "tool_id": "t1",
            "tool_name": "add_event",
            "status": "running",
            "content": "Input: {}",
        }
        yield "완료"

    agent._inner._openai_tool_interaction_loop = mock_tool_loop

    adapter = BasicMemoryAgentAdapter(agent)
    results = []
    async for item in adapter.chat(make_text_batch("이벤트 추가")):
        results.append(item)

    dict_items = [r for r in results if isinstance(r, dict)]
    assert any(r.get("type") == "tool_call_start" for r in dict_items)


@pytest.mark.asyncio
async def test_adapter_handle_interrupt_schedules_task() -> None:
    """adapter.handle_interrupt → asyncio 이벤트 루프에서 태스크 스케줄."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    adapter = BasicMemoryAgentAdapter(agent)

    # 이벤트 루프가 실행 중인 상태에서 호출 → 태스크 스케줄
    adapter.handle_interrupt("인터럽트 테스트")

    # 스케줄된 태스크가 실행되도록 yield
    import asyncio

    await asyncio.sleep(0)

    # upstream _interrupt_handled가 True로 설정됨
    assert agent._inner._interrupt_handled is True


def test_adapter_set_memory_from_history() -> None:
    """adapter.set_memory_from_history → upstream 직접 위임."""
    import asyncio
    from unittest.mock import patch

    async def _async_test() -> None:
        agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
        adapter = BasicMemoryAgentAdapter(agent)

        with patch(
            "open_llm_vtuber.agent.agents.basic_memory_agent.get_history",
            return_value=[{"role": "human", "content": "안녕"}],
        ):
            adapter.set_memory_from_history("conf", "hist")

        assert len(agent._inner._memory) == 1

    asyncio.run(_async_test())


@pytest.mark.asyncio
async def test_adapter_close_delegates_to_agent_aclose() -> None:
    """CR-03: adapter.close() → 내부 _agent.aclose()가 1회 await됨."""
    from unittest.mock import AsyncMock, MagicMock

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

    adapter = BasicMemoryAgentAdapter(mock_agent)
    await adapter.close()

    mock_agent.aclose.assert_awaited_once()

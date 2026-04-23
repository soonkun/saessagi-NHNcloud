# tests/agent/test_memory.py
"""히스토리·연속 턴 테스트 (N-6, N-7)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource


from .conftest import _build_agent_async


def make_text_batch(text: str) -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


# N-6: 히스토리에서 메모리 복원
@pytest.mark.asyncio
async def test_set_memory_from_history() -> None:
    """N-6: upstream get_history mock이 10개 메시지 반환 → _memory 길이 10."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    fake_history = [
        {"role": "human", "content": f"user message {i}"}
        if i % 2 == 0
        else {"role": "ai", "content": f"ai response {i}"}
        for i in range(10)
    ]

    with patch(
        "open_llm_vtuber.agent.agents.basic_memory_agent.get_history",
        return_value=fake_history,
    ):
        await agent.set_memory_from_history("conf123", "hist456")

    assert len(agent._inner._memory) == 10
    roles = {m["role"] for m in agent._inner._memory}
    assert roles <= {"user", "assistant"}


# N-7: 연속 턴 메모리 누적
@pytest.mark.asyncio
async def test_consecutive_turns_memory_accumulation() -> None:
    """N-7: 2턴 대화 → 2번째 턴에서 이전 턴 포함 메시지 전달."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    captured_messages_per_turn: list[list[Any]] = []

    async def mock_stream(messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        captured_messages_per_turn.append(list(messages))
        yield "알겠습니다." if len(captured_messages_per_turn) == 1 else "새싹이입니다."

    agent._simple_stream = mock_stream

    # 턴 1
    async for _ in agent.chat(make_text_batch("내 이름은 새싹이야")):
        pass

    # 턴 2
    async for _ in agent.chat(make_text_batch("내 이름 뭐였지?")):
        pass

    # 턴2의 메시지에는 턴1의 user+assistant 포함
    assert len(captured_messages_per_turn) == 2
    turn2_messages = captured_messages_per_turn[1]
    contents = [str(m.get("content", "")) for m in turn2_messages]
    combined = " ".join(contents)
    assert "새싹이" in combined or any("새싹이야" in c for c in contents)

    # _memory 길이 4 (턴1 user+assistant, 턴2 user+assistant)
    assert len(agent._inner._memory) == 4


# 히스토리 uid 미존재 → 빈 메모리
@pytest.mark.asyncio
async def test_set_memory_from_nonexistent_history() -> None:
    """history_uid 미존재 → upstream이 빈 리스트 반환 → memory 빈 상태."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    # 먼저 메모리에 일부 데이터 추가
    agent._inner._memory.append({"role": "user", "content": "기존 메시지"})

    with patch(
        "open_llm_vtuber.agent.agents.basic_memory_agent.get_history",
        return_value=[],
    ):
        await agent.set_memory_from_history("conf123", "nonexistent_uid")

    assert len(agent._inner._memory) == 0


# set_system_prompt 런타임 교체
@pytest.mark.asyncio
async def test_set_system_prompt() -> None:
    """set_system_prompt → upstream _inner._system 업데이트."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    new_prompt = "새로운 페르소나 프롬프트"
    agent.set_system_prompt(new_prompt)

    assert agent.system_prompt == new_prompt
    # upstream BasicMemoryAgent.set_system은 약간 다른 문자열로 저장할 수 있음 (interrupt_method 추가)
    assert new_prompt in agent._inner._system

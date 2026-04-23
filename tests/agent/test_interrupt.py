# tests/agent/test_interrupt.py
"""인터럽트 테스트 (N-5, E-5, E-6)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

from src.agent.events import EndOfTurn, TextChunk

from .conftest import _build_agent_async


def make_text_batch(text: str = "안녕") -> BatchInput:
    return BatchInput(texts=[TextData(source=TextSource.INPUT, content=text)])


# N-5: 인터럽트 처리
@pytest.mark.asyncio
async def test_handle_interrupt_updates_memory() -> None:
    """N-5: handle_interrupt 호출 시 upstream _memory 업데이트."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    # 먼저 assistant 메시지를 메모리에 추가
    agent._inner._memory.append({"role": "assistant", "content": "안녕하세요"})

    await agent.handle_interrupt("제가 들은 건 여기까지예요")

    # upstream handle_interrupt 동작 확인
    # 마지막 assistant 메시지가 heard_text + "..."로 업데이트
    assert agent._inner._memory[-1]["role"] in ("user", "system")
    assert "[Interrupted by user]" in agent._inner._memory[-1]["content"]


@pytest.mark.asyncio
async def test_handle_interrupt_no_lock() -> None:
    """handle_interrupt는 _chat_lock을 획득하지 않아야 함 (데드락 방지)."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    # _chat_lock을 획득한 상태에서 handle_interrupt 호출
    async def call_while_locked() -> None:
        async with agent._chat_lock:
            # 락이 걸린 상태에서도 handle_interrupt가 완료되어야 함
            await agent.handle_interrupt("인터럽트")

    # 타임아웃 내에 완료되면 성공
    await asyncio.wait_for(call_while_locked(), timeout=1.0)


# E-5: interrupt_method="system" 설정
@pytest.mark.asyncio
async def test_interrupt_method_system() -> None:
    """E-5: interrupt_method="system" 설정 시 system role로 추가."""
    agent = await _build_agent_async(
        use_mcpp=False,
        tool_manager=None,
        tool_executor=None,
        interrupt_method="system",
    )

    agent._inner._memory.append({"role": "assistant", "content": "응답 중"})
    await agent.handle_interrupt("안")

    # system role로 [Interrupted by user] 추가
    assert any(
        m.get("role") == "system" and "[Interrupted by user]" in m.get("content", "")
        for m in agent._inner._memory
    )


# E-6: 동시 chat 호출 직렬화
@pytest.mark.asyncio
async def test_concurrent_chat_serialized() -> None:
    """E-6: 두 chat() 태스크 → 직렬로 실행. 첫 번째 EndOfTurn 후 두 번째 시작."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    order: list[str] = []

    call_count = 0

    async def mock_stream(messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            await asyncio.sleep(0.05)
            order.append("task1_yield")
            yield "응답1"
        else:
            order.append("task2_yield")
            yield "응답2"

    agent._simple_stream = mock_stream

    batch = make_text_batch("안녕")
    events1: list[Any] = []
    events2: list[Any] = []

    async def chat1() -> None:
        async for ev in agent.chat(batch):
            events1.append(ev)

    async def chat2() -> None:
        async for ev in agent.chat(batch):
            events2.append(ev)

    # 동시에 시작
    await asyncio.gather(chat1(), chat2())

    # 두 응답 모두 완료되어야 함
    eot1 = [e for e in events1 if isinstance(e, EndOfTurn)]
    eot2 = [e for e in events2 if isinstance(e, EndOfTurn)]
    assert len(eot1) == 1
    assert len(eot2) == 1

    # 순서: task1이 먼저, task2가 나중
    assert order.index("task1_yield") < order.index("task2_yield")


# 중복 인터럽트 → no-op
@pytest.mark.asyncio
async def test_duplicate_interrupt_is_noop() -> None:
    """중복 handle_interrupt 호출 → 두 번째는 no-op."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)
    agent._inner._memory.append({"role": "assistant", "content": "응답"})

    await agent.handle_interrupt("첫 번째 인터럽트")
    first_memory_len = len(agent._inner._memory)

    await agent.handle_interrupt("두 번째 인터럽트 — 무시되어야 함")
    second_memory_len = len(agent._inner._memory)

    # 두 번째 호출은 메모리 변경 없음
    assert first_memory_len == second_memory_len


# BLOCKER-5: handle_interrupt가 chat() 태스크를 실제로 취소하는 통합 테스트
@pytest.mark.asyncio
async def test_handle_interrupt_cancels_chat_task() -> None:
    """N-5 통합: chat() 실행 중 handle_interrupt → 외부 task.cancel() → CancelledError 전파.

    upstream _memory에 부분 텍스트와 [Interrupted by user] 기록 확인.
    """
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    first_chunk_received = asyncio.Event()

    async def slow_stream(messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "부분"
        first_chunk_received.set()
        # 취소를 기다리는 긴 sleep
        await asyncio.sleep(10.0)
        yield "완료"  # 이 토큰은 도달하지 않아야 함

    agent._simple_stream = slow_stream

    batch = make_text_batch("길게 응답해줘")
    received_events: list[Any] = []

    async def run_chat() -> None:
        async for ev in agent.chat(batch):
            received_events.append(ev)

    chat_task = asyncio.create_task(run_chat())

    # 첫 TextChunk가 방출될 때까지 대기
    await asyncio.wait_for(first_chunk_received.wait(), timeout=2.0)

    # interrupt 호출 후 태스크 취소
    await agent.handle_interrupt("여기까지만 들었어요")
    chat_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await chat_task

    # 부분 텍스트가 TextChunk로 방출됐는지 확인
    text_chunks = [ev for ev in received_events if isinstance(ev, TextChunk)]
    assert any(ev.text == "부분" for ev in text_chunks)

    # upstream _memory에 [Interrupted by user] 기록 확인
    assert any("[Interrupted by user]" in m.get("content", "") for m in agent._inner._memory)

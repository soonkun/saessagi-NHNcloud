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


# ── M_17 확장: prompt_provider + answer_guide 주입 ────────────────────────────


@pytest.mark.asyncio
async def test_M17_N6_answer_guide_prepended_after_tool_hint() -> None:
    """M_17 N-6: answer_guide 있을 때 INPUT에 '[작성 지침] ...' prepend (tool_hint 다음, RAG 앞)."""
    from unittest.mock import AsyncMock, MagicMock

    from open_llm_vtuber.agent.input_types import TextSource

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

    # chat()이 소비될 수 있도록 stream 설정
    async def empty_stream(input_data: Any) -> Any:
        from src.agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = empty_stream

    # intent_classifier mock — doc_query 반환
    from intent_gate.types import IntentResult

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

    # prompt_provider: doc_query_answer 지침 반환
    def prompt_provider() -> dict[str, str]:
        return {"doc_query_answer": "표로 정리"}

    adapter = BasicMemoryAgentAdapter(
        mock_agent,
        rag_service=None,
        intent_classifier=mock_classifier,
        prompt_provider=prompt_provider,
    )

    captured_input: list[Any] = []

    async def capture_chat(input_data: Any) -> Any:
        captured_input.append(input_data)
        from src.agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = capture_chat

    async for _ in adapter.chat(make_text_batch("자료 알려줘")):
        pass

    assert len(captured_input) == 1
    texts = captured_input[0].texts or []
    contents = [t.content for t in texts if t.source == TextSource.INPUT]
    # "[작성 지침] 표로 정리"가 prepend되어야 함
    assert any("[작성 지침] 표로 정리" in c for c in contents)


@pytest.mark.asyncio
async def test_M17_E2_no_prompt_provider_no_answer_guide() -> None:
    """M_17 E-2: prompt_provider=None → M_16 동작과 동일 (answer_guide 없음)."""
    from unittest.mock import AsyncMock, MagicMock

    from intent_gate.types import IntentResult

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

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

    # prompt_provider=None
    adapter = BasicMemoryAgentAdapter(
        mock_agent,
        rag_service=None,
        intent_classifier=mock_classifier,
        prompt_provider=None,
    )

    captured_input: list[Any] = []

    async def capture_chat(input_data: Any) -> Any:
        captured_input.append(input_data)
        from src.agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = capture_chat

    async for _ in adapter.chat(make_text_batch("자료 알려줘")):
        pass

    # "[작성 지침]"이 없어야 함
    if captured_input:
        texts = captured_input[0].texts or []
        from open_llm_vtuber.agent.input_types import TextSource

        contents = [t.content for t in texts if t.source == TextSource.INPUT]
        assert not any("[작성 지침]" in c for c in contents)


@pytest.mark.asyncio
async def test_M17_MAJOR3_answer_guide_order_with_rag_context() -> None:
    """Critic MAJOR-3: tool_hint·answer_guide·RAG 컨텍스트 모두 존재 시 순서 검증.

    순서 계약: tool_hint → answer_guide → RAG 컨텍스트 → 원본 메시지
    rag_service mock을 실제로 연결해 RAG-present 경로를 실행한다.
    """
    from unittest.mock import AsyncMock, MagicMock

    from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

    from intent_gate.types import IntentResult

    # doc_query 의도 — tool_hint + answer_guide + RAG 모두 주입
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

    # RAG 서비스 mock — retrieve 결과 반환
    mock_hit = MagicMock()
    mock_hit.doc_id = "doc-001"
    mock_hit.doc_name = "테스트문서.pdf"
    mock_hit.page = 1
    mock_hit.text = "테스트 RAG 본문"
    mock_hit.score = 0.9
    mock_hit.category = "docs"

    mock_retrieval = MagicMock()
    mock_retrieval.found = True
    mock_retrieval.hits = [mock_hit]

    mock_rag = MagicMock()
    mock_rag.retrieve = MagicMock(return_value=mock_retrieval)
    # VectorStore.get_chunks_by_doc_id는 _store 속성으로 접근
    mock_rag._store = None

    def prompt_provider() -> dict[str, str]:
        return {"doc_query_answer": "결론부터 말해줘"}

    mock_agent = MagicMock()
    mock_agent.aclose = AsyncMock()

    captured_input: list[Any] = []

    async def capture_chat(input_data: Any) -> Any:
        captured_input.append(input_data)
        from agent.events import EndOfTurn

        yield EndOfTurn()

    mock_agent.chat = capture_chat

    adapter = BasicMemoryAgentAdapter(
        mock_agent,
        rag_service=mock_rag,
        intent_classifier=mock_classifier,
        prompt_provider=prompt_provider,
    )

    batch = BatchInput(texts=[TextData(source=TextSource.INPUT, content="방법이 뭐야?")])
    async for _ in adapter.chat(batch):
        pass

    assert len(captured_input) == 1
    texts = captured_input[0].texts or []
    input_texts = [t for t in texts if t.source == TextSource.INPUT]

    # from_name 속성으로 각 요소를 구분 (content로 구분하면 오탐 가능)
    # _HINT_DOC_QUERY 자체에 "[관련 문서 검색 결과]" 문자열이 포함되어 있으므로
    # RAG 컨텍스트는 from_name="문서검색"으로 식별해야 정확하다.
    from_names = [getattr(t, "from_name", "") for t in input_texts]
    contents = [t.content for t in input_texts]

    tool_hint_idx = next((i for i, n in enumerate(from_names) if n == "의도게이트"), None)
    guide_idx = next((i for i, n in enumerate(from_names) if n == "작성지침"), None)
    rag_idx = next((i for i, n in enumerate(from_names) if n == "문서검색"), None)
    original_idx = next((i for i, c in enumerate(contents) if "방법이 뭐야?" in c), None)

    # tool_hint가 있어야 함 (doc_query는 _HINT_DOC_QUERY를 가짐)
    assert tool_hint_idx is not None, f"tool_hint(의도게이트) 없음. from_names={from_names}"
    # answer_guide가 있어야 함
    assert guide_idx is not None, f"answer_guide(작성지침) 없음. from_names={from_names}"
    # RAG 컨텍스트가 있어야 함
    assert rag_idx is not None, f"RAG 컨텍스트(문서검색) 없음. from_names={from_names}"
    # 원본 메시지가 있어야 함
    assert original_idx is not None, f"원본 메시지 없음. contents={contents}"

    # 순서 검증: tool_hint < answer_guide < RAG 컨텍스트 < 원본
    assert tool_hint_idx < guide_idx, (
        f"tool_hint({tool_hint_idx})가 answer_guide({guide_idx})보다 뒤에 있음. from_names={from_names}"
    )
    assert guide_idx < rag_idx, (
        f"answer_guide({guide_idx})가 RAG 컨텍스트({rag_idx})보다 뒤에 있음. from_names={from_names}"
    )
    assert rag_idx < original_idx, (
        f"RAG 컨텍스트({rag_idx})가 원본({original_idx})보다 뒤에 있음. from_names={from_names}"
    )

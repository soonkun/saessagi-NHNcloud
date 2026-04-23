# tests/agent/test_complete_json.py
"""GemmaChatAgent.complete_json 메서드 테스트 (CR-MM-A)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


from .conftest import _build_agent_async


@pytest.fixture
def mock_completion_response() -> MagicMock:
    """OpenAI chat.completions.create 응답 mock."""
    choice = MagicMock()
    choice.message.content = json.dumps(
        {
            "title": "테스트 회의 결과",
            "date": "2026.04.23.",
            "summary_items": [],
            "detail_items": [],
            "next_steps": [],
        }
    )

    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_complete_json_normal(mock_completion_response: MagicMock) -> None:
    """N: complete_json 정상 응답 → dict 반환."""
    agent = await _build_agent_async()

    agent._llm.client.chat.completions.create = AsyncMock(return_value=mock_completion_response)

    result = await agent.complete_json(
        system_prompt="당신은 회의록 작성 전문가입니다.",
        user_prompt="다음 녹취록을 정리해 주세요.",
        json_schema={"type": "object"},
    )

    assert isinstance(result, dict)
    assert result["title"] == "테스트 회의 결과"
    assert result["date"] == "2026.04.23."


@pytest.mark.asyncio
async def test_complete_json_non_json_raises_value_error() -> None:
    """N: LLM이 비-JSON 응답 → ValueError 발생."""
    agent = await _build_agent_async()

    non_json_response = MagicMock()
    non_json_response.choices = [MagicMock()]
    non_json_response.choices[0].message.content = "이것은 JSON이 아닙니다. 회의록 정리 결과입니다."

    agent._llm.client.chat.completions.create = AsyncMock(return_value=non_json_response)

    with pytest.raises(ValueError, match="유효한 JSON"):
        await agent.complete_json(
            system_prompt="시스템",
            user_prompt="사용자",
            json_schema={},
        )


@pytest.mark.asyncio
async def test_complete_json_timeout() -> None:
    """N: timeout_seconds 초과 → asyncio.TimeoutError 전파."""
    agent = await _build_agent_async()

    async def slow_response(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(10)  # 10초 대기

    agent._llm.client.chat.completions.create = AsyncMock(side_effect=slow_response)

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await agent.complete_json(
            system_prompt="시스템",
            user_prompt="사용자",
            json_schema={},
            timeout_seconds=0.01,  # 10ms timeout
        )


@pytest.mark.asyncio
async def test_complete_json_custom_params(mock_completion_response: MagicMock) -> None:
    """N: max_tokens, temperature 커스텀 파라미터가 API 호출에 전달된다."""
    agent = await _build_agent_async()
    create_mock = AsyncMock(return_value=mock_completion_response)
    agent._llm.client.chat.completions.create = create_mock

    await agent.complete_json(
        system_prompt="시스템",
        user_prompt="사용자",
        json_schema={},
        max_tokens=2048,
        temperature=0.1,
    )

    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs.get("max_tokens") == 2048
    assert call_kwargs.get("temperature") == 0.1


@pytest.mark.asyncio
async def test_complete_json_uses_json_mode(mock_completion_response: MagicMock) -> None:
    """N: response_format={"type":"json_object"}가 API 호출에 전달된다."""
    agent = await _build_agent_async()
    create_mock = AsyncMock(return_value=mock_completion_response)
    agent._llm.client.chat.completions.create = create_mock

    await agent.complete_json(
        system_prompt="시스템",
        user_prompt="사용자",
        json_schema={},
    )

    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs.get("response_format") == {"type": "json_object"}

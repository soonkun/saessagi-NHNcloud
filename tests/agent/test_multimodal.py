# tests/agent/test_multimodal.py
"""멀티모달 (screenshot) 입력 테스트 (N-4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from open_llm_vtuber.agent.input_types import (
    BatchInput,
    ImageData,
    ImageSource,
    TextData,
    TextSource,
)

from src.agent.events import EndOfTurn, TextChunk

from .conftest import _build_agent_async


@pytest.mark.asyncio
async def test_multimodal_image_in_messages() -> None:
    """N-4: 이미지 포함 BatchInput → upstream _to_messages에 image_url 블록 포함."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    captured_messages: list[Any] = []

    async def mock_stream(messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        captured_messages.extend(messages)
        yield "이 화면은 바탕화면입니다."

    agent._simple_stream = mock_stream

    batch = BatchInput(
        texts=[TextData(source=TextSource.INPUT, content="이 화면 설명해줘")],
        images=[
            ImageData(
                source=ImageSource.SCREEN,
                data="data:image/png;base64,iVBORw0KGgo=",
                mime_type="image/png",
            )
        ],
    )

    events = []
    async for ev in agent.chat(batch):
        events.append(ev)

    # messages에 image_url 블록이 있어야 함
    user_messages = [m for m in captured_messages if m.get("role") == "user"]
    assert len(user_messages) >= 1

    last_user = user_messages[-1]
    content = last_user.get("content", [])
    assert isinstance(content, list)
    image_blocks = [b for b in content if b.get("type") == "image_url"]
    assert len(image_blocks) >= 1
    assert "data:image/png;base64" in image_blocks[0]["image_url"]["url"]

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    eot = [e for e in events if isinstance(e, EndOfTurn)]
    assert len(text_chunks) >= 1
    assert len(eot) == 1


@pytest.mark.asyncio
async def test_multimodal_texts_and_images() -> None:
    """이미지와 텍스트가 함께 있을 때 둘 다 전달됨."""
    agent = await _build_agent_async(use_mcpp=False, tool_manager=None, tool_executor=None)

    async def mock_stream(messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "설명 완료"

    agent._simple_stream = mock_stream

    batch = BatchInput(
        texts=[TextData(source=TextSource.INPUT, content="화면에 뭐가 있어?")],
        images=[
            ImageData(
                source=ImageSource.SCREEN,
                data="data:image/jpeg;base64,/9j/4AAQ=",
                mime_type="image/jpeg",
            )
        ],
    )

    events = []
    async for ev in agent.chat(batch):
        events.append(ev)

    eot = next((e for e in events if isinstance(e, EndOfTurn)), None)
    assert eot is not None
    assert "설명 완료" in eot.assistant_text_total

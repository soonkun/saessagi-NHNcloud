# tests/agent/test_no_think_llm.py
"""NoThinkLLM — think=False 강제 주입 검증.

패치가 세 경로(stream, non-stream, complete_json) 모두에서
실제로 Ollama에 think=False를 보내는지 확인한다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.no_think_llm import NoThinkLLM


# ────────────────────────────────────────────────────────────
# 헬퍼: NoThinkLLM 인스턴스 빌더
# ────────────────────────────────────────────────────────────


def _make_llm() -> tuple[NoThinkLLM, AsyncMock]:
    """NoThinkLLM을 생성하고 내부 create mock을 반환한다."""
    captured: list[dict[str, Any]] = []

    async def _fake_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body, "kwargs": kwargs})
        # 빈 스트림 mock 반환
        mock_resp = MagicMock()
        mock_resp.__aiter__ = MagicMock(return_value=iter([]))
        return mock_resp

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = _fake_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    # _patch_client 가 patched create 를 shadow함 — captured는 패치된 버전에서만 쌓임
    captured_ref = captured
    return llm, captured_ref  # type: ignore[return-value]


# ────────────────────────────────────────────────────────────
# 정상 케이스
# ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_think_false_injected_when_no_extra_body() -> None:
    """extra_body 없이 호출해도 think=False가 주입된다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    # 패치된 create를 직접 호출
    await llm.client.chat.completions.create(messages=[])

    assert len(captured) == 1
    assert captured[0]["extra_body"] == {"think": False, "reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_think_false_forced_even_when_caller_passes_think_true() -> None:
    """caller가 think=True를 명시해도 think=False로 강제된다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    await llm.client.chat.completions.create(messages=[], extra_body={"think": True})

    assert captured[0]["extra_body"]["think"] is False


@pytest.mark.asyncio
async def test_existing_extra_body_fields_preserved() -> None:
    """think 외의 extra_body 필드는 보존된다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    await llm.client.chat.completions.create(
        messages=[], extra_body={"num_ctx": 4096, "think": True}
    )

    eb = captured[0]["extra_body"]
    assert eb["think"] is False
    assert eb["num_ctx"] == 4096


@pytest.mark.asyncio
async def test_original_extra_body_not_mutated() -> None:
    """패치가 caller의 extra_body dict를 제자리 변경하지 않는다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    caller_eb = {"num_ctx": 8192}
    await llm.client.chat.completions.create(messages=[], extra_body=caller_eb)

    # caller의 원본 dict은 변경되지 않아야 함
    assert "think" not in caller_eb
    assert caller_eb["num_ctx"] == 8192


# ────────────────────────────────────────────────────────────
# 엣지 케이스
# ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_none_extra_body_becomes_think_false() -> None:
    """extra_body=None → {"think": False} 로 변환."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    await llm.client.chat.completions.create(messages=[], extra_body=None)
    assert captured[0]["extra_body"] == {"think": False, "reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_patch_applied_only_to_instance_not_class() -> None:
    """패치는 인스턴스 수준 — 다른 인스턴스에 영향 없음."""
    calls_a: list[Any] = []
    calls_b: list[Any] = []

    def _make_recording(calls: list[Any]) -> Any:
        async def recording_create(*args: Any, extra_body: Any = None, **kwargs: Any) -> Any:
            calls.append(extra_body)
            return AsyncMock()

        return recording_create

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client_a = MagicMock()
        mock_client_a.chat.completions.create = _make_recording(calls_a)
        mock_client_b = MagicMock()
        mock_client_b.chat.completions.create = _make_recording(calls_b)
        mock_openai_cls.side_effect = [mock_client_a, mock_client_b]

        llm_a = NoThinkLLM(base_url="http://127.0.0.1:11434/v1", model="gemma4:e4b")
        llm_b = NoThinkLLM(base_url="http://127.0.0.1:11434/v1", model="gemma4:e4b")

    await llm_a.client.chat.completions.create(messages=[])
    await llm_b.client.chat.completions.create(messages=[])

    assert calls_a[0]["think"] is False
    assert calls_b[0]["think"] is False


@pytest.mark.asyncio
async def test_multiple_calls_all_injected() -> None:
    """연속 호출 모두에서 think=False가 주입된다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    for _ in range(5):
        await llm.client.chat.completions.create(messages=[])

    assert len(captured) == 5
    assert all(c["extra_body"]["think"] is False for c in captured)


# ────────────────────────────────────────────────────────────
# 적대적 케이스
# ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_think_false_not_overridden_by_empty_extra_body() -> None:
    """빈 dict extra_body={}도 think=False를 포함해야 한다."""
    captured: list[dict[str, Any]] = []

    async def recording_create(
        *args: Any, extra_body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        captured.append({"extra_body": extra_body})
        return AsyncMock()

    with patch(
        "open_llm_vtuber.agent.stateless_llm.openai_compatible_llm.AsyncOpenAI"
    ) as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = recording_create
        mock_openai_cls.return_value = mock_client

        llm = NoThinkLLM(
            base_url="http://127.0.0.1:11434/v1",
            model="gemma4:e4b",
        )

    await llm.client.chat.completions.create(messages=[], extra_body={})
    assert captured[0]["extra_body"]["think"] is False

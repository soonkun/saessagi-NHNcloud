"""CR-39: 딥 리서치 전용 LLM 선택 테스트.

핵심은 두 가지다:
  - 기본값(same_as_chat)에서 기존 동작이 그대로여야 한다 (회귀 방지)
  - 모델 태그 오타 같은 설정 실수로 딥 리서치 기능 전체가 죽으면 안 된다 (폴백)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import AppConfig, DeepResearchConfig, IntentGateProviderKind
from app.service_context import AppServiceContext


class _FakeAgent:
    """build_chat_agent가 돌려주는 agent 대역."""

    def __init__(self, tag: str = "built") -> None:
        self.tag = tag
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def ctx() -> AppServiceContext:
    c = AppServiceContext.__new__(AppServiceContext)  # __init__의 무거운 조립 회피
    c.app_config = AppConfig()
    c._deep_research_agent = None
    return c


# ────────────────────────────────────────────────────────────
# 설정 기본값
# ────────────────────────────────────────────────────────────


class TestConfig:
    def test_d1_default_is_same_as_chat(self) -> None:
        assert AppConfig().deep_research.provider == IntentGateProviderKind.SAME_AS_CHAT

    def test_d2_keep_alive_default_longer_than_chat(self) -> None:
        """80GB급 모델은 채팅용 300초로는 매번 재로딩된다."""
        cfg = AppConfig()
        assert cfg.deep_research.keep_alive_seconds > cfg.ollama.keep_alive_seconds

    def test_d3_accepts_ollama_model(self) -> None:
        cfg = DeepResearchConfig(
            provider=IntentGateProviderKind.OLLAMA, ollama_model="mistral-medium-3.5:128b"
        )
        assert cfg.ollama_model == "mistral-medium-3.5:128b"


# ────────────────────────────────────────────────────────────
# agent 조립
# ────────────────────────────────────────────────────────────


class TestBuild:
    @pytest.mark.asyncio
    async def test_b1_same_as_chat_reuses_chat_agent(self, ctx: AppServiceContext) -> None:
        """기본값에서는 별도 agent를 만들지 않고 채팅 agent를 그대로 쓴다."""
        chat = _FakeAgent("chat")
        agent, label = await ctx._build_deep_research_agent(chat)

        assert agent is chat
        assert label.startswith("same_as_chat")
        assert ctx._deep_research_agent is None, "cleanup 대상이 생기면 안 된다"

    @pytest.mark.asyncio
    async def test_b2_ollama_builds_dedicated_agent(
        self, ctx: AppServiceContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_build(**kwargs: Any) -> _FakeAgent:
            captured.update(kwargs)
            return _FakeAgent("dedicated")

        import agent.builder as builder_mod

        monkeypatch.setattr(builder_mod, "build_chat_agent", fake_build)

        ctx.app_config = ctx.app_config.model_copy(
            update={
                "deep_research": DeepResearchConfig(
                    provider=IntentGateProviderKind.OLLAMA,
                    ollama_model="mistral-medium-3.5:128b",
                    keep_alive_seconds=1800,
                )
            }
        )
        chat = _FakeAgent("chat")
        agent, label = await ctx._build_deep_research_agent(chat)

        assert agent is not chat
        assert "mistral-medium-3.5:128b" in label
        # 지정한 모델과 keep_alive가 실제로 agent에 전달되어야 한다
        assert captured["ollama_config"].model == "mistral-medium-3.5:128b"
        assert captured["ollama_config"].keep_alive_seconds == 1800
        # 채팅 모델 설정은 오염되지 않아야 한다
        assert ctx.app_config.ollama.model != "mistral-medium-3.5:128b"
        assert ctx._deep_research_agent is agent, "close()에서 정리되도록 보관되어야 한다"

    @pytest.mark.asyncio
    async def test_b3_empty_model_falls_back(self, ctx: AppServiceContext) -> None:
        """provider만 바꾸고 모델명을 빼먹은 설정 실수."""
        ctx.app_config = ctx.app_config.model_copy(
            update={
                "deep_research": DeepResearchConfig(
                    provider=IntentGateProviderKind.OLLAMA, ollama_model=""
                )
            }
        )
        chat = _FakeAgent("chat")
        agent, label = await ctx._build_deep_research_agent(chat)

        assert agent is chat, "설정 실수로 기능이 죽으면 안 된다"
        assert "fallback" in label

    @pytest.mark.asyncio
    async def test_b4_build_failure_falls_back(
        self, ctx: AppServiceContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(**_: Any) -> None:
            raise RuntimeError("ollama 연결 실패")

        import agent.builder as builder_mod

        monkeypatch.setattr(builder_mod, "build_chat_agent", boom)

        ctx.app_config = ctx.app_config.model_copy(
            update={
                "deep_research": DeepResearchConfig(
                    provider=IntentGateProviderKind.OLLAMA, ollama_model="nonexistent:999b"
                )
            }
        )
        chat = _FakeAgent("chat")
        agent, label = await ctx._build_deep_research_agent(chat)

        assert agent is chat
        assert "fallback" in label

    @pytest.mark.asyncio
    async def test_b5_openai_provider(
        self, ctx: AppServiceContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_build(**kwargs: Any) -> _FakeAgent:
            captured.update(kwargs)
            return _FakeAgent("openai")

        import agent.builder as builder_mod

        monkeypatch.setattr(builder_mod, "build_chat_agent", fake_build)

        ctx.app_config = ctx.app_config.model_copy(
            update={
                "deep_research": DeepResearchConfig(
                    provider=IntentGateProviderKind.OPENAI, openai_model="gpt-4o"
                )
            }
        )
        agent, label = await ctx._build_deep_research_agent(_FakeAgent("chat"))

        assert "openai(gpt-4o)" == label
        assert captured["app_config"].openai.model == "gpt-4o"

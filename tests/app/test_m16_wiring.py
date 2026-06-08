# tests/app/test_m16_wiring.py
"""M_16 IntentGate service_context 배선 + settings 엔드포인트 테스트 (MAJOR #3).

- service_context: same_as_chat / ollama / openai 세 분기의 모델 경로 검증.
  openai 분기가 intent_cfg.openai_model을 실제로 사용하는지 (MAJOR #1 수정 회귀 방지).
  enabled=False면 intent_classifier=None인지 검증.
- GET/POST /api/settings/intent-gate:
  GET이 enum이 아닌 문자열 값을 반환 (E-26 회귀 방지).
  POST 후 in-memory 반영.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── service_context 배선 검증 헬퍼 ────────────────────────────────────────────


def _make_ctx_raw() -> Any:
    """upstream ServiceContext.__init__을 mock해 AppServiceContext 인스턴스 생성."""
    from app.service_context import AppServiceContext

    with patch(
        "open_llm_vtuber.service_context.ServiceContext.__init__",
        return_value=None,
    ):
        ctx = AppServiceContext.__new__(AppServiceContext)
        AppServiceContext.__init__(ctx)
    return ctx


def _make_ctx_for_init_agent() -> Any:
    ctx = _make_ctx_raw()
    mock_char_cfg = MagicMock()
    mock_char_cfg.agent_config = MagicMock()
    mock_char_cfg.persona_prompt = "페르소나"
    ctx.character_config = mock_char_cfg
    mock_sys_cfg = MagicMock()
    mock_sys_cfg.tool_prompts = {}
    ctx.system_config = mock_sys_cfg
    ctx.live2d_model = MagicMock()
    ctx.live2d_model.emo_str = ""
    ctx.agent_engine = None
    ctx.tool_manager = None
    ctx.tool_executor = None
    ctx.tool_router = None
    ctx.tool_router_adapter = None
    ctx.system_prompt = None
    return ctx


def _make_agent_mocks() -> tuple[AsyncMock, MagicMock]:
    """mock_build, mock_bma_cls 반환."""
    mock_gemma = MagicMock(name="GemmaChatAgent")
    mock_gemma.complete_json = AsyncMock()
    mock_build = AsyncMock(return_value=mock_gemma)
    mock_bma_cls = MagicMock(return_value=MagicMock())
    return mock_build, mock_bma_cls


def _make_agent_sys_modules(mock_build: AsyncMock, mock_bma_cls: MagicMock) -> dict[str, Any]:
    mock_builder = MagicMock()
    mock_builder.build_chat_agent = mock_build

    mock_upstream_adapter = MagicMock()
    mock_upstream_adapter.BasicMemoryAgentAdapter = mock_bma_cls

    mock_errors = sys.modules.get("agent.errors", MagicMock())

    return {
        "agent.builder": mock_builder,
        "agent.upstream_adapter": mock_upstream_adapter,
        "agent.errors": mock_errors,
    }


# ── service_context 배선 테스트 ───────────────────────────────────────────────


class TestM16ServiceContextWiring:
    """IntentGate service_context 배선 검증."""

    @pytest.mark.asyncio
    async def test_enabled_false_intent_classifier_is_none(self) -> None:
        """enabled=False → intent_classifier=None."""
        from app.config import AppConfig, IntentGateConfig

        ctx = _make_ctx_for_init_agent()
        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(enabled=False)
        )
        ctx.app_config = app_cfg

        mock_build, mock_bma_cls = _make_agent_mocks()
        agent_mods = _make_agent_sys_modules(mock_build, mock_bma_cls)

        # IntentClassifier는 import되지 않아야 하지만, mock으로 대비
        mock_intent_gate = MagicMock()
        mock_intent_gate.IntentClassifier = MagicMock()

        with (
            patch(
                "app.service_context.AppServiceContext.construct_system_prompt",
                new=AsyncMock(return_value="sys"),
            ),
            patch.dict(
                sys.modules,
                {**agent_mods, "intent_gate": mock_intent_gate},
            ),
        ):
            await ctx.init_agent(MagicMock(), "페르소나")

        assert ctx.intent_classifier is None

    @pytest.mark.asyncio
    async def test_same_as_chat_uses_main_agent_complete_json(self) -> None:
        """provider=same_as_chat → main gemma_agent.complete_json을 IntentClassifier에 주입."""
        from app.config import AppConfig, IntentGateConfig, IntentGateProviderKind

        ctx = _make_ctx_for_init_agent()
        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.SAME_AS_CHAT,
            )
        )
        ctx.app_config = app_cfg

        mock_build, mock_bma_cls = _make_agent_mocks()
        agent_mods = _make_agent_sys_modules(mock_build, mock_bma_cls)

        captured_complete_json: list[Any] = []

        def fake_classifier_init(complete_json: Any, **kwargs: Any) -> MagicMock:
            captured_complete_json.append(complete_json)
            return MagicMock()

        mock_intent_gate = MagicMock()
        mock_intent_gate.IntentClassifier = MagicMock(side_effect=fake_classifier_init)

        with (
            patch(
                "app.service_context.AppServiceContext.construct_system_prompt",
                new=AsyncMock(return_value="sys"),
            ),
            patch.dict(
                sys.modules,
                {**agent_mods, "intent_gate": mock_intent_gate},
            ),
        ):
            await ctx.init_agent(MagicMock(), "페르소나")

        assert len(captured_complete_json) == 1
        # mock_build가 반환한 gemma_agent의 complete_json이 주입되어야 함
        gemma_agent = mock_build.return_value
        assert captured_complete_json[0] is gemma_agent.complete_json

    @pytest.mark.asyncio
    async def test_ollama_provider_uses_ollama_model(self) -> None:
        """provider=ollama → build_chat_agent가 intent_cfg.ollama_model로 호출됨."""
        from app.config import AppConfig, IntentGateConfig, IntentGateProviderKind

        ctx = _make_ctx_for_init_agent()
        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.OLLAMA,
                ollama_model="gemma4:e2b-custom",
            )
        )
        ctx.app_config = app_cfg

        # build_chat_agent가 두 번 호출됨(메인 + 분류기)
        call_ollama_configs: list[Any] = []

        mock_gemma = MagicMock(name="GemmaChatAgent")
        mock_gemma.complete_json = AsyncMock()

        async def fake_build(**kwargs: Any) -> MagicMock:
            oc = kwargs.get("ollama_config")
            if oc is not None:
                call_ollama_configs.append(oc)
            return mock_gemma

        mock_bma_cls = MagicMock(return_value=MagicMock())
        mock_builder = MagicMock()
        mock_builder.build_chat_agent = fake_build

        mock_upstream_adapter = MagicMock()
        mock_upstream_adapter.BasicMemoryAgentAdapter = mock_bma_cls

        agent_mods = {
            "agent.builder": mock_builder,
            "agent.upstream_adapter": mock_upstream_adapter,
            "agent.errors": sys.modules.get("agent.errors", MagicMock()),
        }

        mock_intent_gate = MagicMock()
        mock_intent_gate.IntentClassifier = MagicMock(return_value=MagicMock())

        with (
            patch(
                "app.service_context.AppServiceContext.construct_system_prompt",
                new=AsyncMock(return_value="sys"),
            ),
            patch.dict(
                sys.modules,
                {**agent_mods, "intent_gate": mock_intent_gate},
            ),
        ):
            await ctx.init_agent(MagicMock(), "페르소나")

        # 두 번 빌드 — 마지막 것이 분류기용 (ollama_model="gemma4:e2b-custom")
        assert len(call_ollama_configs) == 2
        classifier_ollama_cfg = call_ollama_configs[-1]
        assert classifier_ollama_cfg.model == "gemma4:e2b-custom", (
            f"분류기 ollama 모델이 'gemma4:e2b-custom'이어야 하지만 {classifier_ollama_cfg.model!r}"
        )

    @pytest.mark.asyncio
    async def test_openai_provider_uses_openai_model_from_intent_cfg(self) -> None:
        """provider=openai → build_chat_agent에 intent_cfg.openai_model이 주입됨 (MAJOR #1 회귀 방지).

        openai_app_config.openai.model이 intent_cfg.openai_model("gpt-4o-mini-classify")이어야 한다.
        메인 OpenAI 모델("gpt-4o")을 사용하면 버그 재현.
        """
        from app.config import (
            AppConfig,
            IntentGateConfig,
            IntentGateProviderKind,
            OpenAISubConfig,
        )

        ctx = _make_ctx_for_init_agent()
        app_cfg = AppConfig(  # type: ignore[call-arg]
            llm_provider="openai",  # type: ignore[arg-type]
            openai=OpenAISubConfig(api_key="test-key", model="gpt-4o"),  # 메인 모델
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.OPENAI,
                openai_model="gpt-4o-mini-classify",  # 분류기 전용 모델
            ),
        )
        ctx.app_config = app_cfg

        call_app_configs: list[Any] = []

        mock_gemma = MagicMock(name="GemmaChatAgent")
        mock_gemma.complete_json = AsyncMock()

        async def fake_build(**kwargs: Any) -> MagicMock:
            ac = kwargs.get("app_config")
            if ac is not None:
                call_app_configs.append(ac)
            return mock_gemma

        mock_bma_cls = MagicMock(return_value=MagicMock())
        mock_builder = MagicMock()
        mock_builder.build_chat_agent = fake_build

        mock_upstream_adapter = MagicMock()
        mock_upstream_adapter.BasicMemoryAgentAdapter = mock_bma_cls

        agent_mods = {
            "agent.builder": mock_builder,
            "agent.upstream_adapter": mock_upstream_adapter,
            "agent.errors": sys.modules.get("agent.errors", MagicMock()),
        }

        mock_intent_gate = MagicMock()
        mock_intent_gate.IntentClassifier = MagicMock(return_value=MagicMock())

        with (
            patch(
                "app.service_context.AppServiceContext.construct_system_prompt",
                new=AsyncMock(return_value="sys"),
            ),
            patch.dict(
                sys.modules,
                {**agent_mods, "intent_gate": mock_intent_gate},
            ),
        ):
            await ctx.init_agent(MagicMock(), "페르소나")

        # 두 번 빌드 — 마지막이 분류기용
        assert len(call_app_configs) == 2
        classifier_app_cfg = call_app_configs[-1]
        # openai.model이 intent_cfg.openai_model로 덮어써져야 함 (MAJOR #1 수정)
        assert classifier_app_cfg.openai.model == "gpt-4o-mini-classify", (
            f"분류기 openai.model이 'gpt-4o-mini-classify'여야 하지만 "
            f"{classifier_app_cfg.openai.model!r}. "
            "MAJOR #1 수정(openai 분기 모델 미주입 버그)이 회귀됨."
        )
        # 메인 모델(gpt-4o)을 쓰면 안 됨
        assert classifier_app_cfg.openai.model != "gpt-4o", (
            "분류기가 메인 OpenAI 모델(gpt-4o)을 사용하고 있음 — 버그 미수정"
        )


# ── settings 엔드포인트 테스트 ────────────────────────────────────────────────


class TestIntentGateSettingsEndpoints:
    """GET/POST /api/settings/intent-gate 엔드포인트 테스트 (E-26 회귀 방지 포함)."""

    def _make_app_with_ctx(self, app_cfg: Any) -> Any:
        """FastAPI 앱과 mock service_context를 반환."""
        from fastapi import FastAPI
        from app.settings_routes import router

        app = FastAPI()
        app.include_router(router)  # router가 이미 prefix="/api/settings"를 포함

        ctx_mock = MagicMock()
        ctx_mock.app_config = app_cfg
        ctx_mock.character_config = MagicMock()
        ctx_mock.character_config.agent_config = MagicMock()
        ctx_mock.character_config.persona_prompt = "페르소나"
        ctx_mock.init_agent = AsyncMock()

        app.state.service_context = ctx_mock
        return app, ctx_mock

    def test_get_intent_gate_returns_string_provider_not_enum(self) -> None:
        """GET /api/settings/intent-gate → provider가 enum이 아닌 문자열 값 반환 (E-26 회귀 방지)."""
        from fastapi.testclient import TestClient
        from app.config import AppConfig, IntentGateConfig, IntentGateProviderKind

        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.SAME_AS_CHAT,
            )
        )
        app, _ = self._make_app_with_ctx(app_cfg)

        with TestClient(app) as client:
            resp = client.get("/api/settings/intent-gate")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["provider"], str), (
            f"provider가 문자열이어야 하지만 {type(data['provider']).__name__}: {data['provider']!r}"
        )
        assert data["provider"] == "same_as_chat", (
            f"provider 값이 'same_as_chat'이어야 하지만 {data['provider']!r}"
        )
        # enabled도 bool이어야 함
        assert isinstance(data["enabled"], bool)

    def test_get_intent_gate_ollama_provider_string(self) -> None:
        """GET → provider=ollama 분기도 문자열 'ollama' 반환."""
        from fastapi.testclient import TestClient
        from app.config import AppConfig, IntentGateConfig, IntentGateProviderKind

        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.OLLAMA,
                ollama_model="gemma4:e2b",
            )
        )
        app, _ = self._make_app_with_ctx(app_cfg)

        with TestClient(app) as client:
            resp = client.get("/api/settings/intent-gate")

        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "ollama"
        assert data["ollama_model"] == "gemma4:e2b"

    def test_post_intent_gate_updates_in_memory(self, tmp_path: Path) -> None:
        """POST /api/settings/intent-gate → in-memory app_config.intent_gate 반영."""
        import yaml
        from fastapi.testclient import TestClient
        from app.config import AppConfig, IntentGateConfig, IntentGateProviderKind

        # 임시 conf.yaml 생성
        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump(
                {
                    "app": {
                        "intent_gate": {
                            "enabled": True,
                            "provider": "same_as_chat",
                        }
                    }
                },
                allow_unicode=True,
            )
        )

        app_cfg = AppConfig(  # type: ignore[call-arg]
            intent_gate=IntentGateConfig(
                enabled=True,
                provider=IntentGateProviderKind.SAME_AS_CHAT,
                ollama_model="gemma4:e4b",
            )
        )
        app, ctx_mock = self._make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/intent-gate",
                json={"enabled": False, "ollama_model": "gemma4:e2b-new"},
            )

        assert resp.status_code == 200
        # in-memory 반영
        assert app_cfg.intent_gate.enabled is False
        assert app_cfg.intent_gate.ollama_model == "gemma4:e2b-new"

    def test_post_intent_gate_invalid_provider_422(self, tmp_path: Path) -> None:
        """POST provider='invalid_value' → 422 반환."""
        import yaml
        from fastapi.testclient import TestClient
        from app.config import AppConfig, IntentGateConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(yaml.dump({"app": {"intent_gate": {}}}, allow_unicode=True))

        app_cfg = AppConfig(intent_gate=IntentGateConfig())  # type: ignore[call-arg]
        app, _ = self._make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/intent-gate",
                json={"provider": "invalid_value"},
            )

        assert resp.status_code == 422

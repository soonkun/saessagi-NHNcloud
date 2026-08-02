# tests/app/test_settings_deep_research.py
"""CR-56 딥 리서치 LLM 설정 엔드포인트 테스트.

CR-39에서 `app.deep_research`로 모델을 나눠 놓고도 화면에서는 고를 수 없어
conf.yaml을 직접 고쳐야 했다. GET/POST를 붙이면서 확인하는 것:

- GET이 enum이 아닌 **문자열** provider를 반환한다 (E-26 동형 회귀 방지).
- POST가 conf.yaml + in-memory를 갱신하고 agent를 재초기화한다
  (DeepResearchService는 init_agent 안에서 조립되므로 재초기화 없이는 안 바뀐다).
- provider=ollama인데 모델이 비면 **422로 거절**한다. 그냥 저장하면 조립이 실패해
  조용히 대화 모델로 폴백되어, 사용자는 바꾼 줄 알지만 실제로는 안 바뀐다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import yaml


def _make_app_with_ctx(app_cfg: Any) -> Any:
    from fastapi import FastAPI

    from app.settings_routes import router

    app = FastAPI()
    app.include_router(router)

    ctx_mock = MagicMock()
    ctx_mock.app_config = app_cfg
    ctx_mock.character_config = MagicMock()
    ctx_mock.character_config.agent_config = MagicMock()
    ctx_mock.character_config.persona_prompt = "페르소나"
    ctx_mock.init_agent = AsyncMock()

    app.state.service_context = ctx_mock
    return app, ctx_mock


class TestGetDeepResearch:
    def test_returns_plain_string_provider_and_chat_model(self) -> None:
        from fastapi.testclient import TestClient

        from app.config import AppConfig, DeepResearchConfig, IntentGateProviderKind, OllamaConfig

        app_cfg = AppConfig(  # type: ignore[call-arg]
            ollama=OllamaConfig(model="gemma4:31b"),
            deep_research=DeepResearchConfig(
                provider=IntentGateProviderKind.OLLAMA,
                ollama_model="mistral-medium-3.5:128b",
                keep_alive_seconds=3600,
            ),
        )
        app, _ = _make_app_with_ctx(app_cfg)

        with TestClient(app) as client:
            resp = client.get("/api/settings/deep-research")

        assert resp.status_code == 200
        body = resp.json()
        # "IntentGateProviderKind.OLLAMA"가 아니라 "ollama"여야 프론트 비교가 성립한다.
        assert body["provider"] == "ollama"
        assert body["ollama_model"] == "mistral-medium-3.5:128b"
        assert body["keep_alive_seconds"] == 3600
        # same_as_chat일 때 무엇이 쓰이는지 화면에 보여주기 위한 참고값
        assert body["chat_model"] == "gemma4:31b"

    def test_defaults_when_section_absent(self) -> None:
        from fastapi.testclient import TestClient

        from app.config import AppConfig

        app, _ = _make_app_with_ctx(AppConfig())  # type: ignore[call-arg]

        with TestClient(app) as client:
            resp = client.get("/api/settings/deep-research")

        assert resp.status_code == 200
        assert resp.json()["provider"] == "same_as_chat"


class TestPostDeepResearch:
    def test_updates_conf_memory_and_reinits_agent(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.config import AppConfig, IntentGateProviderKind

        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump({"app": {"deep_research": {"provider": "same_as_chat"}}}, allow_unicode=True),
            encoding="utf-8",
        )

        app_cfg = AppConfig()  # type: ignore[call-arg]
        app, ctx_mock = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/deep-research",
                json={
                    "provider": "ollama",
                    "ollama_model": "gpt-oss:120b",
                    "keep_alive_seconds": 1200,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # in-memory
        assert app_cfg.deep_research.provider == IntentGateProviderKind.OLLAMA
        assert app_cfg.deep_research.ollama_model == "gpt-oss:120b"
        assert app_cfg.deep_research.keep_alive_seconds == 1200

        # conf.yaml
        saved = yaml.safe_load(conf.read_text(encoding="utf-8"))
        assert saved["app"]["deep_research"]["provider"] == "ollama"
        assert saved["app"]["deep_research"]["ollama_model"] == "gpt-oss:120b"
        assert saved["app"]["deep_research"]["keep_alive_seconds"] == 1200

        # DeepResearchService는 init_agent 안에서 조립된다 — 재초기화가 없으면 안 바뀐다.
        ctx_mock.init_agent.assert_awaited_once()

    def test_ollama_without_model_is_rejected(self, tmp_path: Path) -> None:
        """모델 없이 ollama를 고르면 조용한 폴백 대신 422로 막는다."""
        from fastapi.testclient import TestClient

        from app.config import AppConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(yaml.dump({"app": {}}, allow_unicode=True), encoding="utf-8")

        app, ctx_mock = _make_app_with_ctx(AppConfig())  # type: ignore[call-arg]

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/deep-research", json={"provider": "ollama", "ollama_model": "  "}
            )

        assert resp.status_code == 422
        # 거절했으면 conf.yaml도 건드리지 않아야 한다.
        assert yaml.safe_load(conf.read_text(encoding="utf-8")) == {"app": {}}
        ctx_mock.init_agent.assert_not_awaited()

    def test_unknown_provider_is_rejected(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.config import AppConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(yaml.dump({"app": {}}, allow_unicode=True), encoding="utf-8")

        app, _ = _make_app_with_ctx(AppConfig())  # type: ignore[call-arg]

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post("/api/settings/deep-research", json={"provider": "anthropic"})

        assert resp.status_code == 422

    def test_keeps_existing_model_when_only_provider_sent(self, tmp_path: Path) -> None:
        """이미 저장된 모델이 있으면 provider만 바꿔도 통과해야 한다."""
        from fastapi.testclient import TestClient

        from app.config import AppConfig, DeepResearchConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump(
                {"app": {"deep_research": {"ollama_model": "gpt-oss:120b"}}}, allow_unicode=True
            ),
            encoding="utf-8",
        )

        app_cfg = AppConfig(  # type: ignore[call-arg]
            deep_research=DeepResearchConfig(ollama_model="gpt-oss:120b")
        )
        app, _ = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post("/api/settings/deep-research", json={"provider": "ollama"})

        assert resp.status_code == 200
        assert app_cfg.deep_research.ollama_model == "gpt-oss:120b"

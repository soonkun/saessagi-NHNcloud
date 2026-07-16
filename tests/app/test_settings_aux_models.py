# tests/app/test_settings_aux_models.py
"""CR-19 보조 모델 설정 엔드포인트 테스트.

- GET/POST /api/settings/vision-model: 이미지 첨부 턴 전용 비전 모델.
  POST 후 conf.yaml + in-memory 반영, agent 재초기화 호출 검증.
- GET/POST /api/settings/graphrag-extraction: 지식그래프 인덱싱용 추출 LLM.
  GET이 enum이 아닌 문자열 provider를 반환 (E-26 동형 회귀 방지).
  잘못된 provider는 422.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import yaml


def _make_app_with_ctx(app_cfg: Any) -> Any:
    """FastAPI 앱과 mock service_context를 반환 (test_m16_wiring과 동일 패턴)."""
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


class TestVisionModelEndpoints:
    def test_get_vision_model(self) -> None:
        from fastapi.testclient import TestClient
        from app.config import AppConfig, OllamaConfig

        app_cfg = AppConfig(  # type: ignore[call-arg]
            ollama=OllamaConfig(vision_model="qwen2.5vl:7b")
        )
        app, _ = _make_app_with_ctx(app_cfg)

        with TestClient(app) as client:
            resp = client.get("/api/settings/vision-model")

        assert resp.status_code == 200
        assert resp.json() == {"vision_model": "qwen2.5vl:7b"}

    def test_post_vision_model_updates_conf_and_memory(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient
        from app.config import AppConfig, OllamaConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump({"app": {"ollama": {"vision_model": ""}}}, allow_unicode=True),
            encoding="utf-8",
        )

        app_cfg = AppConfig(ollama=OllamaConfig(vision_model=""))  # type: ignore[call-arg]
        app, ctx_mock = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/vision-model", json={"vision_model": "qwen2.5vl:7b"}
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # in-memory 반영
        assert app_cfg.ollama.vision_model == "qwen2.5vl:7b"
        # conf.yaml 반영
        raw = yaml.safe_load(conf.read_text(encoding="utf-8"))
        assert raw["app"]["ollama"]["vision_model"] == "qwen2.5vl:7b"
        # agent 재초기화 호출 (vision_model은 build_chat_agent에서 배선)
        ctx_mock.init_agent.assert_awaited()

    def test_post_vision_model_empty_disables_routing(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient
        from app.config import AppConfig, OllamaConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump(
                {"app": {"ollama": {"vision_model": "qwen2.5vl:7b"}}}, allow_unicode=True
            ),
            encoding="utf-8",
        )

        app_cfg = AppConfig(  # type: ignore[call-arg]
            ollama=OllamaConfig(vision_model="qwen2.5vl:7b")
        )
        app, _ = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post("/api/settings/vision-model", json={"vision_model": ""})

        assert resp.status_code == 200
        assert app_cfg.ollama.vision_model == ""


class TestGraphragExtractionEndpoints:
    def test_get_returns_string_provider_not_enum(self) -> None:
        """provider가 enum repr이 아닌 문자열 값이어야 함 (E-26 동형)."""
        from fastapi.testclient import TestClient
        from app.config import AppConfig, GraphRagConfig, IntentGateProviderKind

        app_cfg = AppConfig(  # type: ignore[call-arg]
            graphrag=GraphRagConfig(
                enabled=True,
                extraction_provider=IntentGateProviderKind.SAME_AS_CHAT,
                extraction_ollama_model="gemma4:e4b",
            )
        )
        app, _ = _make_app_with_ctx(app_cfg)

        with TestClient(app) as client:
            resp = client.get("/api/settings/graphrag-extraction")

        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "same_as_chat"
        assert data["enabled"] is True
        assert data["ollama_model"] == "gemma4:e4b"

    def test_post_updates_conf_and_memory(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient
        from app.config import AppConfig, GraphRagConfig, IntentGateProviderKind

        conf = tmp_path / "conf.yaml"
        conf.write_text(
            yaml.dump(
                {"app": {"graphrag": {"extraction_provider": "same_as_chat"}}},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        app_cfg = AppConfig(graphrag=GraphRagConfig(enabled=True))  # type: ignore[call-arg]
        app, ctx_mock = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/graphrag-extraction",
                json={"provider": "ollama", "ollama_model": "qwen3:14b"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # in-memory 반영 (model_copy로 교체됨)
        assert app_cfg.graphrag.extraction_provider == IntentGateProviderKind.OLLAMA
        assert app_cfg.graphrag.extraction_ollama_model == "qwen3:14b"
        # conf.yaml 반영
        raw = yaml.safe_load(conf.read_text(encoding="utf-8"))
        assert raw["app"]["graphrag"]["extraction_provider"] == "ollama"
        assert raw["app"]["graphrag"]["extraction_ollama_model"] == "qwen3:14b"
        # 추출 LLM은 init_agent에서 조립되므로 재초기화 필요
        ctx_mock.init_agent.assert_awaited()

    def test_post_invalid_provider_422(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient
        from app.config import AppConfig, GraphRagConfig

        conf = tmp_path / "conf.yaml"
        conf.write_text("app: {}\n", encoding="utf-8")

        app_cfg = AppConfig(graphrag=GraphRagConfig(enabled=True))  # type: ignore[call-arg]
        app, _ = _make_app_with_ctx(app_cfg)

        with (
            patch("app.settings_routes._conf_path", return_value=conf),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/api/settings/graphrag-extraction", json={"provider": "bogus"}
            )

        assert resp.status_code == 422

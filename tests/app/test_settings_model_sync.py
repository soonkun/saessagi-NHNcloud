"""E-81 회귀: 대화 모델을 바꿀 때 conf.yaml의 두 곳이 함께 갱신되는가.

한 곳만 바뀌면 어느 모델이 실제로 쓰이는지 알 수 없어 모델 교체 시험이 무의미해진다.
그리고 예전처럼 `model:` 줄을 일괄 치환하면 OpenAI 설정까지 덮어쓴다.
"""

from __future__ import annotations

from typing import Any

from app.settings_routes import _set_ollama_model_keys


def _conf() -> dict[str, Any]:
    return {
        "app": {
            "ollama": {"model": "old-chat:1b", "vision_model": "vision:1b"},
            "openai": {"model": "gpt-4o"},
        },
        "character_config": {
            "agent_config": {
                "llm_configs": {
                    "ollama_llm": {"model": "old-chat:1b", "temperature": 0.7},
                    "openai_llm": {"model": "gpt-4o"},
                }
            }
        },
    }


def test_updates_both_chat_model_keys() -> None:
    raw = _conf()
    _set_ollama_model_keys(raw, "new-chat:70b")
    assert raw["app"]["ollama"]["model"] == "new-chat:70b"
    assert (
        raw["character_config"]["agent_config"]["llm_configs"]["ollama_llm"]["model"]
        == "new-chat:70b"
    )


def test_does_not_touch_openai_or_vision() -> None:
    """일괄 치환 회귀 방지 — 실측상 conf.yaml의 `model:` 줄 4개 중 2개가 OpenAI 것이었다."""
    raw = _conf()
    _set_ollama_model_keys(raw, "new-chat:70b")
    assert raw["app"]["openai"]["model"] == "gpt-4o"
    assert raw["character_config"]["agent_config"]["llm_configs"]["openai_llm"]["model"] == "gpt-4o"
    assert raw["app"]["ollama"]["vision_model"] == "vision:1b"


def test_keeps_sibling_settings() -> None:
    raw = _conf()
    _set_ollama_model_keys(raw, "new-chat:70b")
    assert (
        raw["character_config"]["agent_config"]["llm_configs"]["ollama_llm"]["temperature"] == 0.7
    )


def test_creates_missing_sections() -> None:
    """설정 파일이 최소 형태여도 두 키를 만들어야 한다."""
    raw: dict[str, Any] = {}
    _set_ollama_model_keys(raw, "m:1b")
    assert raw["app"]["ollama"]["model"] == "m:1b"
    assert raw["character_config"]["agent_config"]["llm_configs"]["ollama_llm"]["model"] == "m:1b"

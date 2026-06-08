"""설정 API — Ollama 모델 목록 조회 및 런타임 모델 전환, LLM 공급자 전환."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

_CONF_PATH_ENV = "SAESSAGI_CONF"
_CONF_DEFAULT = "conf.yaml"


def _conf_path() -> Path:
    raw = os.environ.get(_CONF_PATH_ENV, _CONF_DEFAULT)
    p = Path(raw)
    if not p.is_absolute():
        root = os.environ.get("SAESSAGI_ROOT", "")
        if root:
            p = Path(root) / p
    return p


def _ollama_base(ctx: Any) -> str:
    if ctx and ctx.app_config and ctx.app_config.ollama:
        return str(ctx.app_config.ollama.base_url).rstrip("/")
    return "http://127.0.0.1:11434"


# ── GET /api/settings/models ─────────────────────────────────────────────────


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    """Ollama에서 로컬 모델 목록을 가져온다."""
    ctx = getattr(request.app.state, "service_context", None)
    base = _ollama_base(ctx)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama 연결 실패: {exc}") from exc
    models = [m["name"] for m in data.get("models", [])]
    return {"models": models}


# ── GET /api/settings/model ──────────────────────────────────────────────────


@router.get("/model")
async def get_model(request: Request) -> dict[str, str]:
    """현재 사용 중인 Ollama 모델명을 반환한다."""
    ctx = getattr(request.app.state, "service_context", None)
    if ctx and ctx.app_config:
        return {"model": ctx.app_config.ollama.model}
    # fallback: conf.yaml 읽기
    try:
        text = _conf_path().read_text()
        m = re.search(r"^    model:\s*['\"]?([^'\"\n]+)['\"]?", text, re.MULTILINE)
        if m:
            return {"model": m.group(1).strip()}
    except Exception:
        pass
    return {"model": "unknown"}


# ── POST /api/settings/model ─────────────────────────────────────────────────


class SetModelRequest(BaseModel):
    model: str


@router.post("/model")
async def set_model(body: SetModelRequest, request: Request) -> dict[str, str]:
    """Ollama 모델을 전환한다.

    1. conf.yaml에서 model 키 두 곳을 모두 교체한다.
    2. in-memory app_config.ollama.model 갱신.
    3. agent_engine을 None으로 초기화하여 idempotency 가드를 해제한 뒤 init_agent 재호출.
    """
    new_model = body.model.strip()
    if not new_model:
        raise HTTPException(status_code=422, detail="model 이름이 비어 있습니다.")

    # 1. conf.yaml 업데이트
    conf = _conf_path()
    try:
        text = conf.read_text()
        # character_config.agent_config.llm_configs.ollama_llm.model
        text = re.sub(
            r"([ \t]+model:\s*)['\"]?[^'\"\n]+['\"]?",
            lambda m_: m_.group(1) + f'"{new_model}"',
            text,
        )
        conf.write_text(text)
        logger.info(f"conf.yaml model 업데이트 완료: {new_model}")
    except Exception as exc:
        logger.warning(f"conf.yaml 업데이트 실패: {exc}")

    # 2. in-memory 갱신 + agent 재초기화
    ctx = getattr(request.app.state, "service_context", None)
    if ctx is None:
        return {"model": new_model, "status": "conf_only"}

    if ctx.app_config:
        ctx.app_config.ollama.model = new_model

    # idempotency 가드 해제
    ctx.agent_engine = None

    try:
        char_cfg = ctx.character_config
        await ctx.init_agent(char_cfg.agent_config, char_cfg.persona_prompt)
        logger.info(f"agent 재초기화 완료: model={new_model}")
        return {"model": new_model, "status": "ok"}
    except Exception as exc:
        logger.error(f"agent 재초기화 실패: {exc}")
        raise HTTPException(status_code=500, detail=f"agent 재초기화 실패: {exc}") from exc


# ── GET /api/settings/llm-provider ──────────────────────────────────────────


@router.get("/llm-provider")
async def get_llm_provider(request: Request) -> dict[str, Any]:
    """현재 LLM 공급자 설정을 반환한다."""
    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None
    provider = app_cfg.llm_provider if app_cfg else "ollama"
    # LlmProviderKind는 (str, Enum)이라 str(provider)가 "LlmProviderKind.OPENAI"를
    # 반환한다(값 "openai"가 아님). 프론트는 provider==="openai"로 비교하므로
    # 반드시 enum의 .value를 내보내야 한다. (E-26)
    provider_str = getattr(provider, "value", provider)
    openai_key = app_cfg.openai.api_key if app_cfg else ""
    openai_model = app_cfg.openai.model if app_cfg else "gpt-4o-mini"
    ollama_model = app_cfg.ollama.model if app_cfg else "gemma4:e4b"
    return {
        "provider": str(provider_str),
        "openai_api_key_set": bool(openai_key),
        "openai_model": openai_model,
        "ollama_model": ollama_model,
    }


# ── POST /api/settings/llm-provider ─────────────────────────────────────────


class SetLlmProviderRequest(BaseModel):
    provider: str  # "ollama" | "openai"
    openai_api_key: str | None = None
    openai_model: str | None = None
    ollama_model: str | None = None


def _default_temperature_for(model: str) -> float:
    """OpenAI 모델별 안전한 기본 temperature.

    gpt-5 계열은 temperature=1만 허용 (Only the default (1) value is supported).
    o-series 추론 모델(o1, o3, o4)도 동일. 그 외에는 0.7 사용.
    """
    m = (model or "").lower()
    if m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return 1.0
    return 0.7


@router.post("/llm-provider")
async def set_llm_provider(body: SetLlmProviderRequest, request: Request) -> dict[str, Any]:
    """LLM 공급자를 전환하고 agent를 재초기화한다.

    - provider="ollama": ollama_llm으로 전환 (로컬)
    - provider="openai": openai_llm으로 전환 (외부 API)
    conf.yaml을 직접 수정한 뒤 agent 재초기화.
    """
    from .config import LlmProviderKind

    provider = body.provider.strip().lower()
    if provider not in ("ollama", "openai"):
        raise HTTPException(status_code=422, detail="provider는 'ollama' 또는 'openai'여야 합니다.")

    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None

    conf = _conf_path()
    try:
        raw = yaml.safe_load(conf.read_text()) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 읽기 실패: {exc}") from exc

    # ── app 섹션 업데이트 ──
    app_section = raw.setdefault("app", {})
    app_section["llm_provider"] = provider

    if provider == "openai":
        openai_section = app_section.setdefault("openai", {})
        if body.openai_api_key is not None:
            openai_section["api_key"] = body.openai_api_key
        if body.openai_model:
            openai_section["model"] = body.openai_model
        # upstream agent_config 전환
        _set_upstream_llm_provider(raw, "openai_llm")
        # openai_llm conf 삽입/갱신
        api_key = body.openai_api_key or (app_cfg.openai.api_key if app_cfg else "")
        model = body.openai_model or (app_cfg.openai.model if app_cfg else "gpt-4o-mini")
        llm_configs = (
            raw.get("character_config", {}).get("agent_config", {}).setdefault("llm_configs", {})
        )
        llm_configs["openai_llm"] = {
            "base_url": "https://api.openai.com/v1",
            "llm_api_key": api_key,
            "model": model,
            "temperature": _default_temperature_for(model),
            "interrupt_method": "system",
        }
    else:
        if body.ollama_model:
            app_section.setdefault("ollama", {})["model"] = body.ollama_model
        _set_upstream_llm_provider(raw, "ollama_llm")

    try:
        conf.write_text(
            yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
        logger.info(f"conf.yaml LLM 공급자 전환 완료: {provider}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 쓰기 실패: {exc}") from exc

    # ── in-memory 업데이트 ──
    if app_cfg:
        app_cfg.llm_provider = LlmProviderKind(provider)
        if provider == "openai":
            if body.openai_api_key is not None:
                app_cfg.openai.api_key = body.openai_api_key
            if body.openai_model:
                app_cfg.openai.model = body.openai_model
        elif body.ollama_model:
            app_cfg.ollama.model = body.ollama_model

    if ctx is None:
        return {"provider": provider, "status": "conf_only"}

    # ── upstream character_config in-memory 동기화 ──
    try:
        char_cfg = ctx.character_config
        agent_cfg = char_cfg.agent_config
        agent_settings = agent_cfg.agent_settings

        if provider == "openai":
            api_key = body.openai_api_key or (app_cfg.openai.api_key if app_cfg else "")
            model = body.openai_model or (app_cfg.openai.model if app_cfg else "gpt-4o-mini")
            # agent_settings의 llm_provider 전환
            _patch_agent_settings_provider(agent_settings, "openai_llm")
            # llm_configs에 openai_llm 삽입
            from open_llm_vtuber.config_manager.stateless_llm import OpenAIConfig

            agent_cfg.llm_configs.openai_llm = OpenAIConfig(
                base_url="https://api.openai.com/v1",
                llm_api_key=api_key,
                model=model,
                temperature=_default_temperature_for(model),
            )
        else:
            _patch_agent_settings_provider(agent_settings, "ollama_llm")
            if body.ollama_model and agent_cfg.llm_configs.ollama_llm:
                agent_cfg.llm_configs.ollama_llm.model = body.ollama_model
    except Exception as exc:
        logger.warning(f"in-memory agent_config 동기화 실패 (conf.yaml은 저장됨): {exc}")

    ctx.agent_engine = None
    try:
        char_cfg = ctx.character_config
        await ctx.init_agent(char_cfg.agent_config, char_cfg.persona_prompt)
        logger.info(f"LLM 공급자 전환 후 agent 재초기화 완료: {provider}")
        return {"provider": provider, "status": "ok"}
    except Exception as exc:
        logger.error(f"agent 재초기화 실패: {exc}")
        raise HTTPException(status_code=500, detail=f"agent 재초기화 실패: {exc}") from exc


# ── GET /api/settings/meeting-prompt ────────────────────────────────────────


@router.get("/meeting-prompt")
async def get_meeting_prompt(request: Request) -> dict[str, Any]:
    """현재 회의록 생성 시스템 프롬프트를 반환한다."""
    from meeting_minutes.prompts import SYSTEM_PROMPT as DEFAULT_PROMPT

    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None
    custom = (app_cfg.meeting_minutes_prompt if app_cfg else "") or ""
    return {
        "prompt": custom.strip() or DEFAULT_PROMPT,
        "is_custom": bool(custom.strip()),
        "default_prompt": DEFAULT_PROMPT,
    }


# ── POST /api/settings/meeting-prompt ───────────────────────────────────────


class SetMeetingPromptRequest(BaseModel):
    prompt: str  # 빈 문자열이면 기본값으로 초기화


@router.post("/meeting-prompt")
async def set_meeting_prompt(body: SetMeetingPromptRequest, request: Request) -> dict[str, Any]:
    """회의록 생성 시스템 프롬프트를 저장하고 즉시 적용한다.

    빈 문자열 전달 시 기본값(SYSTEM_PROMPT)으로 초기화.
    """
    prompt = body.prompt.strip()

    conf = _conf_path()
    try:
        raw = yaml.safe_load(conf.read_text()) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 읽기 실패: {exc}") from exc

    app_section = raw.setdefault("app", {})
    app_section["meeting_minutes_prompt"] = prompt

    try:
        conf.write_text(
            yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
        logger.info(f"회의록 프롬프트 저장 완료 (길이={len(prompt)}, 커스텀={bool(prompt)})")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 쓰기 실패: {exc}") from exc

    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None
    if app_cfg:
        app_cfg.meeting_minutes_prompt = prompt
    if ctx and ctx.meeting_minutes_service:
        ctx.meeting_minutes_service.set_custom_prompt(prompt)

    return {"status": "ok", "is_custom": bool(prompt)}


# ── GET /api/settings/intent-gate ────────────────────────────────────────────


@router.get("/intent-gate")
async def get_intent_gate(request: Request) -> dict[str, Any]:
    """현재 의도 분류기 설정을 반환한다 (M_16)."""
    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None
    ig = app_cfg.intent_gate if app_cfg else None

    from .config import IntentGateConfig

    default = IntentGateConfig()
    enabled = ig.enabled if ig else default.enabled
    provider = getattr(ig, "provider", default.provider)
    provider_str = getattr(provider, "value", str(provider))
    ollama_model = ig.ollama_model if ig else default.ollama_model
    openai_model = ig.openai_model if ig else default.openai_model
    confidence_threshold = ig.confidence_threshold if ig else default.confidence_threshold
    timeout_seconds = ig.timeout_seconds if ig else default.timeout_seconds

    return {
        "enabled": enabled,
        "provider": provider_str,
        "ollama_model": ollama_model,
        "openai_model": openai_model,
        "confidence_threshold": confidence_threshold,
        "timeout_seconds": timeout_seconds,
    }


# ── POST /api/settings/intent-gate ───────────────────────────────────────────


class SetIntentGateRequest(BaseModel):
    enabled: bool | None = None
    provider: str | None = None  # "ollama" | "openai" | "same_as_chat"
    ollama_model: str | None = None
    openai_model: str | None = None
    confidence_threshold: float | None = None
    timeout_seconds: float | None = None


@router.post("/intent-gate")
async def set_intent_gate(body: SetIntentGateRequest, request: Request) -> dict[str, Any]:
    """의도 분류기 설정을 저장하고 즉시 적용한다 (M_16).

    conf.yaml의 app.intent_gate 섹션을 갱신하고 in-memory app_config 업데이트 후
    agent를 재초기화한다(기존 model 전환 패턴과 동일).
    """
    from .config import IntentGateProviderKind

    ctx = getattr(request.app.state, "service_context", None)
    app_cfg = ctx.app_config if ctx else None

    conf = _conf_path()
    try:
        raw = yaml.safe_load(conf.read_text()) or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 읽기 실패: {exc}") from exc

    app_section = raw.setdefault("app", {})
    ig_section = app_section.setdefault("intent_gate", {})

    if body.enabled is not None:
        ig_section["enabled"] = body.enabled
    if body.provider is not None:
        ig_section["provider"] = body.provider
    if body.ollama_model is not None:
        ig_section["ollama_model"] = body.ollama_model
    if body.openai_model is not None:
        ig_section["openai_model"] = body.openai_model
    if body.confidence_threshold is not None:
        ig_section["confidence_threshold"] = body.confidence_threshold
    if body.timeout_seconds is not None:
        ig_section["timeout_seconds"] = body.timeout_seconds

    try:
        conf.write_text(
            yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
        )
        logger.info("conf.yaml intent_gate 설정 저장 완료")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conf.yaml 쓰기 실패: {exc}") from exc

    # in-memory 업데이트
    if app_cfg:
        current = app_cfg.intent_gate
        updates: dict[str, Any] = {}
        if body.enabled is not None:
            updates["enabled"] = body.enabled
        if body.provider is not None:
            try:
                updates["provider"] = IntentGateProviderKind(body.provider)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"provider는 'ollama', 'openai', 'same_as_chat' 중 하나여야 합니다: {body.provider!r}",
                )
        if body.ollama_model is not None:
            updates["ollama_model"] = body.ollama_model
        if body.openai_model is not None:
            updates["openai_model"] = body.openai_model
        if body.confidence_threshold is not None:
            updates["confidence_threshold"] = body.confidence_threshold
        if body.timeout_seconds is not None:
            updates["timeout_seconds"] = body.timeout_seconds
        app_cfg.intent_gate = current.model_copy(update=updates)

    if ctx is None:
        return {"status": "conf_only"}

    # agent 재초기화 (classifier 재조립 포함)
    ctx.agent_engine = None
    try:
        char_cfg = ctx.character_config
        await ctx.init_agent(char_cfg.agent_config, char_cfg.persona_prompt)
        logger.info("intent_gate 변경 후 agent 재초기화 완료")
        return {"status": "ok"}
    except Exception as exc:
        logger.error(f"intent_gate agent 재초기화 실패: {exc}")
        raise HTTPException(status_code=500, detail=f"agent 재초기화 실패: {exc}") from exc


def _set_upstream_llm_provider(raw: dict[str, Any], provider: str) -> None:
    """conf.yaml dict에서 upstream agent_settings의 llm_provider를 교체."""
    try:
        agent_settings = raw["character_config"]["agent_config"]["agent_settings"]
        for agent_name, agent_val in agent_settings.items():
            if isinstance(agent_val, dict) and "llm_provider" in agent_val:
                agent_val["llm_provider"] = provider
    except (KeyError, TypeError):
        pass


def _patch_agent_settings_provider(agent_settings: Any, provider: str) -> None:
    """upstream in-memory agent_settings 객체의 llm_provider를 전환."""
    try:
        for val in agent_settings.__dict__.values():
            if val is not None and hasattr(val, "llm_provider"):
                val.llm_provider = provider
    except Exception:
        pass

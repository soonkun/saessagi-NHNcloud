"""설정 API — Ollama 모델 목록 조회 및 런타임 모델 전환."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
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
        return ctx.app_config.ollama.base_url.rstrip("/")
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

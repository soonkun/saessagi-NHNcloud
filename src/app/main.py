# src/app/main.py
"""create_app() 팩토리 및 CLI 엔트리 포인트."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from loguru import logger


def create_app(config_path: str = "") -> FastAPI:
    """본 프로젝트 FastAPI 앱을 생성한다.

    1. load_full_config(config_path)
    2. enforce_private_url(config.app.ollama.base_url)
    3. init_logging(config.app.paths.log_dir)
    4. AppServiceContext() 생성, load_from_config(upstream_config),
       load_app_services(app_config)
    5. AppWebSocketServer(config=full_config, default_context_cache=ctx)
    6. app.on_event("startup") → ctx.idle_monitor.start(), ctx.proactive_dispatcher.start()
    7. app.on_event("shutdown") → ctx.close()
    8. return app.app

    Raises:
        PrivacyViolationError: URL 화이트리스트 위반
        FileNotFoundError: config_path 부재
        pydantic.ValidationError: 설정 스키마 위반
    """
    if not config_path:
        config_path = os.environ.get("SAESSAGI_CONFIG_PATH", "conf.yaml")

    from .config import load_full_config
    from .url_guard import enforce_private_url
    from .logging import init_logging
    from .service_context import AppServiceContext
    from .server import AppWebSocketServer

    from .hardware import (
        detect as detect_hw,
        apply_to_config as hw_apply,
        apply_to_app_config as hw_apply_app,
        log_summary as hw_log,
    )

    # 1. 설정 로딩
    full_config = load_full_config(config_path)

    # 1b. 하드웨어 자동 감지 → upstream ASR/TTS + app RAG 설정 오버라이드
    hw = detect_hw()
    hw_log(hw)
    hw_apply(full_config.upstream, hw)
    hw_apply_app(full_config.app, hw)

    # 2. URL 화이트리스트 검증 (ASR/TTS/LLM 로딩보다 먼저)
    enforce_private_url(full_config.app.ollama.base_url, field_name="OLLAMA_BASE_URL")

    # 3. 로깅 초기화
    log_level = os.environ.get("SAESSAGI_LOG_LEVEL", "INFO").strip() or "INFO"
    init_logging(full_config.app.paths.log_dir, level=log_level)

    logger.info(f"create_app: config_path={config_path}")

    # 4. 서비스 컨텍스트 생성 (비동기 초기화는 lifespan에서 수행)
    ctx = AppServiceContext()

    # ── lifespan 컨텍스트 매니저 (FastAPI 0.115+ 권장 패턴) ──────────────
    @asynccontextmanager
    async def _lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        # startup
        logger.info("애플리케이션 startup 시작")

        # app_config를 먼저 주입 — init_agent 등 upstream 콜백에서 필요
        ctx.app_config = full_config.app

        # 본 프로젝트 서비스 먼저 초기화 (ToolRouter 포함)
        # init_agent가 tool_router_adapter를 볼 수 있도록 load_from_config 전에 실행
        try:
            await ctx.load_app_services(full_config.app)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error(f"load_app_services 실패: {exc}")

        # upstream ServiceContext 초기화 (ASR/TTS/VAD/Agent)
        # ValidationError / FileNotFoundError / PrivacyViolationError는 re-raise (기동 실패)
        # 모델 지연 로딩 실패(RuntimeError, OSError)만 삼켜 기동 계속
        try:
            await ctx.load_from_config(full_config.upstream)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error(f"load_from_config 실패 (모델 로딩 오류): {exc}")
            # 기동 계속 (하위 기능만 OFF)

        # idle_monitor 시작
        if ctx.idle_monitor is not None:
            try:
                result = ctx.idle_monitor.start()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning(f"idle_monitor.start() 실패: {exc}")

        # proactive_dispatcher 시작
        if ctx.proactive_dispatcher is not None:
            try:
                result = ctx.proactive_dispatcher.start()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning(f"proactive_dispatcher.start() 실패: {exc}")

        logger.info("애플리케이션 startup 완료")

        yield  # 앱 실행

        # shutdown
        logger.info("애플리케이션 shutdown 시작")
        try:
            await ctx.close()
        except Exception as exc:
            logger.error(f"ctx.close() 실패: {exc}")
        logger.info("애플리케이션 shutdown 완료")

    # 5. 서버 생성 (lifespan 주입)
    ws_server = AppWebSocketServer(
        config=full_config,
        default_context_cache=ctx,
        lifespan=_lifespan,
    )
    app = ws_server.app

    return app


def run() -> None:
    """CLI 엔트리. argparse로 --config, --verbose 처리, uvicorn.run."""
    import uvicorn

    parser = argparse.ArgumentParser(description="새싹이 AI 비서 서버")
    parser.add_argument(
        "--config",
        default=os.environ.get("SAESSAGI_CONFIG_PATH", "conf.yaml"),
        help="설정 파일 경로 (기본값: conf.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG 레벨 로깅 활성화",
    )
    parser.add_argument("--host", default="127.0.0.1", help="바인드 호스트")
    parser.add_argument("--port", type=int, default=12393, help="바인드 포트")
    args = parser.parse_args()

    if args.verbose:
        os.environ["SAESSAGI_LOG_LEVEL"] = "DEBUG"

    try:
        app = create_app(config_path=args.config)
    except Exception as exc:
        logger.error(f"앱 초기화 실패: {exc}")
        sys.exit(1)

    uvicorn.run(app, host=args.host, port=args.port)

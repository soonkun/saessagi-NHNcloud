# src/app/main.py
"""create_app() 팩토리 및 CLI 엔트리 포인트."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

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

        # M_22 (CR-41): RAG 폴더 감시 시작
        _rag_watch_task = await _start_rag_watch(ctx, full_config.app)
        if _rag_watch_task is not None:
            fastapi_app.state.rag_watch_task = _rag_watch_task

        logger.info("애플리케이션 startup 완료")

        yield  # 앱 실행

        # shutdown
        logger.info("애플리케이션 shutdown 시작")
        if _rag_watch_task is not None:
            _rag_watch_task.cancel()
            try:
                await _rag_watch_task
            except (asyncio.CancelledError, Exception):
                pass
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


async def _start_rag_watch(ctx: Any, app_config: Any) -> "asyncio.Task[None] | None":
    """M_22 (CR-41): RAG 폴더 감시 루프를 백그라운드 태스크로 띄운다.

    비활성이거나 루트가 없으면 조용히 None을 돌려준다 — 감시는 부가 기능이므로
    설정 누락으로 기동을 막지 않는다.
    """
    cfg = getattr(app_config, "rag_watch", None)
    if cfg is None or not cfg.enabled or not cfg.root:
        return None

    root = Path(cfg.root)
    if not root.is_dir():
        logger.warning(f"rag_watch: 감시 루트가 없어 비활성화합니다: {root}")
        return None

    if getattr(ctx, "rag_service", None) is None:
        logger.warning("rag_watch: RagService가 없어 비활성화합니다 (임베딩 불가)")
        return None

    from rag_watch import RagWatchService

    state_path = Path(app_config.paths.data_dir) / "rag_watch_state.json"
    service = RagWatchService(
        root=root,
        state_path=state_path,
        service_context=ctx,
        max_per_cycle=cfg.max_per_cycle,
        delete_policy=cfg.delete_policy.value,
        unindex_guard_ratio=cfg.unindex_guard_ratio,
        unindex_guard_min=cfg.unindex_guard_min,
        max_ingest_failures=cfg.max_ingest_failures,
    )
    ctx.rag_watch_service = service

    async def _loop() -> None:
        # 기동 직후엔 임베더·벡터스토어가 아직 덜 준비됐을 수 있어 한 주기 쉬고 시작한다.
        await asyncio.sleep(min(cfg.interval_seconds, 15))

        # 첫 스캔 전에 반드시 시딩한다 (CR-41). 이미 색인된 파일이 감시 폴더에 있으면
        # 시딩 없이는 전부 재임베딩된다. 상태가 비어 있을 때만 동작하므로 재시작에는 무해하다.
        try:
            await service.seed_from_existing()
        except Exception as exc:
            logger.error(f"rag_watch: 시딩 중 예외 (감시는 계속): {exc!r}")

        while True:
            try:
                await service.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 루프는 절대 죽이지 않는다 — 한 번 죽으면 재시작까지 감시가 멈춘다.
                logger.error(f"rag_watch: 스캔 중 예외 (계속 진행): {exc!r}")
            await asyncio.sleep(cfg.interval_seconds)

    logger.info(
        f"rag_watch: 감시 시작 root={root}, 주기 {cfg.interval_seconds}초, "
        f"삭제정책={cfg.delete_policy.value}, 주기당 최대 {cfg.max_per_cycle}건"
    )
    return asyncio.create_task(_loop())


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
    # 기본값을 두지 않는다 — 미지정 시 conf.yaml의 app.web.host/port를 따른다 (CR-38).
    parser.add_argument(
        "--host", default=None, help="바인드 호스트 (미지정 시 conf.yaml app.web.host)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="바인드 포트 (미지정 시 conf.yaml app.web.port)"
    )
    args = parser.parse_args()

    if args.verbose:
        os.environ["SAESSAGI_LOG_LEVEL"] = "DEBUG"

    try:
        app = create_app(config_path=args.config)
    except Exception as exc:
        logger.error(f"앱 초기화 실패: {exc}")
        sys.exit(1)

    host = args.host or getattr(app.state, "web_host", "127.0.0.1")
    port = args.port or getattr(app.state, "web_port", 12393)
    logger.info(f"서버 바인딩: {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

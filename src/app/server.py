# src/app/server.py
"""AppWebSocketServer — 컴포지션 패턴으로 FastAPI 앱을 보유.

upstream WebSocketServer 상속을 제거하고 내부 속성으로 FastAPI 앱을 보유한다.
upstream의 init_client_ws_route 대신 init_app_ws_route를 사용해 AppWebSocketHandler를 주입.
"""

import os
from typing import Any

from fastapi import FastAPI
from loguru import logger
from starlette.middleware.cors import CORSMiddleware

from open_llm_vtuber.server import CORSStaticFiles, AvatarStaticFiles  # upstream (수정 없이 재사용)
from open_llm_vtuber.routes import init_webtool_routes  # upstream (수정 없이 재사용)

from .config import FullConfig
from .service_context import AppServiceContext
from .ws_route import init_app_ws_route


class AppWebSocketServer:
    """본 프로젝트 FastAPI 서버 (컴포지션 패턴).

    upstream WebSocketServer를 상속하는 대신 FastAPI 앱을 내부 속성으로 보유한다.
    스펙 §"우회 패턴": init_client_ws_route 대신 init_app_ws_route 사용.

    상속하지 않는 이유:
    - upstream __init__이 init_client_ws_route와 StaticFiles 마운트를 직접 수행
    - super().__init__() 호출 시 upstream WebSocketHandler가 등록됨 (AppWebSocketHandler 교체 불가)
    - 컴포지션이 "upstream 수정 없음" 원칙과 일관됨
    """

    def __init__(
        self,
        config: FullConfig,
        default_context_cache: AppServiceContext,
        lifespan: Any | None = None,
    ) -> None:
        # FastAPI 앱을 내부 속성으로 보유 (upstream WebSocketServer.app 패턴과 동일)
        self.app: FastAPI = FastAPI(title="새싹이 AI 비서", lifespan=lifespan)
        self.full_config: FullConfig = config
        self.config = config.upstream  # upstream 호환용 — Config 객체
        self.default_context_cache = default_context_cache

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # AppWebSocketHandler 주입된 /client-ws 라우터 등록
        self.app.include_router(init_app_ws_route(default_context_cache=self.default_context_cache))

        # upstream webtool 라우터 (수정 없이 재사용)
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache)
        )

        # M_13: 회의록 다운로드 라우터 등록
        from .meeting_minutes_routes import router as meeting_router

        # service_context를 request.app.state에서 접근 가능하도록 설정
        self.app.state.service_context = default_context_cache
        self.app.include_router(meeting_router, prefix="", tags=["meeting_minutes"])

        # 캐시 디렉토리
        cache_dir = "cache"
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        self.app.mount(
            "/cache",
            CORSStaticFiles(directory=cache_dir),
            name="cache",
        )

        # Live2D 모델 — upstream CWD(live2d-models/)에서 서빙
        live2d_dir = "live2d-models"
        if os.path.exists(live2d_dir):
            self.app.mount(
                "/live2d-models",
                CORSStaticFiles(directory=live2d_dir),
                name="live2d-models",
            )
        else:
            logger.warning(f"Live2D 모델 디렉토리 없음, /live2d-models 마운트 건너뜀: {live2d_dir}")

        # 배경 이미지 (디렉토리가 있을 때만 마운트)
        bg_dir = "backgrounds"
        if os.path.exists(bg_dir):
            self.app.mount(
                "/bg",
                CORSStaticFiles(directory=bg_dir),
                name="backgrounds",
            )
        else:
            logger.warning(f"배경 디렉토리 없음, /bg 마운트 건너뜀: {bg_dir}")

        # 새싹이 스프라이트 (/avatars → assets/character/saessagi/)
        # conf.yaml avatar 필드 파일명과 실제 파일명이 일치해야 함 (예: neutral.png)
        saessagi_avatar_dir = os.path.join(
            self.full_config.app.paths.assets_dir, "character", "saessagi"
        )
        if os.path.exists(saessagi_avatar_dir):
            self.app.mount(
                "/avatars",
                AvatarStaticFiles(directory=saessagi_avatar_dir),
                name="avatars",
            )
        else:
            logger.warning(
                f"새싹이 아바타 디렉토리 없음, /avatars 마운트 건너뜀: {saessagi_avatar_dir}"
            )

        # 프론트엔드 (존재할 때만 마운트)
        frontend_dir = "frontend"
        if os.path.exists(frontend_dir):
            self.app.mount(
                "/",
                CORSStaticFiles(directory=frontend_dir, html=True),
                name="frontend",
            )
        else:
            logger.warning(f"프론트엔드 디렉토리 없음, / 마운트 건너뜀: {frontend_dir}")

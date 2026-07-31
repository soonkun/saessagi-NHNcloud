# src/app/server.py
"""AppWebSocketServer — 컴포지션 패턴으로 FastAPI 앱을 보유.

upstream WebSocketServer 상속을 제거하고 내부 속성으로 FastAPI 앱을 보유한다.
upstream의 init_client_ws_route 대신 init_app_ws_route를 사용해 AppWebSocketHandler를 주입.
"""

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from loguru import logger
from starlette.middleware.cors import CORSMiddleware

from open_llm_vtuber.server import CORSStaticFiles, AvatarStaticFiles  # upstream (수정 없이 재사용)
from open_llm_vtuber.routes import init_webtool_routes  # upstream (수정 없이 재사용)

from .config import FullConfig
from .service_context import AppServiceContext
from .ws_route import init_app_ws_route


class _SaessagiAvatarFiles(AvatarStaticFiles):
    """아바타 정적 파일 — 영상(webm)도 허용한다 (E-77).

    upstream AvatarStaticFiles는 이미지 확장자만 통과시키고 나머지를 403으로 막는다.
    그런데 문서 임베딩 중에 재생할 `uploading.webm`이 그 필터에 걸려, 화면에서
    캐릭터가 통째로 사라졌다(영상 요소가 아무것도 그리지 못한 채 남았다).
    upstream 파일은 수정하지 않고 여기서 확장자만 넓힌다.
    """

    _EXTRA_EXTENSIONS = (".webm", ".mp4")

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        if path.lower().endswith(self._EXTRA_EXTENSIONS):
            # CORSStaticFiles(부모의 부모)의 처리로 바로 넘긴다 — 확장자 검사만 건너뛴다.
            return await CORSStaticFiles.get_response(self, path, scope)
        return await super().get_response(path, scope)


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
        self.app: FastAPI = FastAPI(title="새싹이 AI 비서", lifespan=lifespan, root_path="/sQRXE2adVt")
        self.full_config: FullConfig = config
        self.config = config.upstream  # upstream 호환용 — Config 객체
        self.default_context_cache = default_context_cache

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # M_21 (CR-38): 웹 UI 인증. 라우터 등록보다 먼저 미들웨어를 걸어 HTTP·정적파일·
        # WebSocket을 한 번에 덮는다. /client-ws가 빠지면 대화·TTS가 통째로 무인증이 된다.
        self._init_web_auth()

        # 인증 라우트 (로그인 페이지·로그인·로그아웃) — 미들웨어가 면제하는 경로
        from .auth_routes import router as auth_router

        self.app.include_router(auth_router)

        # AppWebSocketHandler 주입된 /client-ws 라우터 등록
        self.app.include_router(init_app_ws_route(default_context_cache=self.default_context_cache))

        # upstream webtool 라우터 (수정 없이 재사용)
        self.app.include_router(
            init_webtool_routes(default_context_cache=self.default_context_cache)
        )

        # M_13: 회의록 다운로드 라우터 등록
        from .meeting_minutes_routes import router as meeting_router
        from .calendar_routes import router as calendar_router
        from .rag_routes import router as rag_router
        from .settings_routes import router as settings_router
        from .tts_routes import router as tts_router
        from .knowledge_routes import router as knowledge_router
        from .graphrag_routes import router as graphrag_router  # M_19
        from .deep_research_routes import router as deep_research_router  # M_20

        # service_context를 request.app.state에서 접근 가능하도록 설정
        self.app.state.service_context = default_context_cache
        self.app.include_router(meeting_router, prefix="", tags=["meeting_minutes"])
        self.app.include_router(calendar_router)
        self.app.include_router(rag_router)
        self.app.include_router(settings_router)
        self.app.include_router(tts_router)
        self.app.include_router(knowledge_router)
        self.app.include_router(graphrag_router)  # M_19
        self.app.include_router(deep_research_router)  # M_20

        # 캐시 디렉토리
        cache_dir = "cache"
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        self.app.mount(
            "/cache",
            CORSStaticFiles(directory=cache_dir),
            name="cache",
        )

        # 새싹이 스프라이트 (/avatars → assets/character/saessagi/)
        # conf.yaml avatar 필드 파일명과 실제 파일명이 일치해야 함 (예: neutral.png)
        saessagi_avatar_dir = os.path.join(
            self.full_config.app.paths.assets_dir, "character", "saessagi"
        )
        if os.path.exists(saessagi_avatar_dir):
            self.app.mount(
                "/avatars",
                _SaessagiAvatarFiles(directory=saessagi_avatar_dir),
                name="avatars",
            )
        else:
            logger.warning(
                f"새싹이 아바타 디렉토리 없음, /avatars 마운트 건너뜀: {saessagi_avatar_dir}"
            )

        # 프론트엔드 — web/dist (CR-38: Electron 제거로 frontend/dist 폴백 삭제)
        _saessagi_root = os.environ.get("SAESSAGI_ROOT", "")
        _web_dist = Path(_saessagi_root) / "web" / "dist" if _saessagi_root else None

        if _web_dist and (_web_dist / "index.html").exists():
            frontend_dir = str(_web_dist)
            logger.info(f"프론트엔드: web/dist 사용: {frontend_dir}")
        else:
            frontend_dir = "web/dist"
            logger.warning("web/dist 빌드가 없습니다. 'cd web && npm run build'를 먼저 실행하세요.")

        if os.path.exists(frontend_dir):
            self.app.mount(
                "/",
                CORSStaticFiles(directory=frontend_dir, html=True),
                name="frontend",
            )
        else:
            logger.warning(f"프론트엔드 디렉토리 없음, / 마운트 건너뜀: {frontend_dir}")

    def _init_web_auth(self) -> None:
        """M_21 (CR-38): 웹 인증 미들웨어 설치 + 위험한 설정 조합 차단.

        auth_routes가 request.app.state에서 비밀번호·salt를 읽으므로 여기서 함께 심는다.
        """
        from .web_auth import (
            NoStoreHtmlMiddleware,
            WebAuthMiddleware,
            load_or_create_salt,
            resolve_password,
            validate_web_config,
        )

        # CR-40: HTML 문서는 캐시하지 않는다. 인증 여부와 무관하게 항상 적용 —
        # 캐시된 index.html이 미들웨어를 건너뛰면 로그아웃이 무력해진다.
        self.app.add_middleware(NoStoreHtmlMiddleware)

        web_cfg = self.full_config.app.web
        password = resolve_password(web_cfg.auth_password)

        # 설정 실수(열어두고 인증 끔)를 조용한 노출이 아니라 기동 실패로 바꾼다.
        validate_web_config(web_cfg.host, web_cfg.auth_enabled, password)

        salt = (
            load_or_create_salt(self.full_config.app.paths.data_dir) if web_cfg.auth_enabled else ""
        )

        # run()이 uvicorn 바인딩에 사용 (CLI 인자가 있으면 그쪽이 우선)
        self.app.state.web_host = web_cfg.host
        self.app.state.web_port = web_cfg.port

        self.app.state.web_auth_enabled = web_cfg.auth_enabled
        self.app.state.web_auth_password = password
        self.app.state.web_auth_salt = salt
        self.app.state.web_auth_ttl_hours = web_cfg.session_ttl_hours

        self.app.add_middleware(
            WebAuthMiddleware,
            enabled=web_cfg.auth_enabled,
            password=password,
            salt=salt,
        )

        if web_cfg.auth_enabled:
            logger.info(
                f"web_auth: 인증 활성 (host={web_cfg.host}:{web_cfg.port}, "
                f"세션 {web_cfg.session_ttl_hours}시간)"
            )
        else:
            logger.info(f"web_auth: 인증 비활성 (로컬 전용 {web_cfg.host}:{web_cfg.port})")

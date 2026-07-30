# src/app/auth_routes.py
"""M_21 WebAuth 라우트 (CR-38) — 로그인 페이지·로그인·로그아웃.

미들웨어(web_auth.WebAuthMiddleware)가 EXEMPT_PATHS로 이 세 경로만 열어둔다.
"""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from .web_auth import (
    LOGIN_HTML,
    build_clear_cookie_header,
    build_cookie_header,
    is_secure_request,
    issue_token,
)

router = APIRouter(tags=["auth"])

# 로그인 실패 지연(초) — 무차별 대입 완화.
_FAILURE_DELAY_SEC = 0.5


class LoginRequest(BaseModel):
    password: str


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """로그인 화면. 프론트 빌드에 의존하지 않도록 인라인 HTML을 반환한다."""
    return HTMLResponse(LOGIN_HTML)


@router.post("/api/auth/login")
async def login(body: LoginRequest, request: Request) -> Response:
    """비밀번호 검증 후 세션 쿠키 설정."""
    state = request.app.state
    expected: str = getattr(state, "web_auth_password", "")
    salt: str = getattr(state, "web_auth_salt", "")
    ttl: int = getattr(state, "web_auth_ttl_hours", 12)

    # 인증이 꺼져 있으면 로그인 자체가 의미 없다.
    if not getattr(state, "web_auth_enabled", False):
        return JSONResponse({"detail": "auth disabled"}, status_code=400)

    # 길이 노출을 줄이기 위해 상수시간 비교.
    if not expected or not hmac.compare_digest(body.password, expected):
        await asyncio.sleep(_FAILURE_DELAY_SEC)
        client = request.client.host if request.client else "unknown"
        logger.warning(f"web_auth: 로그인 실패 from={client}")
        return JSONResponse({"detail": "invalid password"}, status_code=401)

    token = issue_token(expected, salt, ttl)
    response = JSONResponse({"ok": True})
    response.headers["set-cookie"] = build_cookie_header(
        token, ttl, secure=is_secure_request(request.scope)
    )
    return response


@router.post("/api/auth/logout")
async def logout() -> Response:
    response = JSONResponse({"ok": True})
    response.headers["set-cookie"] = build_clear_cookie_header()
    return response


@router.get("/api/auth/status")
async def auth_status(request: Request) -> Response:
    """인증이 켜져 있는지 알려준다 — UI가 로그아웃 버튼 노출 여부를 판단하는 데 쓴다.

    비밀번호나 세션 내용은 노출하지 않는다. 인증이 꺼진 배포에서 로그아웃 버튼을 보여주면
    누른 뒤 아무 의미 없는 로그인 화면에 갇히므로, 이 정보가 UI에 필요하다.
    """
    enabled = bool(getattr(request.app.state, "web_auth_enabled", False))
    return JSONResponse({"auth_enabled": enabled})

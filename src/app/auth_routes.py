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


# ── CR-69: 관리 기능 2차 잠금 ─────────────────────────────────────────────────


class AdminUnlockReq(BaseModel):
    password: str = ""


@router.post("/api/auth/admin-unlock")
async def admin_unlock(body: AdminUnlockReq, request: Request) -> JSONResponse:
    """문서 관리·지식그래프 관리 진입 비밀번호 확인.

    로그인은 "이 앱을 쓸 수 있는가"를 가르고, 이쪽은 "문서를 지우거나 그래프를 다시
    만들 수 있는가"를 가른다. 실수로 눌러 코퍼스를 날리는 것을 막는 것이 목적이다.

    **비밀번호를 프론트에 심지 않는다** — 번들에 넣으면 누구나 읽는다. 여기서 비교한다.
    타이밍 공격을 피하려고 `hmac.compare_digest`를 쓴다(로그인과 같은 방식).
    """
    import os

    ctx = getattr(request.app.state, "service_context", None)
    web = getattr(getattr(ctx, "app_config", None), "web", None)
    expected = os.environ.get("SAESSAGI_ADMIN_PASSWORD") or getattr(web, "admin_password", "Rda123")
    ok = bool(expected) and hmac.compare_digest(str(body.password or ""), str(expected))
    if not ok:
        # 비밀번호 자체는 남기지 않는다.
        logger.warning("관리 기능 잠금 해제 실패 (경로=%s)", request.url.path)
        await asyncio.sleep(0.4)  # 무차별 대입 완화
    return JSONResponse({"ok": ok})

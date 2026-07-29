# src/app/web_auth.py
"""M_21 WebAuth — 웹 UI 비밀번호 인증 (CR-38).

사내망에 노출되는 웹 UI 앞에 단일 비밀번호 인증을 둔다. 앱에 사용자 개념이 없으므로
"비밀번호를 아는 사람 = 사용 가능"이 전부다. 기관 SSO 도입 시 이 모듈을 교체한다.

핵심은 **WebSocket까지 보호**하는 것이다. 대화·TTS는 전부 /client-ws로 흐르므로
HTTP만 막으면 사실상 아무것도 막지 못한다. 그래서 라우트 의존성(Depends)이 아니라
순수 ASGI 미들웨어로 구현한다 — scope["type"]이 http든 websocket이든 동일하게 걸린다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Awaitable, Callable, MutableMapping

from loguru import logger

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

COOKIE_NAME = "saessagi_session"

# 인증 없이 통과시킬 경로 — 로그인 자체에 필요한 것만.
EXEMPT_PATHS = frozenset({"/login", "/api/auth/login", "/api/auth/logout"})

# 실패 응답 지연(초). 비밀번호 1개짜리라 무차별 대입을 늦추는 정도만 한다.
_FAILURE_DELAY_SEC = 0.5


# ────────────────────────────────────────────────────────────
# 토큰 — 상태 없는 HMAC 서명 방식
# ────────────────────────────────────────────────────────────


def _salt_path(data_dir: str) -> Path:
    return Path(data_dir) / ".web_auth_salt"


def load_or_create_salt(data_dir: str) -> str:
    """세션 salt를 읽거나 생성한다. 재시작해도 세션이 유지되도록 파일에 보관한다."""
    path = _salt_path(data_dir)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    salt = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(salt, encoding="utf-8")
    # 비밀번호와 결합해 서명키가 되므로 소유자만 읽게 한다.
    os.chmod(path, 0o600)
    logger.info(f"web_auth: 세션 salt 생성 {path}")
    return salt


def _secret(password: str, salt: str) -> bytes:
    return hashlib.sha256((password + salt).encode("utf-8")).digest()


def issue_token(password: str, salt: str, ttl_hours: int) -> str:
    """만료시각을 담은 서명 토큰 발급. 비밀번호가 바뀌면 기존 토큰은 자동 무효화된다."""
    exp = int(time.time()) + ttl_hours * 3600
    sig = hmac.new(_secret(password, salt), str(exp).encode("utf-8"), hashlib.sha256)
    return f"{exp}.{sig.hexdigest()}"


def verify_token(token: str, password: str, salt: str) -> bool:
    """서명과 만료를 모두 검증. 형식 오류는 조용히 실패 처리한다."""
    if not token or "." not in token:
        return False
    exp_str, _, sig_hex = token.partition(".")
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < time.time():
        return False

    expected = hmac.new(
        _secret(password, salt), exp_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_hex)


# ────────────────────────────────────────────────────────────
# 설정 검증 — 설정 실수를 실행 실패로 바꾼다
# ────────────────────────────────────────────────────────────


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # 호스트명은 판단할 수 없으므로 노출된 것으로 간주(보수적으로).
        return False


def validate_web_config(host: str, auth_enabled: bool, password: str) -> None:
    """위험한 조합이면 예외를 던져 기동을 막는다.

    "네트워크에 열어두고 인증 켜는 걸 잊는" 사고가 가장 위험하므로, 조용히 동작하게
    두지 않고 시작 자체를 실패시킨다.
    """
    if not _is_loopback(host) and not auth_enabled:
        raise ValueError(
            f"app.web.host가 '{host}'로 외부에 열려 있는데 app.web.auth_enabled가 false입니다. "
            "인증 없이 사내 문서·LLM이 네트워크에 노출됩니다. "
            "auth_enabled: true + auth_password를 설정하거나 host를 127.0.0.1로 되돌리세요."
        )
    if auth_enabled and not password:
        raise ValueError(
            "app.web.auth_enabled가 true인데 비밀번호가 비어 있습니다. "
            "conf.yaml의 app.web.auth_password 또는 환경변수 SAESSAGI_WEB_PASSWORD를 설정하세요."
        )


def resolve_password(configured: str) -> str:
    """환경변수 우선 — conf.yaml에 평문을 남기지 않고 운영할 수 있게 한다."""
    return os.environ.get("SAESSAGI_WEB_PASSWORD") or configured


# ────────────────────────────────────────────────────────────
# 요청 파싱 헬퍼
# ────────────────────────────────────────────────────────────


def _cookie_token(scope: Scope) -> str:
    for key, value in scope.get("headers") or []:
        if key == b"cookie":
            jar: SimpleCookie = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:
                return ""
            morsel = jar.get(COOKIE_NAME)
            return morsel.value if morsel else ""
    return ""


def is_secure_request(scope: Scope) -> bool:
    """HTTPS로 들어온 요청인가 — 쿠키에 Secure를 붙일지 판단한다.

    평문 HTTP에 Secure를 붙이면 브라우저가 쿠키를 저장하지 않아 로그인이 무한 반복된다.
    리버스 프록시 뒤를 대비해 X-Forwarded-Proto도 본다.
    """
    if scope.get("scheme") in ("https", "wss"):
        return True
    for key, value in scope.get("headers") or []:
        if key == b"x-forwarded-proto" and value.decode("latin-1").split(",")[0].strip() == "https":
            return True
    return False


def build_cookie_header(token: str, ttl_hours: int, secure: bool) -> str:
    parts = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={ttl_hours * 3600}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


# ────────────────────────────────────────────────────────────
# 미들웨어
# ────────────────────────────────────────────────────────────


class WebAuthMiddleware:
    """HTTP·정적파일·WebSocket을 한 곳에서 보호하는 순수 ASGI 미들웨어."""

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool,
        password: str,
        salt: str,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.password = password
        self.salt = salt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        if verify_token(_cookie_token(scope), self.password, self.salt):
            await self.app(scope, receive, send)
            return

        # 여기부터는 인증 실패 경로.
        if scope["type"] == "websocket":
            # 핸드셰이크를 거부한다. accept 전 close는 HTTP 403으로 나간다.
            logger.warning(f"web_auth: 미인증 WebSocket 거부 path={path}")
            await send({"type": "websocket.close", "code": 1008})
            return

        await asyncio.sleep(_FAILURE_DELAY_SEC)

        if path.startswith("/api/"):
            body = json.dumps({"detail": "unauthorized"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("latin-1")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        # 문서 요청 — 로그인 페이지로 보낸다.
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [(b"location", b"/login"), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})


# ────────────────────────────────────────────────────────────
# 로그인 페이지 — 프론트 빌드에 의존하지 않도록 인라인 HTML
# ────────────────────────────────────────────────────────────

LOGIN_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>새싹이 — 로그인</title>
<style>
  :root { color-scheme: light dark; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #f4f6f9; color: #1b1f24;
    font-family: system-ui, -apple-system, "Malgun Gothic", sans-serif;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #14171c; color: #e6e9ee; }
    form { background: #1d2127 !important; border-color: #2c323b !important; }
    input { background: #14171c !important; color: #e6e9ee !important; border-color: #2c323b !important; }
  }
  form {
    background: #fff; border: 1px solid #dde2e8; border-radius: 14px;
    padding: 32px 28px; width: 320px; box-shadow: 0 6px 24px rgba(0,0,0,.07);
  }
  h1 { font-size: 17px; margin: 0 0 4px; }
  p.sub { margin: 0 0 20px; font-size: 13px; opacity: .65; }
  input {
    width: 100%; box-sizing: border-box; padding: 10px 12px; font-size: 14px;
    border: 1px solid #dde2e8; border-radius: 8px; background: #fff; color: inherit;
  }
  button {
    width: 100%; margin-top: 12px; padding: 10px; font-size: 14px; font-weight: 600;
    border: 0; border-radius: 8px; background: #3b82f6; color: #fff; cursor: pointer;
  }
  button:disabled { opacity: .6; cursor: default; }
  .err { margin-top: 10px; font-size: 13px; color: #dc2626; min-height: 18px; }
</style>
</head>
<body>
<form id="f">
  <h1>🌱 새싹이</h1>
  <p class="sub">사내 AI 비서 — 비밀번호를 입력하세요</p>
  <input id="pw" type="password" autocomplete="current-password" autofocus />
  <button id="btn" type="submit">로그인</button>
  <div class="err" id="err"></div>
</form>
<script>
  const f = document.getElementById("f");
  const pw = document.getElementById("pw");
  const btn = document.getElementById("btn");
  const err = document.getElementById("err");
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    btn.disabled = true;
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password: pw.value }),
      });
      if (res.ok) { location.replace("/"); return; }
      err.textContent = "비밀번호가 올바르지 않습니다.";
      pw.select();
    } catch (_) {
      err.textContent = "서버에 연결할 수 없습니다.";
    } finally {
      btn.disabled = false;
    }
  });
</script>
</body>
</html>
"""

"""M_21 WebAuth 테스트 (CR-38) — 토큰·설정검증·미들웨어(HTTP/WebSocket)."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from app.web_auth import (
    COOKIE_NAME,
    WebAuthMiddleware,
    build_cookie_header,
    is_secure_request,
    issue_token,
    load_or_create_salt,
    resolve_password,
    validate_web_config,
    verify_token,
)

PASSWORD = "sprout-2026"
SALT = "test-salt-value"


# ────────────────────────────────────────────────────────────
# 토큰
# ────────────────────────────────────────────────────────────


class TestToken:
    def test_t1_roundtrip(self) -> None:
        token = issue_token(PASSWORD, SALT, ttl_hours=1)
        assert verify_token(token, PASSWORD, SALT) is True

    def test_t2_expired_rejected(self) -> None:
        # 이미 만료된 토큰을 직접 구성 (ttl 음수는 API가 막으므로 서명만 재현)
        import hashlib
        import hmac

        exp = int(time.time()) - 10
        secret = hashlib.sha256((PASSWORD + SALT).encode()).digest()
        sig = hmac.new(secret, str(exp).encode(), hashlib.sha256).hexdigest()
        assert verify_token(f"{exp}.{sig}", PASSWORD, SALT) is False

    def test_t3_tampered_signature_rejected(self) -> None:
        token = issue_token(PASSWORD, SALT, ttl_hours=1)
        exp, _, sig = token.partition(".")
        flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
        assert verify_token(f"{exp}.{flipped}", PASSWORD, SALT) is False

    def test_t4_password_change_invalidates(self) -> None:
        token = issue_token(PASSWORD, SALT, ttl_hours=1)
        assert verify_token(token, "different-password", SALT) is False

    @pytest.mark.parametrize("bad", ["", "garbage", "notanint.abc", "123"])
    def test_t5_malformed_rejected(self, bad: str) -> None:
        assert verify_token(bad, PASSWORD, SALT) is False

    def test_t6_salt_persisted_and_private(self, tmp_path) -> None:
        first = load_or_create_salt(str(tmp_path))
        second = load_or_create_salt(str(tmp_path))
        assert first == second, "salt는 재시작해도 유지되어야 세션이 살아남는다"
        mode = (tmp_path / ".web_auth_salt").stat().st_mode & 0o777
        assert mode == 0o600


# ────────────────────────────────────────────────────────────
# 설정 검증 — 위험한 조합은 기동 실패
# ────────────────────────────────────────────────────────────


class TestConfigValidation:
    def test_c1_exposed_without_auth_refused(self) -> None:
        with pytest.raises(ValueError, match="auth_enabled"):
            validate_web_config("0.0.0.0", auth_enabled=False, password="")

    def test_c2_auth_enabled_without_password_refused(self) -> None:
        with pytest.raises(ValueError, match="비밀번호"):
            validate_web_config("0.0.0.0", auth_enabled=True, password="")

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_c3_loopback_without_auth_ok(self, host: str) -> None:
        validate_web_config(host, auth_enabled=False, password="")

    def test_c4_exposed_with_auth_ok(self) -> None:
        validate_web_config("0.0.0.0", auth_enabled=True, password=PASSWORD)

    def test_c5_hostname_treated_as_exposed(self) -> None:
        # 호스트명은 루프백인지 판단할 수 없으므로 보수적으로 노출로 본다
        with pytest.raises(ValueError):
            validate_web_config("saessagi.corp", auth_enabled=False, password="")

    def test_c6_env_password_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("SAESSAGI_WEB_PASSWORD", "from-env")
        assert resolve_password("from-conf") == "from-env"

    def test_c7_conf_password_when_no_env(self, monkeypatch) -> None:
        monkeypatch.delenv("SAESSAGI_WEB_PASSWORD", raising=False)
        assert resolve_password("from-conf") == "from-conf"


# ────────────────────────────────────────────────────────────
# 미들웨어
# ────────────────────────────────────────────────────────────


def _make_app(*, enabled: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/login")
    async def login_page() -> dict[str, bool]:
        return {"login": True}

    @app.get("/api/things")
    async def things() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/index.html")
    async def doc() -> dict[str, bool]:
        return {"doc": True}

    @app.websocket("/client-ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        await sock.send_text("hello")
        await sock.close()

    app.add_middleware(WebAuthMiddleware, enabled=enabled, password=PASSWORD, salt=SALT)
    return app


@pytest.fixture()
def auth_client() -> TestClient:
    return TestClient(_make_app(enabled=True))


class TestMiddleware:
    def test_m1_disabled_passes_through(self) -> None:
        client = TestClient(_make_app(enabled=False))
        assert client.get("/api/things").status_code == 200

    def test_m2_api_unauthenticated_401(self, auth_client: TestClient) -> None:
        res = auth_client.get("/api/things")
        assert res.status_code == 401
        assert res.json() == {"detail": "unauthorized"}

    def test_m3_document_unauthenticated_redirects(self, auth_client: TestClient) -> None:
        res = auth_client.get("/index.html", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

    def test_m4_login_path_exempt(self, auth_client: TestClient) -> None:
        assert auth_client.get("/login").status_code == 200

    def test_m5_websocket_rejected_without_auth(self, auth_client: TestClient) -> None:
        """대화·TTS가 전부 이 경로로 흐르므로 여기가 뚫리면 인증 전체가 무의미하다."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc:
            with auth_client.websocket_connect("/client-ws") as sock:
                sock.receive_text()
        # 1008 = policy violation. 핸들러가 보낸 "hello"가 아니라 거부여야 한다.
        assert exc.value.code == 1008

    def test_m6_valid_cookie_grants_api(self, auth_client: TestClient) -> None:
        token = issue_token(PASSWORD, SALT, ttl_hours=1)
        res = auth_client.get("/api/things", cookies={COOKIE_NAME: token})
        assert res.status_code == 200
        assert res.json() == {"ok": True}

    def test_m7_valid_cookie_grants_websocket(self, auth_client: TestClient) -> None:
        token = issue_token(PASSWORD, SALT, ttl_hours=1)
        with auth_client.websocket_connect("/client-ws", cookies={COOKIE_NAME: token}) as sock:
            assert sock.receive_text() == "hello"

    def test_m8_wrong_password_cookie_rejected(self, auth_client: TestClient) -> None:
        bad = issue_token("wrong-password", SALT, ttl_hours=1)
        assert auth_client.get("/api/things", cookies={COOKIE_NAME: bad}).status_code == 401


# ────────────────────────────────────────────────────────────
# 쿠키 속성
# ────────────────────────────────────────────────────────────


class TestCookie:
    def test_k1_plain_http_omits_secure(self) -> None:
        """평문 HTTP에 Secure를 붙이면 쿠키가 저장되지 않아 로그인이 무한 반복된다."""
        header = build_cookie_header("tok", 12, secure=False)
        assert "Secure" not in header
        assert "HttpOnly" in header and "SameSite=Lax" in header

    def test_k2_https_sets_secure(self) -> None:
        assert "Secure" in build_cookie_header("tok", 12, secure=True)

    def test_k3_detects_https_scheme(self) -> None:
        assert is_secure_request({"scheme": "https", "headers": []}) is True
        assert is_secure_request({"scheme": "http", "headers": []}) is False

    def test_k4_detects_forwarded_proto(self) -> None:
        scope = {"scheme": "http", "headers": [(b"x-forwarded-proto", b"https")]}
        assert is_secure_request(scope) is True

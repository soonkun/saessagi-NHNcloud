"""M_21 인증 라우트 테스트 — 로그인·로그아웃·상태 조회 (CR-38)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth_routes import router
from app.web_auth import COOKIE_NAME, issue_token, verify_token

PASSWORD = "sprout-2026"
SALT = "route-test-salt"


def _make_app(*, enabled: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.web_auth_enabled = enabled
    app.state.web_auth_password = PASSWORD
    app.state.web_auth_salt = SALT
    app.state.web_auth_ttl_hours = 12
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


class TestLogin:
    def test_r1_correct_password_sets_cookie(self, client: TestClient) -> None:
        res = client.post("/api/auth/login", json={"password": PASSWORD})
        assert res.status_code == 200
        token = res.cookies.get(COOKIE_NAME)
        assert token and verify_token(token, PASSWORD, SALT)

    def test_r2_wrong_password_401_no_cookie(self, client: TestClient) -> None:
        res = client.post("/api/auth/login", json={"password": "nope"})
        assert res.status_code == 401
        assert res.cookies.get(COOKIE_NAME) is None

    def test_r3_plain_http_cookie_has_no_secure(self, client: TestClient) -> None:
        """TestClient는 http — Secure가 붙으면 브라우저가 쿠키를 버려 로그인이 반복된다."""
        res = client.post("/api/auth/login", json={"password": PASSWORD})
        assert "Secure" not in res.headers["set-cookie"]

    def test_r4_login_rejected_when_auth_disabled(self) -> None:
        c = TestClient(_make_app(enabled=False))
        assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 400


class TestLogout:
    def test_r5_clears_cookie(self, client: TestClient) -> None:
        res = client.post("/api/auth/logout")
        assert res.status_code == 200
        cookie = res.headers["set-cookie"]
        assert f"{COOKIE_NAME}=" in cookie and "Max-Age=0" in cookie

    def test_r6_delete_directive_matches_original_cookie(self, client: TestClient) -> None:
        """삭제용 Set-Cookie는 발급 때와 Path·속성이 같아야 브라우저가 실제로 지운다.

        Path가 다르면 브라우저는 별개 쿠키로 보고 원본을 남겨둔다 — 로그아웃한 척만 된다.
        """
        issued = client.post("/api/auth/login", json={"password": PASSWORD}).headers[
            "set-cookie"
        ]
        cleared = client.post("/api/auth/logout").headers["set-cookie"]

        def attrs(header: str) -> set[str]:
            return {p.strip() for p in header.split(";")[1:] if "Max-Age" not in p}

        assert attrs(issued) == attrs(cleared)
        assert "Max-Age=0" in cleared
        # 값이 비워져 있어야 남은 토큰이 재사용되지 않는다
        assert cleared.split(";")[0] == f"{COOKIE_NAME}="

    def test_r7_cleared_cookie_fails_verification(self) -> None:
        """혹시 빈 값이 통과하면 로그아웃이 무의미해진다."""
        assert verify_token("", PASSWORD, SALT) is False


class TestStatus:
    def test_r8_reports_enabled(self, client: TestClient) -> None:
        assert client.get("/api/auth/status").json() == {"auth_enabled": True}

    def test_r9_reports_disabled(self) -> None:
        c = TestClient(_make_app(enabled=False))
        assert c.get("/api/auth/status").json() == {"auth_enabled": False}

    def test_r10_leaks_no_secret(self, client: TestClient) -> None:
        body = client.get("/api/auth/status").text
        assert PASSWORD not in body and SALT not in body

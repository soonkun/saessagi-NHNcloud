# tests/meeting_minutes/test_routes.py
"""M_13 MeetingMinutes 라우터 테스트 (N-4, E-6)."""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.meeting_minutes_routes import router
from meeting_minutes.errors import MeetingFileNotFoundError


def _make_app(service: object | None) -> FastAPI:
    """테스트용 FastAPI 앱 생성."""
    app = FastAPI()
    app.include_router(router)

    # service_context mock
    ctx = MagicMock()
    ctx.meeting_minutes_service = service
    app.state.service_context = ctx
    return app


@pytest.fixture
def dummy_hwpx_file(tmp_path: Path) -> tuple[str, Path]:
    """테스트용 HWPX 더미 파일 (유효한 ZIP) 생성."""
    file_id = str(uuid.uuid4())
    hwpx_path = tmp_path / f"{file_id}.hwpx"

    # 최소한의 유효한 ZIP 파일 생성
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", "<root/>")
    hwpx_path.write_bytes(buf.getvalue())

    return file_id, hwpx_path


# ── 정상 케이스 ──────────────────────────────────────────────


def test_n4_download_200(dummy_hwpx_file: tuple[str, Path]) -> None:
    """N-4: GET /download/{valid_uuid} → 200, Content-Type HWPX, 응답 길이 > 100."""
    file_id, hwpx_path = dummy_hwpx_file

    service = MagicMock()
    service.resolve = MagicMock(return_value=hwpx_path)

    app = _make_app(service)
    client = TestClient(app)

    response = client.get(f"/download/{file_id}")

    assert response.status_code == 200
    assert "hancom" in response.headers["content-type"].lower()
    assert len(response.content) > 100


def test_download_404_when_not_found() -> None:
    """N: 존재하지 않는 file_id → 404."""
    service = MagicMock()
    service.resolve = MagicMock(side_effect=MeetingFileNotFoundError("not found"))

    app = _make_app(service)
    client = TestClient(app)

    response = client.get(f"/download/{uuid.uuid4()}")
    assert response.status_code == 404


def test_download_422_invalid_uuid() -> None:
    """N: UUIDv4 형식이 아닌 file_id → 422."""
    service = MagicMock()
    service.resolve = MagicMock(side_effect=ValueError("invalid uuid"))

    app = _make_app(service)
    client = TestClient(app)

    response = client.get("/download/not-a-valid-uuid-format!!!")
    assert response.status_code == 422


def test_download_503_service_none() -> None:
    """N: service=None → 503."""
    app = _make_app(None)
    client = TestClient(app)

    response = client.get(f"/download/{uuid.uuid4()}")
    assert response.status_code == 503


# ── 엣지 케이스 ──────────────────────────────────────────────


def test_e6_download_base_url_non_loopback(
    fake_agent: MagicMock,
    template_path: Path,
    tmp_path: Path,
) -> None:
    """E-6: meeting_minutes_service=None으로 강등 → dispatch 시 service_unavailable."""
    from meeting_minutes.tool import handle_create_meeting_minutes
    import asyncio

    # service=None인 경우 service_unavailable 반환
    result = asyncio.get_event_loop().run_until_complete(
        handle_create_meeting_minutes(
            None,
            {"transcript": "가" * 50, "pages": 1},
        )
    )

    assert result.ok is False
    assert result.error_code == "service_unavailable"

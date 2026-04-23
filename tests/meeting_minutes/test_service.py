# tests/meeting_minutes/test_service.py
"""M_13 MeetingMinutes Service 테스트 (N-5, A-2)."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meeting_minutes.errors import MeetingFileNotFoundError
from meeting_minutes.service import MeetingMinutesService


# ── 정상 케이스 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_returns_file_id_and_url(
    service: MeetingMinutesService,
    temp_dir: Path,
) -> None:
    """N: generate() → file_id와 download_url을 포함하는 dict 반환."""
    result = await service.generate("농업 회의 녹취록 " * 10, 1)

    assert "file_id" in result
    assert "download_url" in result
    assert "expires_at" in result
    assert result["download_url"].startswith("http://127.0.0.1:12393/download/")
    # UUIDv4 형식 확인
    uuid.UUID(result["file_id"], version=4)
    # 파일 실제 생성 확인
    out_path = temp_dir / f"{result['file_id']}.hwpx"
    assert out_path.exists()


@pytest.mark.asyncio
async def test_n5_cleanup_expired(
    fake_agent: MagicMock,
    template_path: Path,
    tmp_path: Path,
) -> None:
    """N-5: cleanup_expired → 25h 파일 1개만 삭제, 23h·1h 파일은 유지."""
    temp_dir = tmp_path / "cleanup_test"
    temp_dir.mkdir()

    svc = MeetingMinutesService(
        agent=fake_agent,
        template_path=template_path,
        temp_dir=temp_dir,
        download_base_url="http://127.0.0.1:12393",
        ttl_hours=24,
    )

    now = time.time()

    # 25시간 전 파일 (만료)
    old_file = temp_dir / f"{uuid.uuid4()}.hwpx"
    old_file.write_bytes(b"old")
    os.utime(old_file, (now - 25 * 3600, now - 25 * 3600))

    # 23시간 전 파일 (유지)
    recent_file = temp_dir / f"{uuid.uuid4()}.hwpx"
    recent_file.write_bytes(b"recent")
    os.utime(recent_file, (now - 23 * 3600, now - 23 * 3600))

    # 1시간 전 파일 (유지)
    new_file = temp_dir / f"{uuid.uuid4()}.hwpx"
    new_file.write_bytes(b"new")
    os.utime(new_file, (now - 3600, now - 3600))

    deleted = svc.cleanup_expired()

    assert deleted == 1
    assert not old_file.exists()
    assert recent_file.exists()
    assert new_file.exists()


def test_resolve_valid_file_id(
    service: MeetingMinutesService,
    temp_dir: Path,
) -> None:
    """N: resolve() → 유효한 file_id로 Path 반환."""
    file_id = str(uuid.uuid4())
    dummy_file = temp_dir / f"{file_id}.hwpx"
    dummy_file.write_bytes(b"dummy hwpx")

    result = service.resolve(file_id)
    assert result == dummy_file


def test_resolve_expired_file(
    fake_agent: MagicMock,
    template_path: Path,
    tmp_path: Path,
) -> None:
    """N: resolve()에서 만료된 파일은 MeetingFileNotFoundError."""
    temp_dir = tmp_path / "resolve_test"
    temp_dir.mkdir()

    svc = MeetingMinutesService(
        agent=fake_agent,
        template_path=template_path,
        temp_dir=temp_dir,
        download_base_url="http://127.0.0.1:12393",
        ttl_hours=1,
    )

    file_id = str(uuid.uuid4())
    dummy_file = temp_dir / f"{file_id}.hwpx"
    dummy_file.write_bytes(b"dummy")
    # 2시간 전으로 mtime 설정 (TTL 1시간 초과)
    now = time.time()
    os.utime(dummy_file, (now - 7200, now - 7200))

    with pytest.raises(MeetingFileNotFoundError):
        svc.resolve(file_id)


@pytest.mark.asyncio
async def test_aclose_no_error(service: MeetingMinutesService) -> None:
    """N: aclose() 호출 시 예외 없이 완료."""
    await service.aclose()  # 예외 없이 실행돼야 함


# ── 적대적 케이스 ──────────────────────────────────────────────


def test_a2_path_traversal(service: MeetingMinutesService) -> None:
    """A-2: path traversal 시도 → ValueError."""
    with pytest.raises(ValueError):
        service.resolve("../../etc/passwd")


def test_a2_invalid_uuid_format(service: MeetingMinutesService) -> None:
    """A-2: 잘못된 UUID 형식 → ValueError."""
    with pytest.raises(ValueError):
        service.resolve("not-a-uuid-at-all")


def test_a2_nonexistent_valid_uuid(service: MeetingMinutesService) -> None:
    """A-2: UUIDv4 형식이지만 존재하지 않는 파일 → MeetingFileNotFoundError."""
    nonexistent_id = "00000000-0000-4000-a000-000000000000"
    with pytest.raises(MeetingFileNotFoundError):
        service.resolve(nonexistent_id)


def test_a2_no_temp_dir_escape(
    service: MeetingMinutesService,
    temp_dir: Path,
) -> None:
    """A-2: resolve()는 항상 temp_dir 내부 경로만 반환한다."""
    file_id = str(uuid.uuid4())
    dummy_file = temp_dir / f"{file_id}.hwpx"
    dummy_file.write_bytes(b"safe")

    result = service.resolve(file_id)
    # 결과 경로가 temp_dir 하위여야 함
    assert str(result).startswith(str(temp_dir))


def test_cleanup_ignores_non_hwpx(
    fake_agent: MagicMock,
    template_path: Path,
    tmp_path: Path,
) -> None:
    """N: cleanup_expired는 .hwpx 이외의 파일은 삭제하지 않는다."""
    temp_dir = tmp_path / "cleanup_test2"
    temp_dir.mkdir()

    svc = MeetingMinutesService(
        agent=fake_agent,
        template_path=template_path,
        temp_dir=temp_dir,
        download_base_url="http://127.0.0.1:12393",
        ttl_hours=24,
    )

    now = time.time()
    txt_file = temp_dir / "old_file.txt"
    txt_file.write_bytes(b"not hwpx")
    os.utime(txt_file, (now - 25 * 3600, now - 25 * 3600))

    deleted = svc.cleanup_expired()
    assert deleted == 0
    assert txt_file.exists()

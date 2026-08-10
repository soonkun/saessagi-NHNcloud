# tests/app/test_calendar_end_time.py
"""CR-70 일정 등록을 시작/종료 시각으로.

저장은 여전히 `duration_minutes`다(DB·도구 인자를 그대로 두는 쪽이 안전하다).
입력만 종료 시각으로 받아 여기서 분으로 환산한다 — 사람은 "9시~18시"로 생각하고
"540분"으로 생각하지 않는다.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.calendar_routes import _duration_from


def test_end_time_becomes_minutes() -> None:
    assert _duration_from("2026-08-11T09:00:00+09:00", "2026-08-11T18:00:00+09:00", None) == 540


def test_end_wins_over_duration() -> None:
    """둘 다 오면 종료 시각이 이긴다 — 사용자가 고른 쪽이 그쪽이다."""
    assert _duration_from("2026-08-11T09:00:00", "2026-08-11T10:30:00", 999) == 90


def test_falls_back_to_duration() -> None:
    assert _duration_from("2026-08-11T09:00:00", None, 45) == 45


def test_defaults_to_one_hour() -> None:
    """둘 다 없으면 1시간 — 옛 API 기본값과 같다."""
    assert _duration_from("2026-08-11T09:00:00", None, None) == 60


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(HTTPException) as e:
        _duration_from("2026-08-11T18:00:00", "2026-08-11T09:00:00", None)
    assert e.value.status_code == 422


def test_same_time_is_rejected() -> None:
    """길이 0분 일정은 만들 수 없다."""
    with pytest.raises(HTTPException):
        _duration_from("2026-08-11T09:00:00", "2026-08-11T09:00:00", None)


def test_bad_end_format_is_422() -> None:
    with pytest.raises(HTTPException) as e:
        _duration_from("2026-08-11T09:00:00", "오후 여섯시", None)
    assert e.value.status_code == 422

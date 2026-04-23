# tests/meeting_minutes/test_schema.py
"""M_13 MeetingMinutes JSON Schema 검증 테스트 (E-1 ~ E-4)."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from meeting_minutes.schemas import CREATE_MEETING_MINUTES_SCHEMA, MEETING_DRAFT_SCHEMA


@pytest.fixture
def draft_validator() -> Draft202012Validator:
    return Draft202012Validator(MEETING_DRAFT_SCHEMA)


@pytest.fixture
def tool_validator() -> Draft202012Validator:
    return Draft202012Validator(CREATE_MEETING_MINUTES_SCHEMA["function"]["parameters"])


# ── 정상 케이스 ──────────────────────────────────────────────


def test_valid_1page_draft_passes(
    valid_draft_dict_1page: dict, draft_validator: Draft202012Validator
) -> None:
    """N: 유효한 1장 draft dict가 스키마를 통과한다."""
    errors = list(draft_validator.iter_errors(valid_draft_dict_1page))
    assert errors == [], f"Unexpected errors: {errors}"


def test_valid_2page_draft_passes(
    valid_draft_dict_2page: dict, draft_validator: Draft202012Validator
) -> None:
    """N: 유효한 2장 draft dict가 스키마를 통과한다."""
    errors = list(draft_validator.iter_errors(valid_draft_dict_2page))
    assert errors == [], f"Unexpected errors: {errors}"


def test_tool_schema_valid_args(tool_validator: Draft202012Validator) -> None:
    """N: create_meeting_minutes tool 스키마 정상 인자 통과."""
    args = {"transcript": "가" * 50, "pages": 1}
    errors = list(tool_validator.iter_errors(args))
    assert errors == []


# ── 엣지 케이스 (E) ──────────────────────────────────────────


def test_e1_transcript_exactly_50_chars(tool_validator: Draft202012Validator) -> None:
    """E-1: transcript 정확히 50자 → 스키마 통과."""
    args = {"transcript": "가" * 50, "pages": 1}
    errors = list(tool_validator.iter_errors(args))
    assert errors == []


def test_e2_transcript_exactly_50000_chars(tool_validator: Draft202012Validator) -> None:
    """E-2: transcript 정확히 50000자 → 스키마 통과."""
    args = {"transcript": "나" * 50000, "pages": 2}
    errors = list(tool_validator.iter_errors(args))
    assert errors == []


def test_e3_attendees_100_items(
    draft_validator: Draft202012Validator, valid_draft_dict_1page: dict
) -> None:
    """E-3: attendees 100명 → maxItems=100 통과."""
    draft = dict(valid_draft_dict_1page)
    draft["attendees"] = [f"참석자{i:03d}" for i in range(100)]
    errors = list(draft_validator.iter_errors(draft))
    assert errors == []


def test_e4_empty_next_steps(
    draft_validator: Draft202012Validator, valid_draft_dict_1page: dict
) -> None:
    """E-4: next_steps=0개 빈 배열 허용."""
    draft = dict(valid_draft_dict_1page)
    draft["next_steps"] = []
    errors = list(draft_validator.iter_errors(draft))
    assert errors == []


# ── 실패 케이스 ──────────────────────────────────────────────


def test_transcript_too_short_fails(tool_validator: Draft202012Validator) -> None:
    """transcript 49자 → minLength 위반."""
    args = {"transcript": "가" * 49, "pages": 1}
    errors = list(tool_validator.iter_errors(args))
    assert len(errors) > 0


def test_pages_invalid_value_fails(tool_validator: Draft202012Validator) -> None:
    """pages=3 → enum 위반."""
    args = {"transcript": "가" * 50, "pages": 3}
    errors = list(tool_validator.iter_errors(args))
    assert len(errors) > 0


def test_missing_required_field_fails(
    draft_validator: Draft202012Validator, valid_draft_dict_1page: dict
) -> None:
    """필수 필드 title 누락 → 스키마 위반."""
    draft = dict(valid_draft_dict_1page)
    del draft["title"]
    errors = list(draft_validator.iter_errors(draft))
    assert len(errors) > 0


def test_date_invalid_format_fails(
    draft_validator: Draft202012Validator, valid_draft_dict_1page: dict
) -> None:
    """date 포맷 불일치(마지막 점 누락) → pattern 위반."""
    draft = dict(valid_draft_dict_1page)
    draft["date"] = "2026.04.23"  # 마지막 '.' 누락
    errors = list(draft_validator.iter_errors(draft))
    assert len(errors) > 0


def test_additional_property_fails(
    draft_validator: Draft202012Validator, valid_draft_dict_1page: dict
) -> None:
    """additionalProperties 위반 → 스키마 실패."""
    draft = dict(valid_draft_dict_1page)
    draft["unknown_field"] = "extra"
    errors = list(draft_validator.iter_errors(draft))
    assert len(errors) > 0

# src/meeting_minutes/errors.py
"""M_13 MeetingMinutes 모듈 에러 클래스 6종."""

from __future__ import annotations


class MeetingMinutesError(Exception):
    """본 모듈 최상위 예외."""


class MeetingDraftError(MeetingMinutesError):
    """LLM 호출 자체 실패(타임아웃, 비-JSON 응답 등). max_retries 소진 후 raise."""


class MeetingDraftValidationError(MeetingMinutesError, ValueError):
    """LLM 응답이 JSON Schema 위반. ValueError 다중 상속으로 호출자 except ValueError 호환."""


class HwpxTemplateError(MeetingMinutesError):
    """템플릿 파일 부재·ZIP 손상·section0.xml 누락 등 기동 실패 사유."""


class HwpxWriteError(MeetingMinutesError):
    """런타임 lxml 파싱 실패·인코딩 오류·디스크 I/O 실패."""


class MeetingFileNotFoundError(MeetingMinutesError):
    """resolve()에서 file_id 미존재 또는 TTL 초과. FastAPI에서 404로 변환."""

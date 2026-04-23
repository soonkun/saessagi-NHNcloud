# src/meeting_minutes/types.py
"""M_13 MeetingMinutes 데이터 타입 정의."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PageCount = Literal[1, 2]


@dataclass(frozen=True, slots=True)
class SubItem:
    """- 부연설명 + 선택적 * 세부사항.

    text: '- ' 접두사 없는 본문 문자열, 35~37자(2줄 시 73자) 가이드.
    detail: '* ' 접두사 없는 본문 문자열, 40~43자 가이드, None 가능.
    """

    text: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryItem:
    """○ 주요내용 항목 (개요 섹션).

    text: '○ ' 접두사 없는 본문 문자열, 35~37자(2줄 시 73자).
    subs: 부연설명 0~2개 (○당 최대 2개 가이드).
    """

    text: str
    subs: tuple[SubItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DetailItem:
    """○ 세부내용 항목 (세부내용 섹션). SummaryItem과 구조 동일."""

    text: str
    subs: tuple[SubItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class NextStepItem:
    """향후계획 1항목.

    text: '○ ' 접두사 없는 본문 문자열.
    date: 'M.DD.' 형식(예: '4.30.'). 빈 문자열 가능.
    """

    text: str
    date: str = ""


@dataclass(frozen=True, slots=True)
class MeetingDraft:
    """LLM이 생성하는 개조식 회의록 초안.

    - 모든 문자열은 strip 완료 상태(LLM 응답 정규화 후).
    - subs/detail_items/next_steps는 빈 시퀀스 허용. 단 §7.3 분량 가드가 검사.
    """

    title: str
    date: str  # 'YYYY.MM.DD.' 형식
    department: str  # 소속과
    place: str  # 회의 장소
    attendees: tuple[str, ...]  # 참석자 이름 목록
    datetime_place: str  # '2026.04.23.(수) 14:00~15:30, 회의실'
    attendees_str: str  # '홍길동, 이순신 등 5명'
    summary_items: tuple[SummaryItem, ...]
    detail_items: tuple[DetailItem, ...]
    next_steps: tuple[NextStepItem, ...]
    pages: PageCount

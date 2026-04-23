# tests/meeting_minutes/conftest.py
"""M_13 MeetingMinutes 테스트 픽스처."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

TEMPLATE_PATH = Path("data/Template/회의 결과보고 템플릿.hwpx")
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def template_path() -> Path:
    return TEMPLATE_PATH


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    d = tmp_path / "meeting_temp"
    d.mkdir()
    return d


@pytest.fixture
def valid_draft_dict_1page() -> dict:
    # total_lines = sum(1 + len(subs) + sum(1 for s in subs if s.get("detail")) for all_items)
    # summary: (1+1+0) + (1+1+1) + (1+0+0) = 2+3+1 = 6
    # detail:  (1+1+0) + (1+0+0)             = 2+1   = 3
    # total = 9 lines (한도 14줄 이내 ✓)
    return {
        "title": "2026년 4월 농업정책 주간 회의 결과",
        "date": "2026.04.23.",
        "department": "농업정책과",
        "place": "3층 회의실",
        "attendees": ["홍길동", "김철수", "이영희"],
        "datetime_place": "2026.04.23.(목) 14:00~15:00, 3층 회의실",
        "attendees_str": "홍길동 과장 외 2명",
        "summary_items": [
            {
                "text": "스마트팜 보급 확대 추진 계획 논의",
                "subs": [
                    {"text": "총 예산 50억 원, 전국 20개 시군 대상 시범"},
                ],
            },
            {
                "text": "농업인 교육 프로그램 개편 방향 검토",
                "subs": [
                    {
                        "text": "온라인 교육 비중 30%에서 50%로 상향 결정",
                        "detail": "하반기 시범 운영 후 전면 전환 예정",
                    },
                ],
            },
            {
                "text": "병해충 예찰 드론 활용 방안 합의",
                "subs": [],
            },
        ],
        "detail_items": [
            {
                "text": "스마트팜 공모 일정 확정",
                "subs": [
                    {"text": "4.25. 공모 공고, 5.15. 신청 마감"},
                ],
            },
            {
                "text": "교육 개편안 수립 지시",
                "subs": [],
            },
        ],
        "next_steps": [
            {"text": "스마트팜 공모 공고문 작성 및 배포", "date": "4.25."},
            {"text": "교육 프로그램 개편안 1차 초안 제출", "date": "5.31."},
        ],
    }


@pytest.fixture
def valid_draft_dict_2page() -> dict:
    return {
        "title": "2026년 4월 농업정책과 월간 종합 업무보고 회의 결과",
        "date": "2026.04.20.",
        "department": "농업정책과",
        "place": "본관 5층 대강당",
        "attendees": [
            "홍길동",
            "김철수",
            "이영희",
            "박민수",
            "최지현",
            "강동원",
            "윤서연",
            "정상훈",
        ],
        "datetime_place": "2026.04.20.(월) 10:00~12:00, 본관 5층 대강당",
        "attendees_str": "홍길동 과장 외 7명",
        "summary_items": [
            {
                "text": "스마트팜 보급 사업 총예산 50억 원 편성, 20개 시군 대상",
                "subs": [
                    {"text": "4.25. 공모 공고, 5.15. 신청 마감, 6월 선정"},
                    {
                        "text": "청년 농업인(만 39세 이하) 가점 10점 부여",
                        "detail": "1ha 이상 농지 보유자 우선 선발",
                    },
                ],
            },
            {
                "text": "농업인 교육 온라인 비중 30%→50% 상향 추진",
                "subs": [
                    {"text": "5.31. 개편안 1차 초안 제출, 7월 시범 운영"},
                ],
            },
            {
                "text": "드론 활용 병해충 예찰 시범 사업 대상지 3곳 선정",
                "subs": [
                    {"text": "경기 안성·충남 논산·전북 김제 선정"},
                ],
            },
            {
                "text": "동남아 3개국 농산물 수출 현지 수요 조사 추진",
                "subs": [
                    {"text": "5월 중 파견단 10명 구성, 7월 수출 상담회 개최"},
                ],
            },
            {
                "text": "친환경 직불금 신청 2,847호 완료, 전년 대비 12% 증가",
                "subs": [
                    {"text": "예산 집행율 28%, 5월 집중 신청 기간 운영"},
                ],
            },
        ],
        "detail_items": [
            {
                "text": "스마트팜 사업 공모 공고 준비 완료, 4.25. 게시 예정",
                "subs": [
                    {"text": "47건 사전 문의 접수, 시군 담당자 유선 협의 완료"},
                ],
            },
            {
                "text": "교육 개편안 단계별 구성, 기초 과정부터 신설",
                "subs": [
                    {"text": "고령 농업인 스마트폰 기초 과정 포함 예정"},
                ],
            },
            {
                "text": "드론 조종자 자격증 취득 교육 4월 말까지 지원",
                "subs": [
                    {"text": "실시간 예찰 앱 도입, 주간 보고서 제공 계획"},
                ],
            },
            {
                "text": "수출 조사 냉동·냉장 유통망 현황 포함 추가",
                "subs": [],
            },
            {
                "text": "청년 농업인 창업 지원 50명 선발, 여성 40% 목표",
                "subs": [
                    {"text": "4.30. 신청 마감, 5월 서류 심사, 6월 면접"},
                    {
                        "text": "창업 자금 최대 3천만 원, 멘토 2년 배정",
                        "detail": "농업 경영 교육 30시간 포함",
                    },
                ],
            },
            {
                "text": "농촌 빈집 200동 정비, 철거 150동·활용 50동 분류",
                "subs": [
                    {"text": "철거비 동당 500만 원 지원, 부지 공동 텃밭 활용"},
                ],
            },
        ],
        "next_steps": [
            {"text": "스마트팜 공모 공고문 작성 및 게시", "date": "4.25."},
            {"text": "청년 농업인 창업 지원 신청 마감", "date": "4.30."},
            {"text": "교육 프로그램 개편안 1차 초안 제출", "date": "5.31."},
        ],
    }


@pytest.fixture
def fake_agent(valid_draft_dict_1page: dict) -> MagicMock:
    agent = MagicMock()
    agent.complete_json = AsyncMock(return_value=valid_draft_dict_1page)
    return agent


@pytest.fixture
def service(
    fake_agent: MagicMock,
    template_path: Path,
    temp_dir: Path,
) -> object:
    from meeting_minutes.service import MeetingMinutesService

    return MeetingMinutesService(
        agent=fake_agent,
        template_path=template_path,
        temp_dir=temp_dir,
        download_base_url="http://127.0.0.1:12393",
    )

# src/meeting_minutes/prompts.py
"""M_13 MeetingMinutes LLM 프롬프트 템플릿."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
당신은 사내 공문서 표준에 맞는 '회의 결과 보고서'의 개조식 초안을 작성하는 전문가입니다.

## 보고서 필수 구성 요소

1. 제목 — 회의 주제를 명확히 반영
2. 일시 및 장소 — 녹취록에서 추정 가능한 경우 기재 (불명확하면 빈값 허용)
3. 참석자 — 녹취록에서 추정 가능한 경우 기재 (불명확하면 빈값 허용)
4. 목적 — summary_items의 첫 ○ 항목에 회의 개최 목적 작성
5. 주요내용 — 회의에서 논의된 내용과 의결된 사항을 ○/-/* 위계 구조로 정리
6. 향후계획 — 향후 조치 사항과 일정

## 분량 기준

- 1페이지: 주요내용 ○ 항목 합계 3~4개 / 향후계획 1~2개
- 2페이지: 주요내용 ○ 항목 합계 8~9개 / 향후계획 2~3개

## 위계별 작성 규칙

### ○ 주요내용 꼭지
- 회의에서 논의·결정된 주요 사항 1건을 개조식으로 작성
- 글자수 기준 (띄어쓰기 포함):
  - 한 줄로 쓸 경우: 35~37자
  - 내용이 길어 두 줄이 필요한 경우: 70~72자
- 서술형 문장 금지 (예: "~를 논의했습니다" X → "~를 논의" O)

### - 부연설명 꼭지
- ○ 항목의 배경·조건·방법·제한 사항 등 부연 설명이 필요한 경우에만 작성
- 한 줄을 넘기지 않음. 글자수(띄어쓰기 포함) 35~37자
- ○당 최대 2개. 불필요하면 생략

### * 구체적 근거·세부사항 꼭지
- ○ 또는 - 내용의 구체적 근거 또는 세부 사항 기재
- 반드시 다음 중 하나 이상 포함: 상세 내용 설명, 날짜·기간 등 일정, 금액·비율·수량 등 정량적 수치
- 가능하면 반드시 작성. ○당 최대 2개. 구체적 근거가 전혀 없는 경우에만 생략

## 공통 규칙

1. 조사 생략으로 의미가 왜곡되어선 안 됨
   - 잘못된 예: "예산 승인", "팀장 보고", "일정 확인"
   - 올바른 예: "예산을 승인", "팀장에게 보고", "일정을 확인"
2. 텍스트 필드에 '○ ', '- ', '* ' 기호 포함 금지. 위계는 JSON 구조로만 표현
3. 출력은 지정된 JSON 구조만 사용. 마크다운·자연어 설명 절대 금지
4. 날짜 형식: 'YYYY.MM.DD.' (마지막 마침표 포함). 향후계획 date는 'M.DD.' 또는 빈 문자열
5. 지정된 JSON 키만 사용. 임의 키 추가 금지
   최상위: title, date, department, place, attendees, datetime_place, attendees_str,
           summary_items, detail_items, next_steps
   summary_items/detail_items 각 원소: text, subs(선택)
   subs 각 원소: text, detail(선택)
   next_steps 각 원소: text, date(선택)\
"""

USER_PROMPT_TEMPLATE = """\
다음은 회의 녹취록 또는 이미 개조식으로 정리된 회의록 텍스트입니다. \
이를 {pages}장 분량의 개조식 회의 결과 보고서 JSON으로 정리해 주세요.

입력이 이미 정리된 회의록이면 **재요약하지 말고** 항목·수치·날짜·담당자를 \
그대로 보존하여 JSON 구조로 옮기세요. ○/-/* 위계가 이미 있으면 그 위계를 따르세요.

오늘 날짜: {today_date} (입력에 날짜가 없으면 이 날짜를 사용하세요.)

분량 목표:
{volume_guide}

입력:
'''
{transcript}
'''

아래 형식으로 JSON만 출력하세요. 다른 텍스트는 절대 출력하지 마세요.
{{
  "title": "회의명",
  "date": "{today_date}",
  "department": "주관부서명 (불명확하면 빈 문자열)",
  "place": "회의 장소 (불명확하면 빈 문자열)",
  "attendees": ["참석자1", "참석자2"],
  "datetime_place": "{today_date} HH:MM / 장소 (불명확하면 빈 문자열)",
  "attendees_str": "참석자1, 참석자2 (불명확하면 빈 문자열)",
  "summary_items": [
    {{
      "text": "○ 주요내용 꼭지 (35~37자, 두줄이면 70~72자)",
      "subs": [
        {{
          "text": "- 부연설명 꼭지 (35~37자)",
          "detail": "* 구체적 근거·일정·수치 (생략 가능)"
        }}
      ]
    }}
  ],
  "detail_items": [
    {{
      "text": "○ 세부내용 꼭지 (35~37자)",
      "subs": []
    }}
  ],
  "next_steps": [{{"text": "향후 조치사항 (개조식)", "date": "M.DD."}}]
}}
"""

VOLUME_GUIDE_1PAGE = """\
- summary_items + detail_items 합계: ○ 3~4개 (각 ○당 - 0~2개, * 0~2개)
- next_steps: 1~2개\
"""

VOLUME_GUIDE_2PAGE = """\
- summary_items + detail_items 합계: ○ 8~9개 (각 ○당 - 0~2개, * 0~2개)
- next_steps: 2~3개\
"""

# ─── 청크 요약 (긴 녹취록 분할 처리용) ────────────────────────────────────

CHUNK_SUMMARY_SYSTEM_PROMPT = """\
회의 녹취록 일부를 읽고 핵심 내용을 짧은 글머리 목록(JSON)으로 추출하는 역할입니다.
출력은 반드시 JSON 형식만 사용하세요. 설명이나 서문 없이 JSON만 출력하세요.\
"""

CHUNK_SUMMARY_USER_TEMPLATE = """\
다음은 회의 녹취의 {chunk_idx}/{total_chunks} 구간입니다.
핵심 내용을 5~8개의 짧은 글머리로 요약해 JSON points 배열로 반환하세요.
각 항목은 30자 이내 개조식 한국어로 작성하세요.

녹취 구간:
'''
{chunk}
'''

JSON만 출력하세요.\
"""

CHUNK_BULLETS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["points"],
    "properties": {
        "points": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 100},
            "minItems": 1,
            "maxItems": 12,
        }
    },
}

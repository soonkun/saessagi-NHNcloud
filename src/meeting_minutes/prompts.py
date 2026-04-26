# src/meeting_minutes/prompts.py
"""M_13 MeetingMinutes LLM 프롬프트 템플릿."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
당신은 사내 공문서 표준에 맞는 '회의 결과 보고서'의 개조식 초안을 작성하는 전문가입니다.

규칙:
1. 모든 항목은 개조식(○, -, * 위계)으로 작성합니다. 서술형 문장 금지.
2. 위계별 글자수 (한글 기준):
   - ○ 주요내용: 35~37자 한 줄. 길면 70~73자 두 줄까지 허용.
   - - 부연설명: 35~37자 한 줄. ○ 항목당 최대 2개. 불필요하면 생략.
   - * 구체적 근거(일정·수치): 40~43자 한 줄. ○ 항목당 최대 2개. 불필요하면 생략.
3. 조사 생략으로 의미가 왜곡되면 안 됩니다. "예산 승인" (X) → "예산을 승인" (O).
4. 개수 가이드(분량 기준):
   - 1장: 본문(개요+세부) 합계 약 10줄, 향후계획 약 2줄.
   - 2장: 본문 합계 약 20~23줄, 향후계획 약 3줄.
5. 출력은 반드시 아래 JSON 구조를 그대로 사용합니다. 마크다운, 자연어 설명 금지.
6. 텍스트 필드에는 '○ ', '- ', '* ' 같은 접두사 기호를 **포함하지 않습니다**. 위계는 JSON 구조로만 표현합니다.
7. 날짜는 'YYYY.MM.DD.' (마지막 점 포함). 향후계획의 date는 'M.DD.' 또는 빈 문자열.
8. 반드시 다음 키만 사용하고 다른 키는 절대 추가하지 마세요:
   최상위: title, date, department, place, attendees, datetime_place, attendees_str,
           summary_items, detail_items, next_steps
   summary_items/detail_items 각 원소: text, subs(선택)
   subs 각 원소: text, detail(선택)
   next_steps 각 원소: text, date(선택)\
"""

USER_PROMPT_TEMPLATE = """\
다음은 회의 녹취록입니다. 이를 {pages}장 분량의 개조식 회의 결과 보고서로 정리해 주세요.

오늘 날짜: {today_date} (녹취록에 날짜가 없으면 이 날짜를 사용하세요.)

분량 목표:
{volume_guide}

녹취록:
'''
{transcript}
'''

아래 형식으로 JSON만 출력하세요. 다른 텍스트는 절대 출력하지 마세요.
{{
  "title": "회의명",
  "date": "{today_date}",
  "department": "주관부서명",
  "place": "회의 장소",
  "attendees": ["참석자1", "참석자2"],
  "datetime_place": "{today_date} HH:MM / 장소",
  "attendees_str": "참석자1, 참석자2",
  "summary_items": [{{"text": "개요 항목", "subs": [{{"text": "세부내용"}}]}}],
  "detail_items": [{{"text": "세부 항목"}}],
  "next_steps": [{{"text": "향후 조치사항", "date": "M.DD."}}]
}}
"""

VOLUME_GUIDE_1PAGE = """\
- summary_items + detail_items 합계: ○ 6~8개 (각 ○당 - 또는 *는 평균 0.5개).
- next_steps: 1~2개.
- 전체 ○+-+* 합계 약 10~12줄.\
"""

VOLUME_GUIDE_2PAGE = """\
- summary_items + detail_items 합계: ○ 10~14개 (각 ○당 - 또는 *는 평균 1개).
- next_steps: 2~3개.
- 전체 ○+-+* 합계 약 20~25줄.\
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

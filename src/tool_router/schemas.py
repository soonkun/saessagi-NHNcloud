# src/tool_router/schemas.py
"""M_05b 4개 툴 JSON Schema 상수 (OpenAI function-calling 형식, Draft 2020-12)."""

from typing import Any

from meeting_minutes.schemas import CREATE_MEETING_MINUTES_SCHEMA

ADD_EVENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "add_event",
        "description": (
            "일정을 달력에 등록합니다. 자연어 시간 표현은 반드시 ISO 8601 문자열"
            "(예: 2026-04-20T15:00:00+09:00)로 변환해서 전달하세요. 시간대가 없으면 Asia/Seoul로 해석됩니다."
        ),
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "start", "duration_minutes"],
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "일정 제목.",
                },
                "start": {
                    "type": "string",
                    "format": "date-time",
                    "minLength": 10,
                    "maxLength": 40,
                    "description": "ISO 8601 시작 시각. 예: 2026-04-20T15:00:00+09:00.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                    "description": "일정 길이(분). 1~1440.",
                },
                "description": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "선택. 일정 상세 설명.",
                },
            },
        },
    },
}

GET_EVENTS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_events",
        "description": (
            "지정한 날짜 범위의 일정을 조회합니다. start와 end는 ISO 8601 문자열이며"
            " end는 start 이상이어야 합니다."
        ),
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["start", "end"],
            "properties": {
                "start": {
                    "type": "string",
                    "format": "date-time",
                    "minLength": 10,
                    "maxLength": 40,
                },
                "end": {
                    "type": "string",
                    "format": "date-time",
                    "minLength": 10,
                    "maxLength": 40,
                },
            },
        },
    },
}

SEARCH_DOCS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_docs",
        "description": (
            "사내 등록 문서에서 관련 구절을 검색하고 인용 문자열과 함께 반환합니다."
            " 관련 문서가 없으면 found=false로 표시됩니다."
        ),
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "자연어 질의.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 8,
                    "description": "상위 몇 개 청크를 반환할지.",
                },
                "category": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "선택. 상위 폴더명 기반 카테고리 필터(예: 규정, 매뉴얼).",
                },
            },
        },
    },
}

TAKE_SCREENSHOT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "take_screenshot",
        "description": (
            "현재 화면을 캡처해 LLM의 비전 입력으로 전달합니다. continuous=true로 설정하면"
            " interval_seconds 간격으로 연속 캡처를 시작합니다."
        ),
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "continuous": {
                    "type": "boolean",
                    "default": False,
                    "description": "true면 연속 캡처 모드 진입.",
                },
                "interval_seconds": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 60.0,
                    "default": 5.0,
                    "description": "연속 모드 캡처 주기(초). continuous=false일 때 무시됨.",
                },
            },
        },
    },
}

ALL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    ADD_EVENT_SCHEMA,
    GET_EVENTS_SCHEMA,
    SEARCH_DOCS_SCHEMA,
    TAKE_SCREENSHOT_SCHEMA,
    CREATE_MEETING_MINUTES_SCHEMA,
]

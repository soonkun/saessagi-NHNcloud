# src/meeting_minutes/schemas.py
"""M_13 MeetingMinutes JSON Schema 상수 (Draft 2020-12)."""

from __future__ import annotations

from typing import Any

MEETING_DRAFT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "date",
        "department",
        "place",
        "attendees",
        "datetime_place",
        "attendees_str",
        "summary_items",
        "detail_items",
        "next_steps",
    ],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 100},
        "date": {
            "type": "string",
            "pattern": r"^\d{4}\.\d{2}\.\d{2}\.$",
            "description": "YYYY.MM.DD. 형식, 마지막 점 포함.",
        },
        "department": {"type": "string", "maxLength": 100},
        "place": {"type": "string", "maxLength": 100},
        "attendees": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 50},
            "minItems": 0,
            "maxItems": 100,
        },
        "datetime_place": {"type": "string", "maxLength": 200},
        "attendees_str": {"type": "string", "maxLength": 200},
        "summary_items": {
            "type": "array",
            "items": {"$ref": "#/$defs/Item"},
            "minItems": 0,
            "maxItems": 10,
        },
        "detail_items": {
            "type": "array",
            "items": {"$ref": "#/$defs/Item"},
            "minItems": 0,
            "maxItems": 15,
        },
        "next_steps": {
            "type": "array",
            "items": {"$ref": "#/$defs/NextStep"},
            "minItems": 0,
            "maxItems": 10,
        },
    },
    "$defs": {
        "Item": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 80},
                "subs": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Sub"},
                    "maxItems": 2,
                    "default": [],
                },
            },
        },
        "Sub": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 80},
                "detail": {"type": "string", "maxLength": 90},
            },
        },
        "NextStep": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 80},
                "date": {"type": "string", "pattern": r"^(\d{1,2}\.\d{1,2}\.|)$"},
            },
        },
    },
}

CREATE_MEETING_MINUTES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_meeting_minutes",
        "description": (
            "녹취록을 받아 한글(HWPX) 회의 결과 보고서를 자동 생성하고 다운로드 URL을 반환합니다."
            " transcript는 회의 녹취록 전체 텍스트, pages는 보고서 분량(1 또는 2장)입니다."
            " 사용자에게 페이지 수를 먼저 물어본 후 호출하세요."
        ),
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["transcript", "pages"],
            "properties": {
                "transcript": {
                    "type": "string",
                    "minLength": 50,
                    "maxLength": 50000,
                    "description": "회의 녹취록 전체 텍스트. 50자 미만은 회의로 보지 않음.",
                },
                "pages": {
                    "type": "integer",
                    "enum": [1, 2],
                    "description": "보고서 분량. 1장(~20줄) 또는 2장(~40줄).",
                },
            },
        },
    },
}

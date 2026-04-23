# src/tool_router/types.py
"""M_05b ToolRouter 데이터 타입 정의."""

from dataclasses import dataclass, field
from typing import Any, Literal

ToolSpec = dict[str, Any]  # OpenAI function-calling JSON schema

ToolErrorCode = Literal[
    "unknown_tool",
    "invalid_arguments",
    "service_unavailable",
    "handler_exception",
    "screenshot_failed",
    "continuous_already_running",
    "continuous_not_running",
    "invalid_llm_response",
    "schema_violation",
    "hwpx_write_failed",
]


@dataclass(frozen=True)
class ToolResult:
    """ToolRouter.dispatch의 유일한 반환 타입."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = (
        None  # ok=False일 때만 채운다. 사람이 읽을 수 있는 한국어 또는 jsonschema 원문.
    )
    error_code: ToolErrorCode | None = None  # 프로그램 분기용. ok=True일 때는 None.

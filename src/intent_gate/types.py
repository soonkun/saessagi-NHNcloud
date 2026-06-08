# src/intent_gate/types.py
"""M_16 IntentGate 공개 타입 정의."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


# ── IntentLabel — 닫힌 집합(6종) ─────────────────────────────────────────────

IntentLabel = Literal[
    "calendar_add",
    "calendar_query",
    "doc_query",
    "note_save",
    "work_query",
    "chat",
]

ALL_INTENT_LABELS: frozenset[str] = frozenset(
    {
        "calendar_add",
        "calendar_query",
        "doc_query",
        "note_save",
        "work_query",
        "chat",
    }
)

# RAG 검색 소스 필터.
# docs  → 노트 제외 전부  (category IS NULL OR category != '__knowledge__')
# notes → 노트만          (category = '__knowledge__')
# both  → 필터 없음(현행 하이브리드)
RagSource = Literal["docs", "notes", "both"]


@dataclass(frozen=True)
class IntentResult:
    """의도 분류 결과."""

    intent: IntentLabel  # 분류 결과. 실패/저신뢰 시 "chat" 또는 fallback_* source
    confidence: float  # 0.0 ~ 1.0
    reason: str  # 1문장 근거 (로그·디버그용, 최대 200자)
    source: Literal[
        "llm",
        "fallback_lowconf",
        "fallback_error",
        "fallback_disabled",
    ]
    # source != "llm" 이면 라우팅은 "자율 모드"로 폴백


# ── CompleteJsonFn Protocol ────────────────────────────────────────────────────


@runtime_checkable
class CompleteJsonFn(Protocol):
    """GemmaChatAgent.complete_json 과 동일 시그니처의 Protocol.

    DI로 주입함으로써 메인 대화 모델과 **다른** 분류기 전용 모델/클라이언트를
    꽂을 수 있다.
    """

    async def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]: ...

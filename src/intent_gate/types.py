# src/intent_gate/types.py
"""M_16 IntentGate 공개 타입 정의."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


# ── IntentLabel — 닫힌 집합(7종) ─────────────────────────────────────────────
#
# followup은 CR-51에서 추가됐다. 예전에는 정규식 휴리스틱(looks_like_followup)이
# LLM 분류 **이전에** 가로채 처리했는데, "정리해줘"가 들어간 새 질문까지 후속으로 잡아
# RAG를 통째로 건너뛰는 사고가 났다 (E-79). 문맥 판단은 LLM이 할 일이다.

IntentLabel = Literal[
    "calendar_add",
    "calendar_query",
    "doc_query",
    "note_save",
    "work_query",
    "followup",
    "chat",
]

ALL_INTENT_LABELS: frozenset[str] = frozenset(
    {
        "calendar_add",
        "calendar_query",
        "doc_query",
        "note_save",
        "work_query",
        "followup",
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

    # CR-52: **분류기가 직접 판단한** "사내 문서를 새로 찾아봐야 하는가".
    #
    # 예전에는 라벨에서 검색 여부를 코드가 도출했다(followup이면 무조건 검색 안 함).
    # 그 하드코딩 때문에 "구체적으로 농업분야에서 대응할 수 있는 방안은 뭐야?" 같은
    # **심화 질문**이 후속으로 잡히자 문서를 전혀 참조하지 않고 답했다 (E-85).
    # 검색이 필요한지는 문맥을 아는 모델이 판단해야 한다.
    needs_search: bool = False


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

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


# ── 검색 제약 (CR-72) ─────────────────────────────────────────────────────────
#
# "최근 5년 자료로", "완결보고서만" 같은 조건은 **벡터 스토어가 지킬 수 없다** —
# 스키마에 연도·문서종류가 없다(doc_id·doc_name·category·page·section·chunk_id·
# text·bbox·source_path·vector). 그래서 지금까지 그런 조건은 임베딩될 문장의 일부로
# 흘러갈 뿐 아무 데도 적용되지 않았고, 사용자는 걸러진 줄 알고 답을 읽었다.
#
# Neo4j에는 있다(실측 Project·Document 연도 100%, document_type 2종). 제약이 붙으면
# 그래프 경로로만 검색한다.

# M_23 정규 엔티티 유형 (kg/config.py의 ENTITY_TYPE_TO_RELATION과 같은 집합).
ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "RESEARCH_TARGET",
        "TECHNOLOGY",
        "METHOD",
        "OBJECTIVE",
        "RESEARCH_PROBLEM",
        "OUTPUT",
        "DATASET",
    }
)

DOCUMENT_TYPES: frozenset[str] = frozenset({"RFP", "FINAL_REPORT"})


@dataclass(frozen=True)
class RetrievalFilter:
    """질문에 붙은 검색 제약. 전부 선택이고, 비어 있으면 제약 없음."""

    year_from: int | None = None
    year_to: int | None = None
    document_type: str | None = None
    entity_types: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.year_from or self.year_to or self.document_type or self.entity_types)

    @property
    def needs_graph_only(self) -> bool:
        """벡터가 **지킬 수 없는** 제약인가 (CR-72).

        연도·문서종류는 벡터 스토어에 필드 자체가 없어 하이브리드로는 못 지킨다 →
        그래프 전용으로 간다. 엔티티 유형만 있으면 하이브리드를 유지한다 — 유형은
        의미상 벡터도 대략 따라가고, 두 경로 결과가 실측 0% 겹쳐 상호보완이라
        벡터를 버리면 오히려 손해다.
        """
        return bool(self.year_from or self.year_to or self.document_type)

    def describe(self) -> str:
        """사용자에게 보일 한 줄. 필터가 걸린 사실을 모르면 안 된다."""
        parts: list[str] = []
        if self.year_from and self.year_to:
            parts.append(f"{self.year_from}~{self.year_to}년")
        elif self.year_from:
            parts.append(f"{self.year_from}년 이후")
        elif self.year_to:
            parts.append(f"{self.year_to}년 이전")
        if self.document_type:
            parts.append(
                {"RFP": "과제제안요구서", "FINAL_REPORT": "완결보고서"}[self.document_type]
            )
        if self.entity_types:
            parts.append(" · ".join(self.entity_types))
        return " · ".join(parts)


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

    # CR-72: 질문에 붙은 검색 제약("최근 5년", "완결보고서만"). None이면 제약 없음.
    # 모델이 필드를 빠뜨리거나 이상한 값을 내면 None으로 떨어진다 — 기본이 안전한 쪽
    # (제약 없음 = 지금까지의 하이브리드 동작).
    retrieval_filter: "RetrievalFilter | None" = None


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

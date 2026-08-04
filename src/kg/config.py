# src/kg/config.py
"""M_23 지식그래프 파이프라인 설정 (스펙 §7).

임계값·모델·예산을 코드에 상수로 박지 않는다. 지침서가 "모호한 부분은 임의의 대규모 구조
변경으로 해결하지 말고 설정 파일과 인터페이스를 통해 교체 가능하게 구현한다"고 못박았고,
실제로 이 값들은 소규모 테스트 결과를 보고 조정할 것들이다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# 1차 엔티티 유형 (스펙 §5.2). 2차 유형(LOCATION/INDICATOR 등)은 검증 후 확장.
DEFAULT_ENTITY_TYPES: list[str] = [
    "RESEARCH_PROBLEM",
    "OBJECTIVE",
    "RESEARCH_TARGET",
    "TECHNOLOGY",
    "METHOD",
    "DATASET",
    "OUTPUT",
]

# 진술 상태 — 완결보고서의 문장을 전부 실적으로 취급하지 않기 위한 축 (스펙 §5.3)
STATEMENT_STATUSES: frozenset[str] = frozenset(
    {
        "REQUIREMENT",
        "PLANNED",
        "IN_PROGRESS",
        "ACTUAL",
        "RESULT",
        "EXPECTED",
        "PRIOR_RESEARCH",
        "CITATION_ONLY",
        "LIMITATION",
        "UNCERTAIN",
    }
)

PROJECT_RELEVANCES: frozenset[str] = frozenset({"DIRECT", "INDIRECT", "NONE", "UNCERTAIN"})

# 현재 과제의 기술·성과로 연결하면 안 되는 조합 (스펙 §5.3).
# 선행연구·인용문을 현재 과제 성과로 귀속시키는 것이 이 시스템에서 가장 치명적인 오류다.
NON_ATTRIBUTABLE_STATUSES: frozenset[str] = frozenset({"PRIOR_RESEARCH", "CITATION_ONLY"})

DEFAULT_RELATION_TYPES: list[str] = [
    "HAS_PROBLEM",
    "HAS_OBJECTIVE",
    "TARGETS",
    "USES_TECHNOLOGY",
    "USES_METHOD",
    "USES_DATASET",
    "PRODUCES",
    "ADDRESSES",
    "APPLIED_TO",
    "DERIVED_FROM",
    "IMPROVES",
    "USES_PRIOR_OUTPUT",
]


class KgExtractionConfig(BaseModel):
    """후보 추출 단계."""

    provider: str = Field(
        default="same_as_chat",
        description="same_as_chat | ollama | openai. 추출 품질이 그래프 품질을 좌우한다.",
    )
    ollama_model: str = Field(default="gemma4:26b")
    openai_model: str = Field(default="gpt-4o-mini")

    chunks_per_document: int = Field(
        default=12,
        ge=1,
        description=(
            "문서당 LLM에 보낼 청크 수. 전 청크(325,570개) 처리는 65만 호출·900시간 규모라 "
            "불가능하다. 연구목표·내용·결과에 해당할 가능성이 높은 청크만 고른다."
        ),
    )
    max_entities_per_chunk: int = Field(default=12, ge=1)
    minimum_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_retries: int = Field(default=2, ge=0)
    timeout_seconds: float = Field(
        default=240.0,
        gt=0,
        description=(
            "호출 타임아웃. 120초로는 모델이 GPU에 다시 올라오는 구간에서 무더기로 "
            "타임아웃이 났다(실측 21건). 재시도까지 감안해 넉넉히 잡는다."
        ),
    )
    max_output_tokens: int = Field(
        default=4096,
        ge=256,
        description=(
            "청크당 최대 출력 토큰. 2048이면 엔티티가 많은 청크에서 JSON이 중간에 잘려 "
            "'Unterminated string'으로 실패한다(실측). 스키마 강제 출력은 들여쓰기 때문에 "
            "더 길어진다."
        ),
    )

    two_pass: bool = Field(
        default=False,
        description=(
            "True면 문맥분류와 엔티티추출을 별도 호출로 나눈다(지침서 원안). "
            "기본은 한 번의 호출에서 분류 필드를 함께 받아 호출 수를 절반으로 줄인다."
        ),
    )
    skip_citation_only: bool = Field(default=True)
    skip_administrative: bool = Field(default=True)
    enabled_entity_types: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTITY_TYPES))

    evidence_match_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description=(
            "근거 문장이 청크 원문에 실제로 있는지 판정하는 유사도 기준. "
            "미달이면 후보를 REJECTED 처리한다 — 근거 없는 노드를 막는 마지막 방벽."
        ),
    )


class KgNormalizationConfig(BaseModel):
    """정규화 단계 — 잘못된 병합을 막는 것이 최우선."""

    exact_match_enabled: bool = Field(default=True)
    alias_match_enabled: bool = Field(default=True)
    string_similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    vector_candidate_count: int = Field(default=8, ge=1)
    vector_similarity_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    auto_merge_threshold: float = Field(default=0.94, ge=0.0, le=1.0)
    manual_review_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    prevent_cross_type_merge: bool = Field(
        default=True, description="기술과 산출물처럼 유형이 다르면 병합 금지."
    )
    prevent_different_target_merge: bool = Field(
        default=True,
        description="작물·품종·병해충·지역이 다르면 병합 금지 (사과 육종 ≠ 복숭아 육종).",
    )
    llm_adjudication: bool = Field(
        default=True, description="규칙 통과 후보에 대해 LLM 동일성 판정을 수행."
    )


class KgRelationConfig(BaseModel):
    """관계 추출 — 검증된 엔티티 목록 안에서만."""

    enabled: bool = Field(default=True)
    max_relations_per_chunk: int = Field(default=10, ge=1)
    minimum_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    enabled_relation_types: list[str] = Field(default_factory=lambda: list(DEFAULT_RELATION_TYPES))


class KgGraphConfig(BaseModel):
    """Neo4j 적재."""

    batch_size: int = Field(default=500, ge=1)
    create_mentions: bool = Field(default=True)
    create_project_aggregates: bool = Field(default=True)
    preserve_relation_evidence: bool = Field(default=True)


class KgJobConfig(BaseModel):
    """작업 실행 제어."""

    extraction_concurrency: int = Field(
        default=4,
        ge=1,
        description=(
            "동시 LLM 호출 수. 실측 최적값 4 — 1→4에서 처리량 2.07배(7.3→15.1 청크/분), "
            "6·8은 더 나아지지 않는다. 서버 쪽 OLLAMA_NUM_PARALLEL도 함께 켜져 있어야 "
            "효과가 난다(런처가 4로 설정)."
        ),
    )
    normalization_concurrency: int = Field(default=4, ge=1)
    neo4j_write_concurrency: int = Field(default=2, ge=1)
    yield_to_conversation: bool = Field(
        default=True,
        description="대화가 진행 중이면 배경 추출을 쉬어 간다 (E-87 GPU 경합 대응).",
    )


class KgGateConfig(BaseModel):
    """전체 실행 게이트 — 소규모 검증을 통과해야만 전체를 돌릴 수 있다 (스펙 §9)."""

    require_eval_pass: bool = Field(default=True)
    min_entity_precision: float = Field(default=0.80, ge=0.0, le=1.0)
    max_false_merge_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    min_evidence_accuracy: float = Field(default=0.90, ge=0.0, le=1.0)
    smoke_document_count: int = Field(default=10, ge=1)
    pilot_document_count: int = Field(default=100, ge=1)


class KnowledgeGraphConfig(BaseModel):
    """M_23 파이프라인 전체 설정."""

    enabled: bool = Field(default=True)
    candidate_db_path: str = Field(default="data/kg_candidates.db")
    extraction: KgExtractionConfig = Field(default_factory=KgExtractionConfig)
    normalization: KgNormalizationConfig = Field(default_factory=KgNormalizationConfig)
    relations: KgRelationConfig = Field(default_factory=KgRelationConfig)
    graph: KgGraphConfig = Field(default_factory=KgGraphConfig)
    jobs: KgJobConfig = Field(default_factory=KgJobConfig)
    gate: KgGateConfig = Field(default_factory=KgGateConfig)

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
        default=False,
        description=(
            "규칙 통과 후보에 대해 LLM 동일성 판정을 수행. **기본 False** — 실측(스펙 §4.1-A) "
            "결과 규칙 퍼지 병합의 효과가 0.5%였고, 남은 쌍은 애초에 병합하면 안 되는 것들이다. "
            "LLM을 얹으면 얻는 것 없이 False Merge 위험만 커진다. 8단계 관계추출과 함께 재검토."
        ),
    )

    fuzzy_enabled: bool = Field(
        default=True,
        description=(
            "정확일치 후 토큰 블로킹 퍼지 병합 수행 여부. 실측 효과는 0.5%(39,795→39,601)라 "
            "꺼도 결과가 거의 같다. 대량 재실행에서 시간을 아끼려면 끈다."
        ),
    )
    blocking_max_document_frequency: int = Field(
        default=2000,
        ge=1,
        description=(
            "이보다 많은 엔티티에 등장하는 토큰은 블로킹 색인에서 제외한다. '이용한'·'통한' "
            "같은 토큰이 후보군을 통째로 끌어와 비교가 O(n²)이 되는 것을 막는다."
        ),
    )
    max_block_candidates: int = Field(
        default=50,
        ge=1,
        description="한 항목이 비교해 볼 대표 수 상한. 공유 토큰이 많은 순으로 자른다.",
    )
    max_members_per_canonical: int = Field(
        default=50,
        ge=2,
        description=(
            "**블롭 감시.** 한 정규 엔티티가 이보다 많은 표기를 흡수하면 더 받지 않고 "
            "REVIEW_REQUIRED로 보낸다. CR-36에서 union-find 연쇄가 3만 용어를 208개 blob으로 "
            "붕괴시킨 전례가 있다 — 그 실패를 조기에 감지하는 장치다."
        ),
    )
    max_aliases_stored: int = Field(
        default=20, ge=1, description="정규 엔티티당 보관할 별칭 수 상한 (빈도 높은 순)."
    )


# entity_type → Project 관계 1:1 사상 (스펙 §4.1-B).
# 유형 7종이 스펙 §5.2의 Project 관계 7종과 정확히 맞아떨어진다. 관계를 LLM으로 다시
# 뽑을 이유가 없다 — 청크 28,976개 재호출(20~30시간)을 이 표 하나가 대신한다.
ENTITY_TYPE_TO_RELATION: dict[str, str] = {
    "RESEARCH_PROBLEM": "HAS_PROBLEM",
    "OBJECTIVE": "HAS_OBJECTIVE",
    "RESEARCH_TARGET": "TARGETS",
    "TECHNOLOGY": "USES_TECHNOLOGY",
    "METHOD": "USES_METHOD",
    "DATASET": "USES_DATASET",
    "OUTPUT": "PRODUCES",
}

# 엔티티 유형 → Neo4j 라벨 (스펙 §5.2)
ENTITY_TYPE_TO_LABEL: dict[str, str] = {
    "RESEARCH_PROBLEM": "ResearchProblem",
    "OBJECTIVE": "ResearchObjective",
    "RESEARCH_TARGET": "ResearchTarget",
    "TECHNOLOGY": "Technology",
    "METHOD": "Method",
    "DATASET": "Dataset",
    "OUTPUT": "Output",
}

# 문서유형 → derived_status 사전확률 (스펙 §5.3).
# RFP는 정의상 "앞으로 할 일", 완결보고서는 "한 일". 이 경계까지만 정확하고
# 완결보고서 **안에서** 계획과 실적을 가르지는 못한다 — 그건 재추출로만 얻는다.
DOC_TYPE_TO_STATUS: dict[str, str] = {
    "RFP": "REQUIREMENT",
    "FINAL_REPORT": "ACTUAL",
}


class KgRelationConfig(BaseModel):
    """관계 유도 — 검증된 엔티티 목록 안에서만. v2는 LLM을 쓰지 않는다."""

    enabled: bool = Field(default=True)
    max_relations_per_chunk: int = Field(default=10, ge=1)
    minimum_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    enabled_relation_types: list[str] = Field(default_factory=lambda: list(DEFAULT_RELATION_TYPES))

    derive_from_entity_type: bool = Field(
        default=True,
        description=(
            "entity_type→관계 1:1 사상으로 Project 집계 엣지를 만든다 (LLM 없음, 스펙 §4.1-B)."
        ),
    )
    link_target_key: bool = Field(
        default=True,
        description=(
            "target_key를 RESEARCH_TARGET 정규 엔티티로 승격하고 APPLIED_TO로 잇는다. "
            "엔티티의 91.5%가 단일 문서 전용이라(스펙 §4.1-C) 이 엣지가 없으면 그래프가 "
            "'별들의 숲'이 된다 — CR-34에서 이미 한 번 실패한 구조다."
        ),
    )
    derive_statement_status: bool = Field(
        default=True,
        description="statement_status가 UNCERTAIN이면 문서유형에서 유도 (스펙 §5.3).",
    )


class KgGraphConfig(BaseModel):
    """Neo4j 적재."""

    batch_size: int = Field(default=1000, ge=1)
    create_mentions: bool = Field(default=True)
    create_project_aggregates: bool = Field(default=True)
    preserve_relation_evidence: bool = Field(default=True)

    # ── 연결성·잡음 제어 (스펙 §4.1-C) ──────────────────────────────────────
    boilerplate_document_frequency: int = Field(
        default=60,
        ge=2,
        description=(
            "이보다 많은 문서에 등장하는 엔티티는 is_boilerplate로 표시한다. 실측 최상위는 "
            "'산업재산권 출원' 206문서·'학술발표' 139 등 전부 행정 상용구였다. "
            "**삭제가 아니라 표시**다 — 코퍼스가 늘면 판정이 달라져야 하므로 하드코딩하지 않는다."
        ),
    )
    shares_entity_enabled: bool = Field(
        default=True, description="문서↔문서 SHARES_ENTITY 가중 엣지 파생 (중복성 분석의 본체)."
    )
    shares_entity_max_fanout: int = Field(
        default=15,
        ge=2,
        description=(
            "이보다 많은 문서가 공유하는 엔티티는 문서-문서 엣지 계산에서 뺀다. M_19의 "
            "_RELATED_MAX_FANOUT과 같은 IDF 발상 — 상용구 허브가 만드는 가짜 유사도를 막는다."
        ),
    )
    shares_entity_min_weight: float = Field(
        default=2.0, ge=0.0, description="이 미만 가중치의 문서-문서 엣지는 만들지 않는다."
    )
    shares_entity_max_edges: int = Field(
        default=200000, ge=1, description="문서-문서 엣지 총량 상한 (가중치 큰 순 유지)."
    )
    visualization_min_document_frequency: int = Field(
        default=2,
        ge=1,
        description=(
            "그래프 탭 기본 뷰에 그릴 최소 df. 174,985개를 다 그리면 아무것도 안 보인다. "
            "노드를 지우는 것이 아니라 기본 뷰에서 감추는 것이다."
        ),
    )


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

# src/kg/__init__.py
"""M_23 지식그래프 구축 파이프라인.

LLM 추출 결과를 곧바로 Neo4j 노드로 만들지 않는다. 후보 저장 → 문서 단위 통합 →
정규화 → 관계 추출 → 적재 순서를 거친다. 스펙: specs/M_23_KGPipeline_SPEC.md
"""

from .candidates import (
    CandidateStore,
    CanonicalEntity,
    DocEntity,
    DocumentMeta,
    EntityCandidate,
    RelationCandidate,
)
from .config import KnowledgeGraphConfig

__all__ = [
    "CandidateStore",
    "CanonicalEntity",
    "DocEntity",
    "DocumentMeta",
    "EntityCandidate",
    "KnowledgeGraphConfig",
    "RelationCandidate",
]

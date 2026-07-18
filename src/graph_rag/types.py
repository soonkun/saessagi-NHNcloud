# src/graph_rag/types.py
"""M_19 GraphRAG 데이터 타입 (스펙 §3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

# 엔티티 타입 화이트리스트 — 추출 결과가 이 외의 값이면 "기타"로 강등
ENTITY_TYPES: frozenset[str] = frozenset({"인물", "조직", "사업", "제도", "기술", "장소", "기타"})


def normalize_name(name: str) -> str:
    """엔티티 이름 정규화: 공백 정리 + casefold. 병합 키로 사용."""
    return " ".join(name.split()).casefold()


def entity_id(name: str, type_: str) -> str:
    """엔티티 고유 id = 정규화 이름 + ':' + 타입."""
    return f"{normalize_name(name)}:{type_}"


@dataclass(frozen=True)
class Entity:
    """그래프 엔티티 노드."""

    id: str
    name: str
    type: str
    description: str = ""


@dataclass(frozen=True)
class Relation:
    """엔티티 간 관계. weight는 동시출현 누적치."""

    source_id: str
    target_id: str
    type: str
    description: str = ""
    weight: float = 1.0


@dataclass(frozen=True)
class ChunkLink:
    """엔티티 ↔ 청크 언급 링크."""

    entity_id: str
    chunk_id: str


@dataclass(frozen=True)
class GraphNode:
    """시각화용 노드. kind: entity | document | note."""

    id: str
    label: str
    kind: str
    type: str = ""


@dataclass(frozen=True)
class GraphEdge:
    """시각화용 엣지. kind: rel | mentioned_in | part_of."""

    source: str
    target: str
    kind: str
    weight: float = 1.0


@dataclass(frozen=True)
class GraphSnapshot:
    """그래프 탭에 내려줄 스냅샷."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass(frozen=True)
class EvidenceSubgraph:
    """한 번의 하이브리드 검색이 실제로 밟은 근거 서브그래프."""

    query: str
    created: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    chunk_ids: list[str]


@dataclass
class IndexStatus:
    """문서 단위 그래프 인덱싱 진행 상태."""

    doc_id: str
    state: str = "pending"  # pending | running | done | failed | cancelled
    total_chunks: int = 0
    done_chunks: int = 0
    skipped_chunks: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "state": self.state,
            "total_chunks": self.total_chunks,
            "done_chunks": self.done_chunks,
            "skipped_chunks": self.skipped_chunks,
            "error": self.error,
        }


@dataclass
class ExtractionResult:
    """(구) 추출기 출력 — 청크 하나에서 나온 엔티티·관계. CR-30에서 폐기 경로."""

    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


# ── CR-30: Project + 역할 키워드 스키마 ──────────────────────────────────────

# 키워드 역할 화이트리스트 — 이 외의 값은 폐기 (기타 강등 없음, 보수적)
KEYWORD_ROLES: frozenset[str] = frozenset(
    {"research_target", "technology", "problem", "outcome"}
)

KEYWORD_ROLE_LABELS: dict[str, str] = {
    "research_target": "연구대상",
    "technology": "기술",
    "problem": "문제",
    "outcome": "산출물",
}


def keyword_id(doc_id: str, raw_term: str, role: str) -> str:
    """키워드 노드 id — 문서 스코프 (전역 병합 금지: 같은 단어도 문서·문맥별 보존)."""
    return f"{doc_id}::{normalize_name(raw_term)}::{role}"


@dataclass(frozen=True)
class ProjectInfo:
    """문서(=Project 노드)의 과제 식별 정보 — 별도 노드가 아닌 속성으로 저장."""

    doc_id: str
    title: str = ""  # 과제명 (추출 실패 시 빈값 — 저장 시 doc_name 폴백)
    rfp_no: str = ""
    project_no: str = ""


@dataclass(frozen=True)
class KeywordMention:
    """문서별 키워드 언급 — raw 우선, 정규화는 후처리 단계에서 속성으로만 갱신."""

    doc_id: str
    raw_term: str
    role: str  # KEYWORD_ROLES 중 하나
    confidence: float = 0.0
    normalized_term: str = ""  # 정규화 후처리에서 채움 (raw_term은 보존)
    normalization_status: str = "raw"  # raw | normalized

    @property
    def id(self) -> str:
        return keyword_id(self.doc_id, self.raw_term, self.role)


@dataclass
class ProjectExtraction:
    """CR-30 추출기 출력 — 문서 1건에서 나온 과제 정보 + 키워드(최대 10)."""

    project: ProjectInfo
    keywords: list[KeywordMention] = field(default_factory=list)

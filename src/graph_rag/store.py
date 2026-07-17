# src/graph_rag/store.py
"""M_19 GraphStore 추상 인터페이스 (스펙 §3.2).

Neo4j(V1)와 추후 Kuzu(V2 후보) 구현체가 이 계약을 만족한다 — CR-18의
"서버형 DB를 임베디드로 교체 가능해야 한다" 요구의 실체.
모든 메서드는 sync이다(드라이버가 sync). async 경계는 GraphRagService가
run_in_executor로 감당한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ChunkLink, Entity, GraphSnapshot, Relation


class GraphStore(ABC):
    """엔티티·관계·청크링크 그래프 저장소 계약."""

    @abstractmethod
    def ping(self) -> bool:
        """저장소 연결 확인. 예외 없이 bool 반환."""

    @abstractmethod
    def ensure_schema(self) -> None:
        """유니크 제약·인덱스 멱등 생성."""

    @abstractmethod
    def upsert_document(self, doc_id: str, name: str, category: str = "") -> None: ...

    @abstractmethod
    def upsert_note(self, slug: str, title: str) -> None: ...

    @abstractmethod
    def upsert_entities(self, entities: list[Entity]) -> None:
        """같은 id는 병합. description은 기존이 비어있을 때만 갱신."""

    @abstractmethod
    def upsert_relations(self, relations: list[Relation]) -> None:
        """같은 (source,target,type)은 weight 누적."""

    @abstractmethod
    def link_chunks(self, links: list[ChunkLink], parent_id: str, parent_kind: str) -> None:
        """(:Entity)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(:Document|:Note) 배선.

        parent_kind: "document" | "note".
        """

    @abstractmethod
    def find_entities(self, terms: list[str], limit: int = 20) -> list[Entity]:
        """정규화된 용어의 부분일치로 엔티티 검색."""

    @abstractmethod
    def neighbors(self, entity_ids: list[str], hops: int = 1, limit: int = 50) -> list[Entity]:
        """REL 엣지를 따라 hops 홉 이내 이웃 엔티티."""

    @abstractmethod
    def chunks_for_entities(self, entity_ids: list[str], limit: int = 30) -> list[tuple[str, int]]:
        """엔티티들이 언급된 청크 (chunk_id, 연결 엔티티 수) — 연결 수 내림차순."""

    @abstractmethod
    def subgraph(self, entity_ids: list[str], chunk_ids: list[str]) -> GraphSnapshot:
        """근거 시각화용: 지정 엔티티·청크가 속한 부분 그래프 (문서/노트 노드 포함)."""

    @abstractmethod
    def snapshot(self, limit: int = 500, entity_types: list[str] | None = None) -> GraphSnapshot:
        """그래프 탭용 전체 스냅샷 (노드 수 상한)."""

    @abstractmethod
    def delete_by_doc_id(self, doc_id: str) -> None:
        """문서/노트와 소속 청크 삭제 + 고아 엔티티 정리."""

    @abstractmethod
    def all_entities(self, limit: int = 2000) -> list[Entity]:
        """전체 엔티티 목록 (정규화 후보 수집용)."""

    @abstractmethod
    def merge_entities(self, target_id: str, source_ids: list[str]) -> int:
        """CR-22 정규화: source 엔티티들의 관계·언급을 target으로 이전 후 삭제.

        Returns: 실제 병합(삭제)된 엔티티 수.
        """

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """{entities, relations, chunks, documents, notes}."""

    @abstractmethod
    def close(self) -> None: ...

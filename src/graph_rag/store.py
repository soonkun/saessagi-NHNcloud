# src/graph_rag/store.py
"""M_19 GraphStore 추상 인터페이스 (스펙 §3.2).

Neo4j(V1)와 추후 Kuzu(V2 후보) 구현체가 이 계약을 만족한다 — CR-18의
"서버형 DB를 임베디드로 교체 가능해야 한다" 요구의 실체.
모든 메서드는 sync이다(드라이버가 sync). async 경계는 GraphRagService가
run_in_executor로 감당한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import ChunkLink, Entity, GraphSnapshot, KeywordMention, ProjectInfo, Relation


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
    def clear_all(self) -> dict[str, int]:
        """CR-26: 그래프 전체 초기화 — Entity/Chunk/Document/Note 전부 삭제.

        Returns: 삭제 전 stats (기록용).
        """

    # ── CR-30: Project + 역할 키워드 스키마 ──────────────────────────────────

    @abstractmethod
    def upsert_project_bundle(
        self, project: "ProjectInfo", keywords: list["KeywordMention"]
    ) -> None:
        """문서 1건의 과제 정보·키워드를 단일 트랜잭션으로 저장.

        - Document 노드에 title/rfp_no/project_no를 속성으로 SET (별도 노드 아님)
        - 문서의 기존 Keyword 노드를 지우고 새 키워드로 교체 (재인덱싱 멱등)
        - Keyword id는 문서 스코프 — 전역 병합 금지
        """

    @abstractmethod
    def find_keywords(self, terms: list[str], limit: int = 30) -> list["KeywordMention"]:
        """질의 용어와 raw_term/normalized_term 부분 일치하는 키워드 언급 검색."""

    @abstractmethod
    def keywords_for_doc(self, doc_id: str) -> list["KeywordMention"]:
        """문서의 키워드 언급 목록 (검증·정규화 후처리용)."""

    @abstractmethod
    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """CR-31: 질의로 문서(과제)를 찾는다 — 제목 또는 소속 키워드 일치.

        키워드는 문서를 찾는 신호이고 결과는 문서만 반환한다.
        Returns: [{doc_id, title, title_match: bool, matched_keywords: [term...]}]
        (매칭 키워드 많을수록 상위)
        """

    @abstractmethod
    def all_keywords(self, limit: int = 5000) -> list["KeywordMention"]:
        """전체 키워드 언급 (정규화 후처리 대상 수집)."""

    @abstractmethod
    def update_keyword_normalization(self, keyword_ids: list[str], normalized_term: str) -> int:
        """정규화 후처리: 해당 키워드 노드들의 normalized_term/status만 갱신
        (raw_term 보존, 노드 병합 없음). 반환: 갱신 수."""

    @abstractmethod
    def existing_doc_ids(self) -> list[str]:
        """CR-35: 그래프에 이미 인덱싱된 Document의 doc_id 목록 (증분 인덱싱 판별용)."""

    @abstractmethod
    def reset_keyword_normalization(self) -> int:
        """CR-36: 전체 재정규화 전 초기화 — 모든 Keyword의 normalized_term을 비우고
        status를 'raw'로 되돌린다(raw_term 보존). 반환: 초기화된 수."""

    @abstractmethod
    def mark_keywords_processed(self, keyword_ids: list[str]) -> int:
        """CR-35: 증분 정규화 — 병합 안 됐어도 '처리 완료'로 표시.

        normalization_status를 'normalized'로 올리고, normalized_term이 비어 있으면
        raw_term으로 채운다(raw_term 자체는 보존). 다음 증분 정규화에서 재검토 대상
        (status='raw')에서 빠지게 하기 위함. 반환: 갱신 수.
        """

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """{entities, relations, chunks, documents, notes}."""

    @abstractmethod
    def close(self) -> None: ...

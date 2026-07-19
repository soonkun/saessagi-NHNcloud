# src/graph_rag/neo4j_store.py
"""M_19 Neo4j GraphStore 구현 (스펙 §3.2).

- bolt://127.0.0.1:7687 등 사설 대역만 허용 (호출자 app.url_guard가 검증).
- 모든 Cypher는 파라미터라이즈 — 사용자 유래 문자열을 쿼리에 조립하지 않는다.
- 드라이버는 lazy 생성, 세션은 호출 단위로 짧게 사용.
"""

from __future__ import annotations

import logging
from typing import Any

from .errors import GraphStoreError
from .store import GraphStore
from .types import (
    ChunkLink,
    Entity,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    KeywordMention,
    ProjectInfo,
    Relation,
)

logger = logging.getLogger(__name__)

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
    "CREATE CONSTRAINT note_slug IF NOT EXISTS FOR (n:Note) REQUIRE n.slug IS UNIQUE",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.norm_name)",
    # CR-30: Project(=Document 속성) + 문서 스코프 키워드
    "CREATE CONSTRAINT keyword_id IF NOT EXISTS FOR (k:Keyword) REQUIRE k.id IS UNIQUE",
    "CREATE INDEX keyword_raw IF NOT EXISTS FOR (k:Keyword) ON (k.raw_term)",
    "CREATE INDEX keyword_norm IF NOT EXISTS FOR (k:Keyword) ON (k.normalized_term)",
    "CREATE CONSTRAINT techcode_id IF NOT EXISTS FOR (t:TechnologyCode) REQUIRE t.code IS UNIQUE",
)


class Neo4jGraphStore(GraphStore):
    """Neo4j 5.x 기반 GraphStore."""

    def __init__(
        self,
        uri: str = "bolt://127.0.0.1:7687",
        user: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Any = None

    # ── 내부 유틸 ─────────────────────────────────────────────────────────────

    def _get_driver(self) -> Any:
        if self._driver is None:
            try:
                from neo4j import GraphDatabase

                self._driver = GraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                    connection_timeout=3.0,
                    max_transaction_retry_time=5.0,
                )
            except Exception as exc:  # import 실패 포함
                raise GraphStoreError(f"Neo4j 드라이버 생성 실패: {exc}") from exc
        return self._driver

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """단일 쿼리 실행 → dict 리스트."""
        try:
            driver = self._get_driver()
            with driver.session(database=self._database) as session:
                result = session.run(query, **params)
                return [dict(record) for record in result]
        except GraphStoreError:
            raise
        except Exception as exc:
            raise GraphStoreError(f"Cypher 실행 실패: {exc}") from exc

    # ── GraphStore 구현 ───────────────────────────────────────────────────────

    def ping(self) -> bool:
        try:
            self._run("RETURN 1 AS ok")
            return True
        except Exception as exc:
            logger.debug("Neo4j ping 실패: %s", exc)
            return False

    def ensure_schema(self) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            self._run(stmt)
        logger.info("Neo4j 스키마 제약/인덱스 확인 완료")

    def upsert_document(self, doc_id: str, name: str, category: str = "") -> None:
        self._run(
            "MERGE (d:Document {doc_id: $doc_id}) SET d.name = $name, d.category = $category",
            doc_id=doc_id,
            name=name,
            category=category,
        )

    def upsert_note(self, slug: str, title: str) -> None:
        self._run(
            "MERGE (n:Note {slug: $slug}) SET n.title = $title",
            slug=slug,
            title=title,
        )

    def upsert_entities(self, entities: list[Entity]) -> None:
        if not entities:
            return
        rows = [
            {
                "id": e.id,
                "name": e.name,
                "norm_name": e.id.rsplit(":", 1)[0],
                "type": e.type,
                "description": e.description,
            }
            for e in entities
        ]
        self._run(
            "UNWIND $rows AS row "
            "MERGE (e:Entity {id: row.id}) "
            "ON CREATE SET e.name = row.name, e.norm_name = row.norm_name, "
            "  e.type = row.type, e.description = row.description "
            "ON MATCH SET e.description = "
            "  CASE WHEN coalesce(e.description, '') = '' THEN row.description "
            "       ELSE e.description END",
            rows=rows,
        )

    def upsert_relations(self, relations: list[Relation]) -> None:
        if not relations:
            return
        rows = [
            {
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.type,
                "description": r.description,
                "weight": r.weight,
            }
            for r in relations
        ]
        self._run(
            "UNWIND $rows AS row "
            "MATCH (a:Entity {id: row.source_id}), (b:Entity {id: row.target_id}) "
            "MERGE (a)-[rel:REL {type: row.type}]->(b) "
            "ON CREATE SET rel.description = row.description, rel.weight = row.weight "
            "ON MATCH SET rel.weight = rel.weight + row.weight",
            rows=rows,
        )

    def link_chunks(self, links: list[ChunkLink], parent_id: str, parent_kind: str) -> None:
        if not links:
            return
        rows = [{"entity_id": ln.entity_id, "chunk_id": ln.chunk_id} for ln in links]
        if parent_kind == "note":
            parent_match = "MERGE (p:Note {slug: $parent_id})"
        else:
            parent_match = "MERGE (p:Document {doc_id: $parent_id})"
        self._run(
            parent_match + " "
            "WITH p UNWIND $rows AS row "
            "MERGE (c:Chunk {chunk_id: row.chunk_id}) "
            "SET c.doc_id = $parent_id "
            "MERGE (c)-[:PART_OF]->(p) "
            "WITH c, row "
            "MATCH (e:Entity {id: row.entity_id}) "
            "MERGE (e)-[:MENTIONED_IN]->(c)",
            rows=rows,
            parent_id=parent_id,
        )

    def find_entities(self, terms: list[str], limit: int = 20) -> list[Entity]:
        terms_norm = [t.casefold() for t in terms if t.strip()]
        if not terms_norm:
            return []
        # 양방향 포함: 질의어에 조사가 붙어도("A기관과") 엔티티명("a기관")과 매칭되도록
        # 역포함(term CONTAINS name)은 이름 3자 이상일 때만 — "C" 같은 초단문 엔티티가
        # 아무 질의에나 매칭되는 오염 방지 (CR-25에서 실그래프로 발견)
        rows = self._run(
            "UNWIND $terms AS term "
            "MATCH (e:Entity) "
            "WHERE e.norm_name CONTAINS term "
            "   OR (size(e.norm_name) >= 3 AND term CONTAINS e.norm_name) "
            "RETURN DISTINCT e.id AS id, e.name AS name, e.type AS type, "
            "  coalesce(e.description, '') AS description "
            "LIMIT $limit",
            terms=terms_norm,
            limit=limit,
        )
        return [Entity(**row) for row in rows]

    def neighbors(self, entity_ids: list[str], hops: int = 1, limit: int = 50) -> list[Entity]:
        if not entity_ids:
            return []
        hops = max(1, min(int(hops), 3))  # 안전 상한
        # 가변 길이 패턴의 홉 수는 파라미터화 불가 — 정수 검증 후 포맷 (인젝션 불가)
        query = (
            "MATCH (s:Entity)-[:REL*1..%d]-(e:Entity) "
            "WHERE s.id IN $ids AND NOT e.id IN $ids "
            "RETURN DISTINCT e.id AS id, e.name AS name, e.type AS type, "
            "  coalesce(e.description, '') AS description "
            "LIMIT $limit" % hops
        )
        rows = self._run(query, ids=entity_ids, limit=limit)
        return [Entity(**row) for row in rows]

    def chunks_for_entities(self, entity_ids: list[str], limit: int = 30) -> list[tuple[str, int]]:
        if not entity_ids:
            return []
        rows = self._run(
            "MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk) "
            "WHERE e.id IN $ids "
            "RETURN c.chunk_id AS chunk_id, count(DISTINCT e) AS n "
            "ORDER BY n DESC LIMIT $limit",
            ids=entity_ids,
            limit=limit,
        )
        return [(str(row["chunk_id"]), int(row["n"])) for row in rows]

    def subgraph(self, entity_ids: list[str], chunk_ids: list[str]) -> GraphSnapshot:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        if entity_ids:
            for row in self._run(
                "MATCH (e:Entity) WHERE e.id IN $ids "
                "RETURN e.id AS id, e.name AS name, e.type AS type",
                ids=entity_ids,
            ):
                nodes[str(row["id"])] = GraphNode(
                    id=str(row["id"]), label=str(row["name"]), kind="entity", type=str(row["type"])
                )
            for row in self._run(
                "MATCH (a:Entity)-[r:REL]-(b:Entity) "
                "WHERE a.id IN $ids AND b.id IN $ids AND a.id < b.id "
                "RETURN a.id AS s, b.id AS t, r.weight AS w",
                ids=entity_ids,
            ):
                edges.append(
                    GraphEdge(
                        source=str(row["s"]),
                        target=str(row["t"]),
                        kind="rel",
                        weight=float(row["w"] or 1.0),
                    )
                )

        if chunk_ids and entity_ids:
            for row in self._run(
                "MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk)-[:PART_OF]->(p) "
                "WHERE e.id IN $eids AND c.chunk_id IN $cids "
                "RETURN DISTINCT e.id AS eid, "
                "  CASE WHEN p:Note THEN p.slug ELSE p.doc_id END AS pid, "
                "  CASE WHEN p:Note THEN p.title ELSE p.name END AS plabel, "
                "  CASE WHEN p:Note THEN 'note' ELSE 'document' END AS pkind",
                eids=entity_ids,
                cids=chunk_ids,
            ):
                pid = str(row["pid"])
                if pid not in nodes:
                    nodes[pid] = GraphNode(
                        id=pid, label=str(row["plabel"] or pid), kind=str(row["pkind"])
                    )
                edges.append(GraphEdge(source=str(row["eid"]), target=pid, kind="mentioned_in"))

        return GraphSnapshot(nodes=list(nodes.values()), edges=edges)

    def snapshot(self, limit: int = 500, entity_types: list[str] | None = None) -> GraphSnapshot:
        limit = max(1, min(int(limit), 2000))
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        # ── CR-30: Project(문서) + 역할 키워드 (신규 스키마 — 우선 표시) ──────
        role_filter = " WHERE k.role IN $types" if entity_types else ""
        for row in self._run(
            "MATCH (d:Document)-[:HAS_KEYWORD]->(k:Keyword)" + role_filter + " "
            "RETURN d.doc_id AS did, coalesce(d.title, d.name) AS dlabel, "
            "  d.rfp_no AS rfp_no, d.project_no AS project_no, "
            "  k.id AS kid, k.raw_term AS kterm, k.role AS krole, "
            "  coalesce(k.normalized_term, '') AS knorm "
            "LIMIT $limit",
            types=entity_types or [],
            limit=limit * 4,
        ):
            did = str(row["did"])
            if did not in nodes:
                nodes[did] = GraphNode(
                    id=did, label=str(row["dlabel"] or did), kind="document"
                )
            kid = str(row["kid"])
            if kid not in nodes:
                nodes[kid] = GraphNode(
                    id=kid,
                    label=str(row["knorm"] or row["kterm"]),
                    kind="keyword",
                    type=str(row["krole"]),
                )
            edges.append(GraphEdge(source=did, target=kid, kind="has_keyword"))

        type_filter = " AND e.type IN $types" if entity_types else ""
        for row in self._run(
            "MATCH (e:Entity) WHERE true" + type_filter + " "
            "OPTIONAL MATCH (e)-[m:MENTIONED_IN]->(:Chunk) "
            "WITH e, count(m) AS mentions ORDER BY mentions DESC LIMIT $limit "
            "RETURN e.id AS id, e.name AS name, e.type AS type",
            types=entity_types or [],
            limit=limit,
        ):
            nodes[str(row["id"])] = GraphNode(
                id=str(row["id"]), label=str(row["name"]), kind="entity", type=str(row["type"])
            )

        ids = list(nodes.keys())
        if ids:
            for row in self._run(
                "MATCH (a:Entity)-[r:REL]->(b:Entity) "
                "WHERE a.id IN $ids AND b.id IN $ids "
                "RETURN a.id AS s, b.id AS t, r.weight AS w",
                ids=ids,
            ):
                edges.append(
                    GraphEdge(
                        source=str(row["s"]),
                        target=str(row["t"]),
                        kind="rel",
                        weight=float(row["w"] or 1.0),
                    )
                )
            # 엔티티 → 소속 문서/노트 (문서 노드는 상한 없이 — 문서 수는 작다)
            for row in self._run(
                "MATCH (e:Entity)-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(p) "
                "WHERE e.id IN $ids "
                "RETURN DISTINCT e.id AS eid, "
                "  CASE WHEN p:Note THEN p.slug ELSE p.doc_id END AS pid, "
                "  CASE WHEN p:Note THEN p.title ELSE p.name END AS plabel, "
                "  CASE WHEN p:Note THEN 'note' ELSE 'document' END AS pkind",
                ids=ids,
            ):
                pid = str(row["pid"])
                if pid not in nodes:
                    nodes[pid] = GraphNode(
                        id=pid, label=str(row["plabel"] or pid), kind=str(row["pkind"])
                    )
                edges.append(GraphEdge(source=str(row["eid"]), target=pid, kind="mentioned_in"))

        return GraphSnapshot(nodes=list(nodes.values()), edges=edges)

    def all_entities(self, limit: int = 2000) -> list[Entity]:
        rows = self._run(
            "MATCH (e:Entity) "
            "RETURN e.id AS id, e.name AS name, e.type AS type, "
            "  coalesce(e.description, '') AS description "
            "LIMIT $limit",
            limit=limit,
        )
        return [Entity(**row) for row in rows]

    def merge_entities(self, target_id: str, source_ids: list[str]) -> int:
        """CR-22 정규화: 언급(MENTIONED_IN)·관계(REL)를 target으로 이전 후 source 삭제."""
        source_ids = [s for s in source_ids if s and s != target_id]
        if not source_ids:
            return 0
        # 병합 대상 수 선계수 (존재하는 것만)
        rows = self._run(
            "MATCH (s:Entity) WHERE s.id IN $sources RETURN count(s) AS n",
            sources=source_ids,
        )
        count = int(rows[0]["n"]) if rows else 0
        if count == 0:
            return 0
        # 청크 언급 이전 (중복은 MERGE로 흡수)
        self._run(
            "MATCH (s:Entity)-[m:MENTIONED_IN]->(c:Chunk) WHERE s.id IN $sources "
            "MATCH (t:Entity {id: $target}) "
            "MERGE (t)-[:MENTIONED_IN]->(c) "
            "DELETE m",
            sources=source_ids,
            target=target_id,
        )
        # 나가는 관계 이전
        self._run(
            "MATCH (s:Entity)-[r:REL]->(b:Entity) "
            "WHERE s.id IN $sources AND b.id <> $target AND NOT b.id IN $sources "
            "MATCH (t:Entity {id: $target}) "
            "MERGE (t)-[nr:REL {type: r.type}]->(b) "
            "ON CREATE SET nr.description = r.description, nr.weight = coalesce(r.weight, 1) "
            "DELETE r",
            sources=source_ids,
            target=target_id,
        )
        # 들어오는 관계 이전
        self._run(
            "MATCH (a:Entity)-[r:REL]->(s:Entity) "
            "WHERE s.id IN $sources AND a.id <> $target AND NOT a.id IN $sources "
            "MATCH (t:Entity {id: $target}) "
            "MERGE (a)-[nr:REL {type: r.type}]->(t) "
            "ON CREATE SET nr.description = r.description, nr.weight = coalesce(r.weight, 1) "
            "DELETE r",
            sources=source_ids,
            target=target_id,
        )
        # 잔여 간선 포함 삭제
        self._run(
            "MATCH (s:Entity) WHERE s.id IN $sources DETACH DELETE s",
            sources=source_ids,
        )
        return count

    def clear_all(self) -> dict[str, int]:
        """CR-26: 그래프 전체 초기화 — 우리 스키마의 노드 라벨만 삭제 (다른 DB 데이터 보호)."""
        before = self.stats()
        for label in ("Chunk", "Entity", "Keyword", "TechnologyCode", "Document", "Note"):
            self._run(f"MATCH (n:{label}) DETACH DELETE n")
        return before

    # ── CR-30: Project + 역할 키워드 ─────────────────────────────────────────

    def upsert_project_bundle(
        self, project: ProjectInfo, keywords: list[KeywordMention]
    ) -> None:
        """문서 1건의 과제 정보·키워드를 단일 write 트랜잭션으로 저장 (재인덱싱 멱등)."""
        rows = [
            {
                "id": k.id,
                "doc_id": k.doc_id,
                "raw_term": k.raw_term,
                "normalized_term": k.normalized_term,
                "role": k.role,
                "confidence": k.confidence,
                "normalization_status": k.normalization_status,
            }
            for k in keywords
        ]

        def _tx(tx: Any) -> None:
            tx.run(
                "MERGE (d:Document {doc_id: $doc_id}) "
                "SET d.title = $title, d.rfp_no = $rfp_no, d.project_no = $project_no",
                doc_id=project.doc_id,
                title=project.title,
                rfp_no=project.rfp_no,
                project_no=project.project_no,
            )
            # 문서의 기존 키워드 교체 (전역 병합 금지 — 문서 스코프 노드)
            tx.run(
                "MATCH (d:Document {doc_id: $doc_id})-[:HAS_KEYWORD]->(k:Keyword) "
                "DETACH DELETE k",
                doc_id=project.doc_id,
            )
            if rows:
                tx.run(
                    "MATCH (d:Document {doc_id: $doc_id}) "
                    "UNWIND $rows AS row "
                    "CREATE (k:Keyword {id: row.id}) "
                    "SET k.doc_id = row.doc_id, k.raw_term = row.raw_term, "
                    "    k.normalized_term = row.normalized_term, k.role = row.role, "
                    "    k.confidence = row.confidence, "
                    "    k.normalization_status = row.normalization_status "
                    "CREATE (d)-[:HAS_KEYWORD]->(k)",
                    doc_id=project.doc_id,
                    rows=rows,
                )

        driver = self._get_driver()
        with driver.session(database=self._database) as session:
            session.execute_write(_tx)

    _KW_RETURN = (
        "RETURN k.doc_id AS doc_id, k.raw_term AS raw_term, k.role AS role, "
        "  coalesce(k.confidence, 0.0) AS confidence, "
        "  coalesce(k.normalized_term, '') AS normalized_term, "
        "  coalesce(k.normalization_status, 'raw') AS normalization_status"
    )

    def find_keywords(self, terms: list[str], limit: int = 30) -> list[KeywordMention]:
        terms_norm = [t.casefold() for t in terms if t.strip()]
        if not terms_norm:
            return []
        # E-60 교훈: 역포함은 용어 3자 이상만 (초단문 와일드카드 방지)
        rows = self._run(
            "UNWIND $terms AS term "
            "MATCH (k:Keyword) "
            "WHERE toLower(k.raw_term) CONTAINS term "
            "   OR toLower(k.normalized_term) CONTAINS term "
            "   OR (size(k.raw_term) >= 3 AND term CONTAINS toLower(k.raw_term)) "
            f"{self._KW_RETURN} LIMIT $limit",
            terms=terms_norm,
            limit=limit,
        )
        return [KeywordMention(**row) for row in rows]

    def keywords_for_doc(self, doc_id: str) -> list[KeywordMention]:
        rows = self._run(
            f"MATCH (:Document {{doc_id: $doc_id}})-[:HAS_KEYWORD]->(k:Keyword) {self._KW_RETURN}",
            doc_id=doc_id,
        )
        return [KeywordMention(**row) for row in rows]

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """CR-31: 제목 또는 소속 키워드로 문서 검색 — 결과는 문서만."""
        q = " ".join((query or "").split()).casefold()
        if len(q) < 2:
            return []
        rows = self._run(
            "MATCH (d:Document) "
            "OPTIONAL MATCH (d)-[:HAS_KEYWORD]->(k:Keyword) "
            "  WHERE toLower(k.raw_term) CONTAINS $q OR toLower(k.normalized_term) CONTAINS $q "
            "WITH d, "
            "  toLower(coalesce(d.title, d.name, '')) CONTAINS $q AS title_match, "
            "  collect(DISTINCT k.raw_term) AS kws "
            "WHERE title_match OR size(kws) > 0 "
            "RETURN d.doc_id AS doc_id, coalesce(d.title, d.name, d.doc_id) AS title, "
            "  d.project_no AS project_no, title_match, kws AS matched_keywords "
            "ORDER BY size(kws) DESC, title_match DESC "
            "LIMIT $limit",
            q=q,
            limit=limit,
        )
        return [
            {
                "doc_id": str(r["doc_id"]),
                "title": str(r["title"] or r["doc_id"]),
                "project_no": str(r.get("project_no") or ""),
                "title_match": bool(r["title_match"]),
                "matched_keywords": [str(x) for x in (r.get("matched_keywords") or []) if x],
            }
            for r in rows
        ]

    def all_keywords(self, limit: int = 5000) -> list[KeywordMention]:
        rows = self._run(f"MATCH (k:Keyword) {self._KW_RETURN} LIMIT $limit", limit=limit)
        return [KeywordMention(**row) for row in rows]

    def update_keyword_normalization(self, keyword_ids: list[str], normalized_term: str) -> int:
        if not keyword_ids:
            return 0
        rows = self._run(
            "MATCH (k:Keyword) WHERE k.id IN $ids "
            "SET k.normalized_term = $norm, k.normalization_status = 'normalized' "
            "RETURN count(k) AS n",
            ids=keyword_ids,
            norm=normalized_term,
        )
        return int(rows[0]["n"]) if rows else 0

    def delete_by_doc_id(self, doc_id: str) -> None:
        # CR-30: 문서 스코프 키워드 연쇄 삭제
        self._run(
            "MATCH (:Document {doc_id: $doc_id})-[:HAS_KEYWORD]->(k:Keyword) DETACH DELETE k",
            doc_id=doc_id,
        )
        # 문서/노트 + 소속 청크 삭제
        self._run(
            "OPTIONAL MATCH (d:Document {doc_id: $doc_id}) "
            "OPTIONAL MATCH (n:Note {slug: $doc_id}) "
            "WITH coalesce(d, n) AS p WHERE p IS NOT NULL "
            "OPTIONAL MATCH (c:Chunk)-[:PART_OF]->(p) "
            "DETACH DELETE c, p",
            doc_id=doc_id,
        )
        # 고아 엔티티 정리 (어느 청크에도 언급되지 않는 엔티티)
        self._run("MATCH (e:Entity) WHERE NOT (e)-[:MENTIONED_IN]->(:Chunk) DETACH DELETE e")

    def stats(self) -> dict[str, int]:
        row = self._run(
            "RETURN count { MATCH (e:Entity) RETURN e } AS entities, "
            "count { MATCH (:Entity)-[r:REL]->(:Entity) RETURN r } AS relations, "
            "count { MATCH (k:Keyword) RETURN k } AS keywords, "
            "count { MATCH (c:Chunk) RETURN c } AS chunks, "
            "count { MATCH (d:Document) RETURN d } AS documents, "
            "count { MATCH (n:Note) RETURN n } AS notes"
        )
        return {k: int(v) for k, v in row[0].items()} if row else {}

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception as exc:
                logger.debug("Neo4j driver close 실패 (무시): %s", exc)
            self._driver = None

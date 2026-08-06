# src/kg/graph_store.py
"""M_23 Neo4j 스토어 (스펙 §5.2, §3 (9)).

M_19의 `graph_rag/neo4j_store.py`를 넓히지 않고 새로 둔다. 그쪽 `GraphStore` ABC를 건드리면
`tests/graph_rag/fakes.py`의 FakeGraphStore까지 전부 따라 바뀌어야 하는데, 두 그래프는
스키마가 겹치지 않아 공통 인터페이스로 묶을 이유가 없다.

**Cypher 안전 규칙**: 라벨과 관계 유형은 Cypher 파라미터로 넘길 수 없어 문자열로 조립해야
한다. 그래서 조립 전에 **반드시 화이트리스트로 검증한다**(`_safe_label`·`_safe_rel`).
값은 전부 파라미터로 넘긴다 — 이 파일에서 f-string에 들어가도 되는 것은 화이트리스트를
통과한 라벨·관계유형뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from .config import ENTITY_TYPE_TO_LABEL, ENTITY_TYPE_TO_RELATION

logger = logging.getLogger(__name__)

# 조립이 허용되는 라벨·관계유형. 여기 없는 값은 예외를 던진다.
_ALLOWED_LABELS: frozenset[str] = frozenset(ENTITY_TYPE_TO_LABEL.values()) | {
    "CanonicalEntity",
    "Project",
    "Document",
    "Chunk",
    "Mention",
}
_ALLOWED_RELS: frozenset[str] = frozenset(ENTITY_TYPE_TO_RELATION.values()) | {
    "APPLIED_TO",
    "SHARES_ENTITY",
    "HAS_DOCUMENT",
    "HAS_CHUNK",
    "HAS_MENTION",
    "REFERS_TO",
}

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT kg_project_id IF NOT EXISTS FOR (p:Project) REQUIRE p.project_id IS UNIQUE",
    "CREATE CONSTRAINT kg_document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
    "CREATE CONSTRAINT kg_chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT kg_mention_id IF NOT EXISTS FOR (m:Mention) REQUIRE m.mention_id IS UNIQUE",
    "CREATE CONSTRAINT kg_canonical_id IF NOT EXISTS"
    " FOR (c:CanonicalEntity) REQUIRE c.canonical_id IS UNIQUE",
    # 검색·시각화가 매번 쓰는 축들
    "CREATE INDEX kg_canonical_norm IF NOT EXISTS FOR (c:CanonicalEntity) ON (c.normalized_name)",
    "CREATE INDEX kg_canonical_df IF NOT EXISTS FOR (c:CanonicalEntity) ON (c.document_frequency)",
    "CREATE INDEX kg_canonical_type IF NOT EXISTS FOR (c:CanonicalEntity) ON (c.entity_type)",
    "CREATE INDEX kg_canonical_build IF NOT EXISTS FOR (c:CanonicalEntity) ON (c.build_id)",
    "CREATE INDEX kg_mention_build IF NOT EXISTS FOR (m:Mention) ON (m.build_id)",
    "CREATE FULLTEXT INDEX kg_canonical_text IF NOT EXISTS"
    " FOR (c:CanonicalEntity) ON EACH [c.canonical_name, c.aliases_text]",
)


class KgGraphStoreError(RuntimeError):
    """Neo4j 접근 실패."""


def _safe_label(label: str) -> str:
    if label not in _ALLOWED_LABELS:
        raise KgGraphStoreError(f"허용되지 않은 라벨: {label!r}")
    return label


def _safe_rel(rel: str) -> str:
    if rel not in _ALLOWED_RELS:
        raise KgGraphStoreError(f"허용되지 않은 관계 유형: {rel!r}")
    return rel


class KgGraphStore:
    """M_23 정규 엔티티 그래프의 Neo4j 스토어."""

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

    # ── 연결 ──────────────────────────────────────────────────────────────────

    def _get_driver(self) -> Any:
        if self._driver is None:
            try:
                from neo4j import GraphDatabase

                self._driver = GraphDatabase.driver(
                    self._uri,
                    auth=(self._user, self._password),
                    connection_timeout=5.0,
                    max_transaction_retry_time=10.0,
                )
            except Exception as exc:
                raise KgGraphStoreError(f"Neo4j 드라이버 생성 실패: {exc}") from exc
        return self._driver

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        try:
            driver = self._get_driver()
            with driver.session(database=self._database) as session:
                return [dict(r) for r in session.run(query, **params)]
        except KgGraphStoreError:
            raise
        except Exception as exc:
            raise KgGraphStoreError(f"Cypher 실행 실패: {exc}") from exc

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    def ping(self) -> bool:
        try:
            self._run("RETURN 1 AS ok")
            return True
        except Exception as exc:
            logger.debug("KG Neo4j ping 실패: %s", exc)
            return False

    def ensure_schema(self) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            try:
                self._run(stmt)
            except KgGraphStoreError as exc:
                # 전문검색 인덱스는 버전에 따라 문법이 달라 실패할 수 있다 — 치명적이지 않다.
                if "FULLTEXT" in stmt:
                    logger.warning("전문검색 인덱스 생성 건너뜀: %s", exc)
                    continue
                raise
        logger.info("KG Neo4j 스키마 제약/인덱스 확인 완료")

    # ── 적재 ──────────────────────────────────────────────────────────────────

    def upsert_projects(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MERGE (p:Project {project_id: row.project_id}) "
            "SET p.title = row.title, p.normalized_title = row.normalized_title, "
            "    p.project_id_source = row.project_id_source, p.year = row.year, "
            "    p.project_no = row.project_no, p.document_count = row.document_count, "
            "    p.build_id = row.build_id",
            rows=list(rows),
        )

    def upsert_documents(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MERGE (d:Document {doc_id: row.doc_id}) "
            "SET d.doc_name = row.doc_name, d.title = row.title, d.year = row.year, "
            "    d.document_type = row.document_type, d.folder_name = row.folder_name, "
            "    d.name = row.doc_name, d.build_id = row.build_id "
            "WITH d, row MATCH (p:Project {project_id: row.project_id}) "
            "MERGE (p)-[:HAS_DOCUMENT]->(d)",
            rows=list(rows),
        )

    def upsert_canonicals(self, rows: Sequence[dict[str, Any]]) -> None:
        """유형 라벨을 함께 붙인다. 라벨은 파라미터가 안 되므로 유형별로 나눠 실행한다."""
        if not rows:
            return
        by_label: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            label = _safe_label(ENTITY_TYPE_TO_LABEL.get(r["entity_type"], "CanonicalEntity"))
            by_label.setdefault(label, []).append(r)
        for label, group in by_label.items():
            self._run(
                "UNWIND $rows AS row "
                "MERGE (c:CanonicalEntity {canonical_id: row.canonical_id}) "
                f"SET c:{label}, "
                "    c.canonical_name = row.canonical_name, "
                "    c.normalized_name = row.normalized_name, "
                "    c.entity_type = row.entity_type, c.target_key = row.target_key, "
                "    c.aliases = row.aliases, c.aliases_text = row.aliases_text, "
                "    c.review_status = row.review_status, "
                "    c.mention_count = row.mention_count, "
                "    c.document_frequency = row.document_frequency, "
                "    c.is_boilerplate = row.is_boilerplate, "
                "    c.from_target_key = row.from_target_key, "
                "    c.build_id = row.build_id",
                rows=group,
            )

    def upsert_chunks(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MERGE (c:Chunk {chunk_id: row.chunk_id}) "
            "SET c.page = row.page, c.text_preview = row.text_preview, "
            "    c.build_id = row.build_id "
            "WITH c, row MATCH (d:Document {doc_id: row.doc_id}) "
            "MERGE (d)-[:HAS_CHUNK]->(c)",
            rows=list(rows),
        )

    def upsert_mentions(self, rows: Sequence[dict[str, Any]]) -> None:
        """Mention과 그 양쪽 엣지(Chunk→Mention, Mention→CanonicalEntity)를 한 번에."""
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MERGE (m:Mention {mention_id: row.mention_id}) "
            "SET m.surface_form = row.surface_form, m.entity_type = row.entity_type, "
            "    m.statement_status = row.statement_status, "
            "    m.derived_status = row.derived_status, m.status_source = row.status_source, "
            "    m.project_relevance = row.project_relevance, m.confidence = row.confidence, "
            "    m.evidence = row.evidence, m.page = row.page, "
            "    m.section_hint = row.section_hint, m.extractor_model = row.extractor_model, "
            "    m.extractor_version = row.extractor_version, m.build_id = row.build_id "
            "WITH m, row "
            "MATCH (c:CanonicalEntity {canonical_id: row.canonical_id}) "
            "MERGE (m)-[:REFERS_TO]->(c) "
            "WITH m, row "
            "MATCH (ch:Chunk {chunk_id: row.chunk_id}) "
            "MERGE (ch)-[:HAS_MENTION]->(m)",
            rows=list(rows),
        )

    def upsert_project_relations(self, rel_type: str, rows: Sequence[dict[str, Any]]) -> None:
        """(Project)-[:REL]->(CanonicalEntity) 집계 엣지."""
        if not rows:
            return
        rel = _safe_rel(rel_type)
        self._run(
            "UNWIND $rows AS row "
            "MATCH (p:Project {project_id: row.project_id}) "
            "MATCH (c:CanonicalEntity {canonical_id: row.canonical_id}) "
            f"MERGE (p)-[r:{rel}]->(c) "
            "SET r.derived_status = row.derived_status, r.status_source = row.status_source, "
            "    r.confidence = row.confidence, r.mention_count = row.mention_count, "
            "    r.source_document_ids = row.source_document_ids, r.build_id = row.build_id",
            rows=list(rows),
        )

    def upsert_applied_to(self, rows: Sequence[dict[str, Any]]) -> None:
        """(CanonicalEntity)-[:APPLIED_TO]->(:ResearchTarget) — 연결성의 핵심 엣지."""
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MATCH (s:CanonicalEntity {canonical_id: row.source_id}) "
            "MATCH (t:CanonicalEntity {canonical_id: row.target_id}) "
            "MERGE (s)-[r:APPLIED_TO]->(t) "
            "SET r.mention_count = row.mention_count, r.build_id = row.build_id",
            rows=list(rows),
        )

    def upsert_shares_entity(self, rows: Sequence[dict[str, Any]]) -> None:
        """(Document)-[:SHARES_ENTITY]-(Document) — 중복성 분석용 무방향 가중 엣지."""
        if not rows:
            return
        self._run(
            "UNWIND $rows AS row "
            "MATCH (a:Document {doc_id: row.source_id}) "
            "MATCH (b:Document {doc_id: row.target_id}) "
            "MERGE (a)-[r:SHARES_ENTITY]->(b) "
            "SET r.weight = row.weight, r.shared_count = row.shared_count, "
            "    r.build_id = row.build_id",
            rows=list(rows),
        )

    # ── 조회 (그래프 탭 시각화) ───────────────────────────────────────────────
    #
    # 반환 형태를 M_19 `GraphSnapshot`과 맞춘다 — 노드 {id,label,kind,type} · 엣지
    # {source,target,kind,weight}. 프론트(`GraphRagView.tsx`, 1,636줄)를 다시 쓰지 않고
    # 데이터 출처만 바꾸기 위해서다. 달라지는 것은 kind가 keyword → entity 인 것과
    # type이 역할 4종 → 엔티티 유형 7종인 것뿐이다.

    def snapshot(
        self,
        limit: int = 500,
        entity_types: list[str] | None = None,
        min_df: int = 2,
        include_boilerplate: bool = False,
    ) -> dict[str, Any]:
        """개요 그래프.

        **df 하한이 핵심이다.** 정규 엔티티가 207,674개라 전부 그리면 아무것도 안 보인다
        (CR-34에서 6,276개로도 이미 헤어볼이었다). 기본은 2문서 이상 공유하는 엔티티만
        — 그것이 과제 간 연결을 만드는 노드들이고, 나머지는 문서를 골랐을 때
        `doc_focus_snapshot`으로 따로 보여준다.
        """
        limit = max(1, min(int(limit), 3000))
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        types = entity_types or list(ENTITY_TYPE_TO_LABEL.keys())
        # 유형별로 고르게 뽑는다. df 순으로만 자르면 상위가 전부 RESEARCH_TARGET(작물)이라
        # 기술·방법·산출물이 화면에서 사라진다(실측: 200개 중 38개가 작물, 기술은 1개).
        per_type = max(3, limit // max(len(types), 1))
        # 엔티티 하나에 매달 과제 수 상한. '벼'는 과제 422개에 걸려 있어서 상한이 없으면
        # 그 하나가 화면을 문서로 가득 채운다 — 별들의 숲을 고쳤더니 문서 헤어볼이 된다.
        projects_per_entity = 4

        bp_clause = "" if include_boilerplate else " AND coalesce(c.is_boilerplate,false) = false"

        rows = self._run(
            "UNWIND $types AS t "
            "MATCH (c:CanonicalEntity {entity_type: t}) "
            f"WHERE coalesce(c.document_frequency,0) >= $min_df{bp_clause} "
            "WITH t, c ORDER BY c.document_frequency DESC "
            "WITH t, collect(c)[0..$per_type] AS cs "
            "UNWIND cs AS c "
            "RETURN c.canonical_id AS cid, c.canonical_name AS cname, "
            "       c.entity_type AS ctype, coalesce(c.document_frequency,0) AS df",
            types=types,
            min_df=min_df,
            per_type=per_type,
        )
        for row in rows:
            cid = str(row["cid"])
            nodes[cid] = {
                "id": cid,
                "label": str(row["cname"] or cid),
                "kind": "entity",
                "type": str(row["ctype"] or ""),
            }
        ent_ids = list(nodes.keys())
        if not ent_ids:
            return {"nodes": [], "edges": []}

        # 엔티티마다 대표 과제 몇 개만 — collect()[0..n]으로 엔티티별 상한을 건다.
        for row in self._run(
            "UNWIND $ids AS cid "
            "MATCH (c:CanonicalEntity {canonical_id: cid})<-[r]-(p:Project) "
            "WITH cid, collect({pid: p.project_id, "
            "                   label: coalesce(p.title, p.project_id), "
            "                   rel: type(r)})[0..$cap] AS ps "
            "UNWIND ps AS x "
            "RETURN cid, x.pid AS pid, x.label AS plabel, x.rel AS rel",
            ids=ent_ids,
            cap=projects_per_entity,
        ):
            cid = str(row["cid"])
            pid = str(row["pid"])
            if pid not in nodes:
                nodes[pid] = {"id": pid, "label": str(row["plabel"] or pid), "kind": "document"}
            edges.append(
                {
                    "source": pid,
                    "target": cid,
                    "kind": str(row["rel"] or "rel").lower(),
                    "weight": 1.0,
                }
            )

        # 엔티티 → 대상 허브 (APPLIED_TO). 이게 있어야 '별들의 숲'이 아니라 망으로 보인다.
        for row in self._run(
            "MATCH (s:CanonicalEntity)-[:APPLIED_TO]->(t:CanonicalEntity) "
            "WHERE s.canonical_id IN $ids AND t.canonical_id IN $ids "
            "RETURN s.canonical_id AS src, t.canonical_id AS tgt LIMIT $limit",
            ids=ent_ids,
            limit=limit * 4,
        ):
            edges.append(
                {
                    "source": str(row["src"]),
                    "target": str(row["tgt"]),
                    "kind": "applied_to",
                    "weight": 1.5,
                }
            )

        # 화면에 올라온 과제끼리의 중복성 — 이 탭의 존재 이유다.
        doc_ids = [n["id"] for n in nodes.values() if n["kind"] == "document"]
        if doc_ids:
            for row in self._run(
                "MATCH (a:Document)-[r:SHARES_ENTITY]->(b:Document) "
                "MATCH (pa:Project)-[:HAS_DOCUMENT]->(a) "
                "MATCH (pb:Project)-[:HAS_DOCUMENT]->(b) "
                "WHERE pa.project_id IN $ids AND pb.project_id IN $ids "
                "RETURN pa.project_id AS src, pb.project_id AS tgt, r.weight AS w "
                "ORDER BY r.weight DESC LIMIT $limit",
                ids=doc_ids,
                limit=limit,
            ):
                edges.append(
                    {
                        "source": str(row["src"]),
                        "target": str(row["tgt"]),
                        "kind": "shares_entity",
                        "weight": float(row["w"] or 1.0),
                    }
                )
        return {"nodes": list(nodes.values()), "edges": edges}

    def doc_focus_snapshot(self, doc_id: str, limit: int = 60) -> dict[str, Any]:
        """문서 하나를 중심으로 — 그 과제의 엔티티 전부(df 하한 없음)와 겹치는 과제들.

        개요에서 감춰진 단일문서 엔티티도 여기서는 다 보여준다. 그 문서를 이해하려면
        그 문서에만 있는 내용이 오히려 핵심이기 때문이다.
        """
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        for row in self._run(
            "MATCH (d:Document {doc_id: $doc_id}) "
            "OPTIONAL MATCH (p:Project)-[:HAS_DOCUMENT]->(d) "
            "WITH d, p LIMIT 1 "
            "MATCH (p)-[r]->(c:CanonicalEntity) "
            "RETURN d.doc_id AS did, coalesce(d.title, d.doc_name) AS dlabel, "
            "       c.canonical_id AS cid, c.canonical_name AS cname, "
            "       c.entity_type AS ctype, type(r) AS rel "
            "LIMIT $limit",
            doc_id=doc_id,
            limit=limit,
        ):
            did = str(row["did"])
            if did not in nodes:
                nodes[did] = {"id": did, "label": str(row["dlabel"] or did), "kind": "document"}
            cid = str(row["cid"])
            if cid not in nodes:
                nodes[cid] = {
                    "id": cid,
                    "label": str(row["cname"] or cid),
                    "kind": "entity",
                    "type": str(row["ctype"] or ""),
                }
            edges.append(
                {
                    "source": did,
                    "target": cid,
                    "kind": str(row["rel"] or "rel").lower(),
                    "weight": 1.0,
                }
            )

        # 중복성 — 엔티티를 공유하는 다른 문서
        for row in self._run(
            "MATCH (a:Document {doc_id: $doc_id})-[r:SHARES_ENTITY]-(b:Document) "
            "RETURN b.doc_id AS did, coalesce(b.title, b.doc_name) AS dlabel, "
            "       r.weight AS w, r.shared_count AS n ORDER BY r.weight DESC LIMIT 12",
            doc_id=doc_id,
        ):
            did = str(row["did"])
            if did not in nodes:
                nodes[did] = {"id": did, "label": str(row["dlabel"] or did), "kind": "document"}
            edges.append(
                {
                    "source": doc_id,
                    "target": did,
                    "kind": "shares_entity",
                    "weight": float(row["w"] or 1.0),
                }
            )
        return {"nodes": list(nodes.values()), "edges": edges}

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """제목·엔티티 이름으로 과제(문서) 검색. 결과는 문서만 (CR-31)."""
        q = (query or "").strip().lower()
        if not q:
            return []
        rows = self._run(
            "MATCH (d:Document) WHERE toLower(coalesce(d.title, d.doc_name)) CONTAINS $q "
            "RETURN d.doc_id AS doc_id, coalesce(d.title, d.doc_name) AS title, "
            "       d.doc_name AS doc_name, d.year AS year, "
            "       d.document_type AS document_type, 2.0 AS score "
            "LIMIT $limit "
            "UNION "
            "MATCH (c:CanonicalEntity) WHERE toLower(c.canonical_name) CONTAINS $q "
            "WITH c LIMIT 40 "
            "MATCH (p:Project)-[]->(c) MATCH (p)-[:HAS_DOCUMENT]->(d:Document) "
            "RETURN d.doc_id AS doc_id, coalesce(d.title, d.doc_name) AS title, "
            "       d.doc_name AS doc_name, d.year AS year, "
            "       d.document_type AS document_type, 1.0 AS score "
            "LIMIT $limit",
            q=q,
            limit=limit,
        )
        seen: dict[str, dict[str, Any]] = {}
        for r in rows:
            did = str(r["doc_id"])
            if did not in seen or r["score"] > seen[did]["score"]:
                seen[did] = dict(r)
        return sorted(seen.values(), key=lambda x: -x["score"])[:limit]

    def graph_stats(self) -> dict[str, int]:
        """그래프 탭 상단에 띄울 요약."""
        out = self.stats()
        rows = self._run(
            "MATCH (c:CanonicalEntity) WHERE coalesce(c.document_frequency,0) >= 2"
            " RETURN count(c) AS c"
        )
        out["shared_entities"] = rows[0]["c"] if rows else 0
        return out

    # ── 세대 관리 ─────────────────────────────────────────────────────────────

    def delete_stale(self, build_id: str, labels: Iterable[str], batch: int = 5000) -> int:
        """이번 세대에 속하지 않는 노드를 배치로 지운다.

        대표 이름이 바뀌면 `canonical_id` 해시도 바뀌어 옛 노드가 고아로 남는다.
        단일 거대 트랜잭션으로 지우면 힙이 터지므로 LIMIT 루프로 나눈다.
        """
        total = 0
        for raw in labels:
            label = _safe_label(raw)
            while True:
                rows = self._run(
                    f"MATCH (n:{label}) WHERE n.build_id IS NULL OR n.build_id <> $build_id "
                    "WITH n LIMIT $batch DETACH DELETE n RETURN count(n) AS deleted",
                    build_id=build_id,
                    batch=batch,
                )
                deleted = rows[0]["deleted"] if rows else 0
                total += deleted
                if deleted < batch:
                    break
        if total:
            logger.info("KG 적재: 옛 세대 노드 %d개 삭제 (build_id != %s)", total, build_id)
        return total

    # M_19 전용 라벨. **이 목록에 M_23 라벨이 절대 들어가면 안 된다.**
    # M_23은 CanonicalEntity + 유형 라벨(ResearchTarget·Technology…)을 쓰고,
    # 아래 셋 중 어느 것도 쓰지 않는다. Document는 두 그래프가 공유하므로 제외한다 —
    # 지우면 사용자 노트 연결과 M_23 문서 노드까지 함께 날아간다.
    _LEGACY_LABELS: tuple[str, ...] = ("Keyword", "Entity", "TechnologyCode")

    def purge_preflight(self) -> dict[str, Any]:
        """삭제해도 되는 상태인지 먼저 센다. **아무것도 지우지 않는다.**

        되돌릴 수 없는 작업이라 호출자가 숫자를 보고 판단할 수 있어야 한다.
        """
        counts = self.stats()
        legacy = {
            label: (self._run(f"MATCH (n:{label}) RETURN count(n) AS c") or [{"c": 0}])[0]["c"]
            for label in self._LEGACY_LABELS
        }
        # M_23 라벨이 legacy 라벨을 겸하고 있으면 삭제가 곧 데이터 손실이다.
        overlap = (
            self._run(
                "MATCH (n:CanonicalEntity) WHERE n:Keyword OR n:Entity OR n:TechnologyCode"
                " RETURN count(n) AS c"
            )
            or [{"c": 0}]
        )[0]["c"]
        ready = counts.get("CanonicalEntity", 0) > 0 and counts.get("Mention", 0) > 0
        return {
            "m23": counts,
            "legacy": legacy,
            "label_overlap": overlap,
            "safe": bool(ready and overlap == 0),
            "reason": (
                ""
                if ready and overlap == 0
                else (
                    "M_23 그래프가 비어 있습니다 — 먼저 구축하세요."
                    if not ready
                    else f"M_23 노드 {overlap}개가 legacy 라벨을 겸하고 있습니다 — 삭제하면 함께 사라집니다."
                )
            ),
        }

    def purge_legacy_keyword_graph(self, batch: int = 5000, force: bool = False) -> dict[str, Any]:
        """M_19 키워드 그래프를 지운다 (CR-61 완전 교체).

        **안전장치**: M_23 그래프가 실제로 적재돼 있지 않으면 거부한다. 새 그래프가 없는데
        옛 그래프를 지우면 검색이 통째로 죽고, 되돌리려면 재적재가 필요하다.
        `force=True`는 그 판단을 호출자가 떠안는다는 뜻이다.

        `Document`는 지우지 않는다 — 두 그래프가 공유한다.
        """
        pre = self.purge_preflight()
        if not pre["safe"] and not force:
            logger.warning("M_19 그래프 삭제 거부: %s", pre["reason"])
            return {"purged": False, "reason": pre["reason"], "preflight": pre}

        removed: dict[str, int] = {}
        for label in self._LEGACY_LABELS:
            count = 0
            while True:
                rows = self._run(
                    f"MATCH (n:{label}) WITH n LIMIT $batch DETACH DELETE n"
                    " RETURN count(n) AS deleted",
                    batch=batch,
                )
                deleted = rows[0]["deleted"] if rows else 0
                count += deleted
                if deleted < batch:
                    break
            removed[label] = count

        post = self.stats()
        # 삭제 후 M_23이 멀쩡한지 확인한다 — 여기서 줄었다면 라벨 설계가 틀린 것이다.
        for key in ("CanonicalEntity", "Mention", "Project"):
            if post.get(key, 0) < pre["m23"].get(key, 0):
                logger.error(
                    "M_19 삭제가 M_23 노드를 건드렸다! %s: %d → %d",
                    key,
                    pre["m23"].get(key, 0),
                    post.get(key, 0),
                )
        logger.info("M_19 키워드 그래프 삭제: %s (M_23 이후 상태: %s)", removed, post)
        return {"purged": True, "removed": removed, "m23_before": pre["m23"], "m23_after": post}

    def clear_all(self, batch: int = 5000) -> dict[str, int]:
        """그래프를 비운다 — **Neo4j만.** 추출 후보는 손대지 않는다.

        M_19의 `clear_all`은 `Document`·`Chunk`를 지우는데 그 둘은 M_23도 쓴다. 그대로 두면
        그래프 탭의 초기화 버튼 한 번에 Mention 216,509개가 통째로 고아가 된다 —
        노드는 남고 연결만 끊겨서 "지워지지 않았는데 아무것도 안 보이는" 최악의 상태다.
        그래서 M_23을 아는 초기화를 따로 둔다.

        **여기서 지우는 것은 전부 `data/kg_candidates.db`에서 몇 분이면 다시 만들 수 있다**
        (`kg_build.py load`). 26시간짜리 추출 결과인 entity_candidates는 이 함수가
        닿지 않는 다른 저장소에 있다.
        """
        before = self.stats()
        labels = (
            "Mention",
            "Chunk",
            "CanonicalEntity",
            "Project",
            "Document",
        ) + self._LEGACY_LABELS
        for raw in labels:
            label = raw if raw in self._LEGACY_LABELS else _safe_label(raw)
            while True:
                rows = self._run(
                    f"MATCH (n:{label}) WITH n LIMIT $batch DETACH DELETE n"
                    " RETURN count(n) AS deleted",
                    batch=batch,
                )
                if (rows[0]["deleted"] if rows else 0) < batch:
                    break
        logger.info("KG 그래프 초기화(Neo4j 전용, 후보 저장소는 보존): %s", before)
        return before

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for label in ("Project", "Document", "Chunk", "Mention", "CanonicalEntity"):
            rows = self._run(f"MATCH (n:{label}) RETURN count(n) AS c")
            out[label] = rows[0]["c"] if rows else 0
        rows = self._run("MATCH ()-[r]->() RETURN count(r) AS c")
        out["relationships"] = rows[0]["c"] if rows else 0
        return out


__all__ = ["KgGraphStore", "KgGraphStoreError"]

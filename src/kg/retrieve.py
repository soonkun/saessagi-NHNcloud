# src/kg/retrieve.py
"""M_23 그래프 검색 — M_19 키워드 검색을 대체한다 (CR-61 완전 교체).

M_19의 `graph_retrieve`는 질의어를 `Keyword` 노드에 맞추고 그 키워드를 가진 문서를 돌려줬다.
키워드가 **문서 스코프**(`doc_id::term::role`)였기 때문에 같은 용어라도 문서마다 다른
노드였고, 그래서 문서를 가로지르는 신호가 원리적으로 없었다(CR-34).

여기서는 정규 엔티티에 맞춘다. 한 엔티티가 여러 문서에 걸쳐 있으므로 매칭 하나가 곧
문서 간 연결이다.

**랭킹의 핵심은 df 역가중이다.** 실측상 가장 많은 문서에 걸린 엔티티는 `산업재산권 출원`
(206문서)·`학술발표`(139)·`논문 게재 SCI`(114) 같은 행정 상용구다. 매칭 수만 세면 이런
노드가 검색 결과를 지배한다 — M_19가 겪은 허브 폭주(CR-36)와 같은 실패다. 그래서 흔한
엔티티일수록 가볍게 친다(IDF).

질의 경로에 LLM은 없다 — 이름 매칭과 산술뿐이다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .graph_store import KgGraphStore, KgGraphStoreError
from .merge import normalize_name

logger = logging.getLogger(__name__)

# 질의에서 후보 용어 뽑기 — M_19와 같은 규칙을 쓴다 (동작 일관성).
_TERM_RE = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣._-]{1,}")

_SCORE_MIN = 0.40
_SCORE_MAX = 0.90


@dataclass
class EntityMatch:
    """질의어에 걸린 정규 엔티티 하나."""

    canonical_id: str
    canonical_name: str
    entity_type: str
    document_frequency: int
    is_boilerplate: bool
    doc_ids: list[str]


def _hit_to_row(hit: Any) -> dict[str, Any]:
    """SearchHit → LanceDB row 형태 (E-102).

    `GraphRagService._row_to_hit`이 row dict를 기대하므로 형태를 맞춰 돌려준다.
    변환을 두 번 하는 셈이지만, 두 경로(문서 내 검색 · 앞청크 폴백)가 **같은 형태**를
    내보내야 호출자가 분기 없이 처리한다.
    """
    return {
        "doc_id": hit.doc_id,
        "doc_name": hit.doc_name,
        "category": hit.category,
        "page": hit.page,
        "section": hit.section,
        "chunk_id": hit.chunk_id,
        "text": hit.text,
        "bbox": list(hit.bbox) if hit.bbox else None,
        "source_path": hit.source_path,
    }


class KgRetriever:
    """정규 엔티티 그래프 기반 검색기."""

    def __init__(
        self,
        graph: KgGraphStore,
        total_documents: int = 6121,
        max_terms: int = 12,
        boilerplate_penalty: float = 0.15,
    ) -> None:
        self._graph = graph
        self._total_documents = max(total_documents, 1)
        self._max_terms = max_terms
        self._boilerplate_penalty = boilerplate_penalty

    # ── 조회 ──────────────────────────────────────────────────────────────────

    def find_entities(self, terms: list[str], limit: int = 60) -> list[EntityMatch]:
        """질의어와 이름·별칭이 겹치는 정규 엔티티와 그 문서들을 찾는다."""
        if not terms:
            return []
        normalized = [normalize_name(t) for t in terms]
        normalized = [t for t in normalized if len(t) >= 2]
        if not normalized:
            return []
        rows = self._graph._run(  # noqa: SLF001 — 같은 패키지 내부 조회
            "UNWIND $terms AS term "
            "MATCH (c:CanonicalEntity) "
            "WHERE c.normalized_name = term OR c.normalized_name CONTAINS term "
            "   OR any(a IN c.aliases WHERE toLower(a) CONTAINS term) "
            "WITH DISTINCT c LIMIT $limit "
            "MATCH (m:Mention)-[:REFERS_TO]->(c) "
            "MATCH (ch:Chunk)-[:HAS_MENTION]->(m) "
            "MATCH (d:Document)-[:HAS_CHUNK]->(ch) "
            "RETURN c.canonical_id AS canonical_id, c.canonical_name AS canonical_name, "
            "       c.entity_type AS entity_type, "
            "       coalesce(c.document_frequency, 1) AS df, "
            "       coalesce(c.is_boilerplate, false) AS is_boilerplate, "
            "       collect(DISTINCT d.doc_id) AS doc_ids",
            terms=normalized,
            limit=limit,
        )
        return [
            EntityMatch(
                canonical_id=r["canonical_id"],
                canonical_name=r["canonical_name"],
                entity_type=r["entity_type"],
                document_frequency=int(r["df"] or 1),
                is_boilerplate=bool(r["is_boilerplate"]),
                doc_ids=list(r["doc_ids"] or []),
            )
            for r in rows
        ]

    def rank_documents(self, matches: list[EntityMatch]) -> list[tuple[str, float]]:
        """문서를 df 역가중 합으로 순위매긴다.

        희소한 엔티티가 걸린 문서가 위로 온다. 상용구로 표시된 엔티티는 추가로 감점한다 —
        `산업재산권 출원`이 걸렸다는 사실이 그 문서를 질의에 더 가깝게 만들지는 않는다.
        """
        scores: dict[str, float] = {}
        for m in matches:
            df = max(m.document_frequency, 1)
            # IDF. df=1이면 최대, 코퍼스 전체에 퍼져 있으면 0에 수렴.
            weight = math.log(1.0 + self._total_documents / df)
            if m.is_boilerplate:
                weight *= self._boilerplate_penalty
            for did in m.doc_ids:
                scores[did] = scores.get(did, 0.0) + weight
        return sorted(scores.items(), key=lambda kv: -kv[1])

    def overlapping_documents(self, doc_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """중복성 분석 — 이 문서와 엔티티를 많이 공유하는 문서들."""
        rows = self._graph._run(  # noqa: SLF001
            "MATCH (a:Document {doc_id: $doc_id})-[r:SHARES_ENTITY]-(b:Document) "
            "RETURN b.doc_id AS doc_id, b.title AS title, b.year AS year, "
            "       b.document_type AS document_type, r.weight AS weight, "
            "       r.shared_count AS shared_count "
            "ORDER BY r.weight DESC LIMIT $limit",
            doc_id=doc_id,
            limit=limit,
        )
        return [dict(r) for r in rows]

    def shared_entities(self, doc_a: str, doc_b: str, limit: int = 40) -> list[dict[str, Any]]:
        """두 문서가 실제로 무엇을 공유하는지 — 중복성 판단의 근거."""
        rows = self._graph._run(  # noqa: SLF001
            "MATCH (a:Document {doc_id: $a})-[:HAS_CHUNK]->(:Chunk)-[:HAS_MENTION]->"
            "      (:Mention)-[:REFERS_TO]->(c:CanonicalEntity) "
            "MATCH (b:Document {doc_id: $b})-[:HAS_CHUNK]->(:Chunk)-[:HAS_MENTION]->"
            "      (:Mention)-[:REFERS_TO]->(c) "
            "WHERE coalesce(c.is_boilerplate, false) = false "
            "RETURN DISTINCT c.canonical_name AS name, c.entity_type AS entity_type, "
            "       coalesce(c.document_frequency, 1) AS df "
            "ORDER BY df ASC LIMIT $limit",
            a=doc_a,
            b=doc_b,
            limit=limit,
        )
        return [dict(r) for r in rows]

    # ── 검색 진입점 ───────────────────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        vstore: Any,
        top_k: int = 5,
        query_vec: Any = None,
    ) -> tuple[list[dict[str, Any]], list[EntityMatch]]:
        """질의 → 엔티티 매칭 → 문서 랭킹 → 대표 청크 행.

        LanceDB 행을 그대로 돌려준다. `SearchHit` 변환은 호출자(GraphRagService)가 이미
        갖고 있는 `_row_to_hit`이 맡는다 — 두 곳에서 같은 변환을 하지 않기 위해서다.
        """
        terms = _TERM_RE.findall(query or "")[: self._max_terms]
        if not terms:
            return [], []

        loop = asyncio.get_running_loop()
        try:
            matches = await loop.run_in_executor(None, lambda: self.find_entities(terms))
        except KgGraphStoreError as exc:
            logger.warning("KG 그래프 질의 실패 — 벡터 결과만 사용: %s", exc)
            return [], []
        if not matches:
            return [], []

        ranked = self.rank_documents(matches)[: top_k * 2]
        if not ranked:
            return [], []
        max_score = ranked[0][1] or 1.0

        # E-102: 문서를 골랐으면 **그 문서 안에서 질의로 다시** 청크를 고른다.
        # 예전에는 `get_chunks_by_doc_id(doc, limit=2)`로 앞 2청크를 집어 표지·제출문이
        # 근거로 갔다(실측 최종 근거의 28%가 1~2페이지). 문서 선별이라는 그래프의
        # 강점을 마지막 한 줄에서 버리고 있었다.
        # `query_vec`이 없으면(호출자가 임베더를 못 주면) 예전 방식으로 폴백한다 —
        # 근거가 아예 없는 것보다는 낫다.
        use_vec = query_vec is not None and hasattr(vstore, "search_in_doc")
        fallback_used = not use_vec

        def _fetch(doc: str) -> list[dict[str, Any]]:
            if use_vec:
                hits = vstore.search_in_doc(query_vec, doc, top_k=2)
                if hits:
                    return [_hit_to_row(h) for h in hits]
                # 그 문서에서 못 고르면 예전 경로로 (문서가 통째로 비는 것보다 낫다)
            result: list[dict[str, Any]] = vstore.get_chunks_by_doc_id(doc, limit=2)
            return result

        rows: list[dict[str, Any]] = []
        for doc_id, score in ranked:
            chunk_rows = await loop.run_in_executor(None, _fetch, doc_id)
            norm = _SCORE_MIN + (_SCORE_MAX - _SCORE_MIN) * (score / max_score)
            for row in chunk_rows:
                enriched = dict(row)
                enriched["_kg_score"] = norm
                rows.append(enriched)
                if len(rows) >= top_k:
                    break
            if len(rows) >= top_k:
                break

        logger.info(
            "KG 그래프 검색: 용어=%d, 엔티티=%d, 문서=%d, hits=%d, 청크선택=%s (query=%r)",
            len(terms),
            len(matches),
            len(ranked),
            len(rows),
            "앞청크(폴백)" if fallback_used else "문서내검색",
            (query or "")[:50],
        )
        return rows, matches


def evidence_payload(
    query: str,
    matches: list[EntityMatch],
    chunk_ids: list[str],
    graph: KgGraphStore | None = None,
) -> dict[str, Any]:
    """그래프 탭 근거 서브그래프용 노드/엣지 (M_19 EvidenceSubgraph와 같은 형태).

    형태를 맞추는 이유는 `GraphRagView.tsx`(1,636줄)를 다시 쓰지 않기 위해서다.
    `kind`만 keyword → entity로 바뀐다.

    `graph`가 있으면 **개요와 같은 노드 규약**으로 만든다 (E-101) — 문서 노드 id는
    `Project.project_id`, 라벨은 과제 제목, `doc_id`는 별도 필드. 예전에는 id에 raw
    `doc_id`를, 라벨에도 `doc_id`를 넣어서 프론트가 개요에서 노드를 찾지 못했고
    (근거 94건 중 0건 일치) 노드 이름도 파일명 그대로 그려졌다.

    `graph`가 없으면 예전 형태로 되돌아간다 — 근거가 아예 안 뜨는 것보다는 낫다.
    """
    if graph is not None:
        try:
            snap = graph.evidence_snapshot([m.canonical_id for m in matches])
            if snap["nodes"]:
                return {
                    "query": query,
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "nodes": snap["nodes"],
                    "edges": snap["edges"],
                    "chunk_ids": chunk_ids,
                }
            logger.warning("E-101 근거 서브그래프가 비었다 — 폴백 사용 (매칭 %d건)", len(matches))
        except KgGraphStoreError as exc:
            logger.warning("E-101 근거 서브그래프 조회 실패, 폴백 사용: %s", exc)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for m in matches:
        if m.canonical_id not in nodes:
            nodes[m.canonical_id] = {
                "id": m.canonical_id,
                "label": m.canonical_name,
                "kind": "entity",
                "type": m.entity_type,
            }
        for did in m.doc_ids:
            if did not in nodes:
                nodes[did] = {"id": did, "label": did, "kind": "document", "doc_id": did}
            edges.append({"source": did, "target": m.canonical_id, "kind": "mentions"})
    return {
        "query": query,
        "created": datetime.now().isoformat(timespec="seconds"),
        "nodes": list(nodes.values()),
        "edges": edges,
        "chunk_ids": chunk_ids,
    }


__all__ = ["EntityMatch", "KgRetriever", "evidence_payload"]

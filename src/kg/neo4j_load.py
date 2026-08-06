# src/kg/neo4j_load.py
"""M_23 9단계 — Neo4j 적재 (스펙 §3 (9), §5.2).

SQLite 후보 저장소가 진실의 원본이고 Neo4j는 그것의 조회용 투영이다. 그래서 적재는
**언제든 다시 돌릴 수 있어야** 하고, 다시 돌린 결과가 이전 결과와 같아야 한다.

멱등성은 `build_id` 세대 교체로 얻는다. 이번 적재에 쓰인 모든 노드에 같은 `build_id`를
찍고, 끝나면 그것과 다른 노드를 지운다. `MERGE`만으로는 부족하다 — 대표 이름이 바뀌면
`canonical_id` 해시가 바뀌어 옛 노드가 고아로 남기 때문이다. 실제로 정규화 규칙을 조금만
손봐도 수천 개가 옮겨간다.

메모리는 배치로 흘린다. Mention이 216,509개라 전부 올리면 프로세스가 부담스럽다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .candidates import CandidateStore
from .config import DOC_TYPE_TO_STATUS, ENTITY_TYPE_TO_RELATION, KnowledgeGraphConfig
from .derive import (
    RELATION_APPLIED_TO,
    RELATION_SHARES_ENTITY,
    SOURCE_KIND_DOCUMENT,
    SOURCE_KIND_PROJECT,
    STATUS_DERIVED,
    STATUS_EXTRACTED,
    STATUS_UNKNOWN,
)
from .graph_store import KgGraphStore
from .projects import resolve_projects

logger = logging.getLogger(__name__)

StopFn = Callable[[], bool]
ProgressFn = Callable[[str, int, int], None]


@dataclass
class LoadStats:
    build_id: str = ""
    projects: int = 0
    documents: int = 0
    chunks: int = 0
    canonicals: int = 0
    mentions: int = 0
    project_relations: int = 0
    applied_to: int = 0
    shares_entity: int = 0
    stale_deleted: int = 0
    legacy_purged: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    stopped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "build_id": self.build_id,
            "projects": self.projects,
            "documents": self.documents,
            "chunks": self.chunks,
            "canonicals": self.canonicals,
            "mentions": self.mentions,
            "project_relations": self.project_relations,
            "applied_to": self.applied_to,
            "shares_entity": self.shares_entity,
            "stale_deleted": self.stale_deleted,
            "legacy_purged": self.legacy_purged,
            "seconds": round(self.seconds, 1),
            "stopped": self.stopped,
        }


def _batched(rows: Iterator[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    buf: list[dict[str, Any]] = []
    for r in rows:
        buf.append(r)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def load_graph(
    store: CandidateStore,
    graph: KgGraphStore,
    config: KnowledgeGraphConfig,
    build_id: str | None = None,
    purge_legacy: bool = False,
    chunk_text: Callable[[str], str] | None = None,
    progress: ProgressFn | None = None,
    should_stop: StopFn | None = None,
) -> LoadStats:
    """후보 저장소의 정규 산출물을 Neo4j로 적재한다.

    `purge_legacy=True`면 적재 **전에** M_19 키워드 그래프를 지운다 (CR-61 완전 교체).
    되돌릴 수 없는 유일한 동작이라 기본값은 False이고 호출자가 명시해야 한다.
    """
    stats = LoadStats(build_id=build_id or f"b{int(time.time())}")
    bid = stats.build_id
    batch = config.graph.batch_size
    t0 = time.perf_counter()

    graph.ensure_schema()
    if purge_legacy:
        stats.legacy_purged = graph.purge_legacy_keyword_graph()

    conn = store._conn  # noqa: SLF001 — 같은 패키지 내부 조회

    def stop() -> bool:
        if should_stop is not None and should_stop():
            stats.stopped = True
            return True
        return False

    # ── 과제 ─────────────────────────────────────────────────────────────────
    projects = resolve_projects(store, persist=False)
    for chunk in _batched(
        (
            {
                "project_id": p.project_id,
                "project_id_source": p.project_id_source,
                "title": p.title,
                "normalized_title": p.normalized_title,
                "year": p.year,
                "project_no": p.project_no,
                "document_count": p.document_count,
                "build_id": bid,
            }
            for p in projects
        ),
        batch,
    ):
        if stop():
            return _finish(stats, t0)
        graph.upsert_projects(chunk)
        stats.projects += len(chunk)

    # ── 문서 ─────────────────────────────────────────────────────────────────
    doc_rows = conn.execute(
        "SELECT doc_id, doc_name, title, year, document_type, folder_name, project_id"
        " FROM documents ORDER BY doc_id"
    ).fetchall()
    for chunk in _batched(
        (
            {
                "doc_id": r["doc_id"],
                "doc_name": r["doc_name"],
                "title": r["title"],
                "year": r["year"],
                "document_type": r["document_type"],
                "folder_name": r["folder_name"],
                "project_id": r["project_id"] or f"doc:{r['doc_id']}",
                "build_id": bid,
            }
            for r in doc_rows
        ),
        batch,
    ):
        if stop():
            return _finish(stats, t0)
        graph.upsert_documents(chunk)
        stats.documents += len(chunk)
    if progress:
        progress("load:documents", stats.documents, len(doc_rows))

    # ── 정규 엔티티 ──────────────────────────────────────────────────────────
    total_canon = conn.execute("SELECT COUNT(*) AS c FROM canonical_entities").fetchone()["c"]
    cur = conn.execute(
        "SELECT canonical_id, entity_type, canonical_name, normalized_name, target_key,"
        " aliases_json, review_status, mention_count, document_frequency, is_boilerplate,"
        " from_target_key FROM canonical_entities ORDER BY canonical_id"
    )
    for chunk in _batched(
        (
            {
                "canonical_id": r["canonical_id"],
                "entity_type": r["entity_type"],
                "canonical_name": r["canonical_name"],
                "normalized_name": r["normalized_name"],
                "target_key": r["target_key"],
                "aliases": json.loads(r["aliases_json"] or "[]"),
                "aliases_text": " ".join(json.loads(r["aliases_json"] or "[]")),
                "review_status": r["review_status"],
                "mention_count": r["mention_count"],
                "document_frequency": r["document_frequency"],
                "is_boilerplate": bool(r["is_boilerplate"]),
                "from_target_key": bool(r["from_target_key"]),
                "build_id": bid,
            }
            for r in cur
        ),
        batch,
    ):
        if stop():
            return _finish(stats, t0)
        graph.upsert_canonicals(chunk)
        stats.canonicals += len(chunk)
        if progress and stats.canonicals % (batch * 20) == 0:
            progress("load:canonicals", stats.canonicals, total_canon)

    # ── 청크 · Mention ───────────────────────────────────────────────────────
    if config.graph.create_mentions:
        chunk_rows = conn.execute(
            "SELECT DISTINCT chunk_id, doc_id, MIN(page) AS page FROM entity_candidates"
            " WHERE state != 'REJECTED' AND canonical_id IS NOT NULL"
            " GROUP BY chunk_id, doc_id"
        ).fetchall()
        for group in _batched(
            (
                {
                    "chunk_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "page": r["page"],
                    "text_preview": (chunk_text(r["chunk_id"])[:400] if chunk_text else ""),
                    "build_id": bid,
                }
                for r in chunk_rows
            ),
            batch,
        ):
            if stop():
                return _finish(stats, t0)
            graph.upsert_chunks(group)
            stats.chunks += len(group)
        if progress:
            progress("load:chunks", stats.chunks, len(chunk_rows))

        total_m = conn.execute(
            "SELECT COUNT(*) AS c FROM entity_candidates WHERE canonical_id IS NOT NULL"
            " AND state != 'REJECTED'"
        ).fetchone()["c"]
        mcur = conn.execute(
            "SELECT ec.candidate_id, ec.chunk_id, ec.canonical_id, ec.surface_form,"
            " ec.entity_type, ec.statement_status, ec.project_relevance, ec.confidence,"
            " ec.evidence, ec.page, ec.section_hint, ec.extractor_model, ec.extractor_version,"
            " d.document_type"
            " FROM entity_candidates ec JOIN documents d ON d.doc_id = ec.doc_id"
            " WHERE ec.canonical_id IS NOT NULL AND ec.state != 'REJECTED'"
        )

        def _mention_rows() -> Iterator[dict[str, Any]]:
            for r in mcur:
                raw = r["statement_status"] or "UNCERTAIN"
                if raw != "UNCERTAIN":
                    derived, source = raw, STATUS_EXTRACTED
                else:
                    guess = DOC_TYPE_TO_STATUS.get(r["document_type"], "")
                    derived, source = (
                        (guess, STATUS_DERIVED) if guess else ("UNCERTAIN", STATUS_UNKNOWN)
                    )
                yield {
                    "mention_id": r["candidate_id"],
                    "chunk_id": r["chunk_id"],
                    "canonical_id": r["canonical_id"],
                    "surface_form": r["surface_form"],
                    "entity_type": r["entity_type"],
                    "statement_status": raw,
                    "derived_status": derived,
                    "status_source": source,
                    "project_relevance": r["project_relevance"],
                    "confidence": r["confidence"],
                    "evidence": (r["evidence"] or "")[:1000],
                    "page": r["page"],
                    "section_hint": r["section_hint"],
                    "extractor_model": r["extractor_model"],
                    "extractor_version": r["extractor_version"],
                    "build_id": bid,
                }

        for group in _batched(_mention_rows(), batch):
            if stop():
                return _finish(stats, t0)
            graph.upsert_mentions(group)
            stats.mentions += len(group)
            if progress and stats.mentions % (batch * 20) == 0:
                progress("load:mentions", stats.mentions, total_m)

    # ── 관계 ─────────────────────────────────────────────────────────────────
    if config.graph.create_project_aggregates:
        stats.project_relations = _load_project_relations(conn, graph, bid, batch, stop)
    stats.applied_to = _load_applied_to(conn, graph, bid, batch, stop)
    if config.graph.shares_entity_enabled:
        stats.shares_entity = _load_shares_entity(conn, graph, bid, batch, stop)

    # ── 세대 정리 ────────────────────────────────────────────────────────────
    if not stats.stopped:
        stats.stale_deleted = graph.delete_stale(
            bid, ("CanonicalEntity", "Mention", "Chunk", "Project")
        )

    return _finish(stats, t0)


def _finish(stats: LoadStats, t0: float) -> LoadStats:
    stats.seconds = time.perf_counter() - t0
    logger.info("KG 9단계 적재 완료: %s", stats.as_dict())
    return stats


def _load_project_relations(
    conn: Any, graph: KgGraphStore, bid: str, batch: int, stop: Callable[[], bool]
) -> int:
    """(Project)-[:REL]->(Entity). 문서별 행을 과제 단위로 접어 근거 문서 목록을 모은다."""
    total = 0
    for rel_type in sorted(set(ENTITY_TYPE_TO_RELATION.values())):
        rows = conn.execute(
            "SELECT source_canonical_id AS project_id, target_canonical_id AS canonical_id,"
            " MAX(statement_status) AS derived_status, MAX(status_source) AS status_source,"
            " MAX(confidence) AS confidence, SUM(mention_count) AS mention_count,"
            " GROUP_CONCAT(DISTINCT doc_id) AS docs"
            " FROM relation_candidates WHERE source_kind=? AND relation_type=?"
            " GROUP BY source_canonical_id, target_canonical_id",
            (SOURCE_KIND_PROJECT, rel_type),
        ).fetchall()
        for group in _batched(
            (
                {
                    "project_id": r["project_id"],
                    "canonical_id": r["canonical_id"],
                    "derived_status": r["derived_status"],
                    "status_source": r["status_source"],
                    "confidence": r["confidence"],
                    "mention_count": r["mention_count"],
                    "source_document_ids": (r["docs"] or "").split(",")[:50],
                    "build_id": bid,
                }
                for r in rows
            ),
            batch,
        ):
            if stop():
                return total
            graph.upsert_project_relations(rel_type, group)
            total += len(group)
    return total


def _load_applied_to(
    conn: Any, graph: KgGraphStore, bid: str, batch: int, stop: Callable[[], bool]
) -> int:
    rows = conn.execute(
        "SELECT source_canonical_id AS source_id, target_canonical_id AS target_id,"
        " SUM(mention_count) AS mention_count FROM relation_candidates"
        " WHERE relation_type=? GROUP BY source_canonical_id, target_canonical_id",
        (RELATION_APPLIED_TO,),
    ).fetchall()
    total = 0
    for group in _batched(
        (
            {
                "source_id": r["source_id"],
                "target_id": r["target_id"],
                "mention_count": r["mention_count"],
                "build_id": bid,
            }
            for r in rows
        ),
        batch,
    ):
        if stop():
            return total
        graph.upsert_applied_to(group)
        total += len(group)
    return total


def _load_shares_entity(
    conn: Any, graph: KgGraphStore, bid: str, batch: int, stop: Callable[[], bool]
) -> int:
    rows = conn.execute(
        "SELECT source_canonical_id AS source_id, target_canonical_id AS target_id,"
        " confidence AS weight, mention_count AS shared_count FROM relation_candidates"
        " WHERE source_kind=? AND relation_type=? ORDER BY confidence DESC",
        (SOURCE_KIND_DOCUMENT, RELATION_SHARES_ENTITY),
    ).fetchall()
    total = 0
    for group in _batched(
        (
            {
                "source_id": r["source_id"],
                "target_id": r["target_id"],
                "weight": r["weight"],
                "shared_count": r["shared_count"],
                "build_id": bid,
            }
            for r in rows
        ),
        batch,
    ):
        if stop():
            return total
        graph.upsert_shares_entity(group)
        total += len(group)
    return total


def load_summary(store: CandidateStore) -> dict[str, Any]:
    """적재 없이 무엇이 올라갈지 미리 센다 (`--dry-run`)."""
    conn = store._conn  # noqa: SLF001

    def q(sql: str, *a: Any) -> int:
        return int(conn.execute(sql, a).fetchone()[0])

    return {
        "projects": len({p.project_id for p in resolve_projects(store, persist=False)}),
        "documents": q("SELECT COUNT(*) FROM documents"),
        "canonical_entities": q("SELECT COUNT(*) FROM canonical_entities"),
        "mentions": q(
            "SELECT COUNT(*) FROM entity_candidates WHERE canonical_id IS NOT NULL"
            " AND state != 'REJECTED'"
        ),
        "chunks": q(
            "SELECT COUNT(DISTINCT chunk_id) FROM entity_candidates"
            " WHERE canonical_id IS NOT NULL AND state != 'REJECTED'"
        ),
        "project_relations": q(
            "SELECT COUNT(*) FROM relation_candidates WHERE source_kind=?", SOURCE_KIND_PROJECT
        ),
        "applied_to": q(
            "SELECT COUNT(*) FROM relation_candidates WHERE relation_type=?", RELATION_APPLIED_TO
        ),
        "shares_entity": q(
            "SELECT COUNT(*) FROM relation_candidates WHERE relation_type=?",
            RELATION_SHARES_ENTITY,
        ),
    }


__all__ = ["LoadStats", "load_graph", "load_summary"]

# src/graph_rag/service.py
"""M_19 GraphRagService — 인덱싱 큐 + 그래프 검색 + 하이브리드 융합 (스펙 §3.4).

질의 경로에는 LLM 호출이 없다(엔티티 이름 매칭만) — 지연 목표 <300ms.
그래프 저장소가 죽어 있으면 벡터-only로 자동 폴백한다 (기능 저하, 오류 아님).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any

from vector_search.rag import _rrf_fuse
from vector_search.types import RetrievalResult, SearchHit

from .extractor import EntityExtractor
from .store import GraphStore
from .types import ChunkLink, EvidenceSubgraph, GraphSnapshot, IndexStatus

logger = logging.getLogger(__name__)

# 질의에서 엔티티 후보 용어 추출 (2자 이상 한글/영숫자 연속)
_TERM_RE = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣._-]{1,}")
# 그래프 유래 hit의 score 범위 (연결 엔티티 수 정규화)
_GRAPH_SCORE_MIN = 0.40
_GRAPH_SCORE_MAX = 0.90
_PING_CACHE_SECONDS = 60.0
_KNOWLEDGE_CATEGORY = "__knowledge__"


class GraphRagService:
    """그래프 인덱싱·검색 오케스트레이터."""

    def __init__(
        self,
        graph_store: GraphStore,
        vector_store: Any,  # vector_search.VectorStore (순환 의존 회피용 Any)
        extractor: EntityExtractor,
        rag_service: Any,  # vector_search.RagService
        max_hops: int = 2,
        evidence_buffer: int = 5,
    ) -> None:
        self._graph = graph_store
        self._vstore = vector_store
        self._extractor = extractor
        self._rag = rag_service
        self._max_hops = max_hops

        self._statuses: dict[str, IndexStatus] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        # 생성 시점의 이벤트 루프 캡처 — executor 스레드에서의 스케줄을 threadsafe로 전달
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._evidence: list[EvidenceSubgraph] = []
        self._evidence_buffer = evidence_buffer
        self._ping_cache: tuple[float, bool] = (0.0, False)
        self._fallback_warned_at = 0.0

    # ── 가용성 ────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """그래프 저장소 연결 여부 (60초 캐시, sync — 캐시 미스 시 짧은 blocking ping)."""
        now = time.monotonic()
        ts, ok = self._ping_cache
        if now - ts < _PING_CACHE_SECONDS:
            return ok
        ok = self._safe_ping()
        self._ping_cache = (now, ok)
        return ok

    def _safe_ping(self) -> bool:
        try:
            return bool(self._graph.ping())
        except Exception:
            return False

    def _warn_fallback(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._fallback_warned_at > 60.0:
            logger.warning("GraphRAG 폴백 (벡터-only): %s", reason)
            self._fallback_warned_at = now

    # ── 인덱싱 ────────────────────────────────────────────────────────────────

    def schedule_index_document(self, doc_id: str) -> None:
        """문서 그래프 인덱싱을 백그라운드 큐에 등록 (중복 등록은 무시).

        이벤트 루프 밖(executor 스레드)에서도 안전하게 호출 가능.
        """
        status = self._statuses.get(doc_id)
        if status is not None and status.state in ("pending", "running"):
            logger.debug("GraphRAG 인덱싱 중복 스케줄 무시: %s", doc_id)
            return
        self._statuses[doc_id] = IndexStatus(doc_id=doc_id, state="pending")

        def _enqueue() -> None:
            self._queue.put_nowait(doc_id)
            self._ensure_worker()

        try:
            asyncio.get_running_loop()
            _enqueue()  # 루프 안에서 호출됨
        except RuntimeError:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(_enqueue)
            else:
                logger.warning("GraphRAG 스케줄 실패: 이벤트 루프 없음 (doc_id=%s)", doc_id)
                self._statuses.pop(doc_id, None)
                return
        logger.info("GraphRAG 인덱싱 스케줄: %s", doc_id)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop(), name="graphrag-indexer")

    async def _worker_loop(self) -> None:
        while True:
            try:
                doc_id = await asyncio.wait_for(self._queue.get(), timeout=300.0)
            except asyncio.TimeoutError:
                return  # 5분간 작업 없으면 워커 종료 (다음 스케줄에서 재생성)
            try:
                await self.index_document(doc_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("GraphRAG 인덱싱 실패 (doc_id=%s): %s", doc_id, exc)
                st = self._statuses.get(doc_id)
                if st is not None:
                    st.state = "failed"
                    st.error = str(exc)[:200]

    async def index_document(self, doc_id: str) -> IndexStatus:
        """문서(또는 노트 doc_id)의 청크를 순차 추출해 그래프에 축적."""
        status = self._statuses.setdefault(doc_id, IndexStatus(doc_id=doc_id))
        status.state = "running"
        status.error = ""

        if not self.available:
            status.state = "failed"
            status.error = "그래프 저장소 연결 불가"
            self._warn_fallback("인덱싱 시도 중 저장소 미연결")
            return status

        loop = asyncio.get_running_loop()
        rows: list[dict[str, Any]] = await loop.run_in_executor(
            None, lambda: self._vstore.get_chunks_by_doc_id(doc_id, limit=1000)
        )
        status.total_chunks = len(rows)
        if not rows:
            status.state = "done"
            return status

        # 부모 노드 (노트 vs 문서)
        first = rows[0]
        is_note = first.get("category") == _KNOWLEDGE_CATEGORY
        parent_kind = "note" if is_note else "document"
        parent_id = doc_id
        try:
            if is_note:
                slug = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                parent_id = slug
                await loop.run_in_executor(
                    None, lambda: self._graph.upsert_note(slug, str(first.get("doc_name") or slug))
                )
            else:
                await loop.run_in_executor(
                    None,
                    lambda: self._graph.upsert_document(
                        doc_id,
                        str(first.get("doc_name") or doc_id),
                        str(first.get("category") or ""),
                    ),
                )
        except Exception as exc:
            status.state = "failed"
            status.error = f"부모 노드 upsert 실패: {exc}"
            return status

        for row in rows:
            chunk_id = str(row.get("chunk_id") or "")
            text = str(row.get("text") or "")
            result = await self._extractor.extract(text)
            if not result.entities:
                status.skipped_chunks += 1
                status.done_chunks += 1
                continue
            links = [ChunkLink(entity_id=e.id, chunk_id=chunk_id) for e in result.entities]
            try:
                await loop.run_in_executor(
                    None, lambda: self._graph.upsert_entities(result.entities)
                )
                await loop.run_in_executor(
                    None, lambda: self._graph.upsert_relations(result.relations)
                )
                await loop.run_in_executor(
                    None, lambda: self._graph.link_chunks(links, parent_id, parent_kind)
                )
            except Exception as exc:
                logger.warning("GraphRAG 청크 upsert 실패 (skip): %s", exc)
                status.skipped_chunks += 1
            status.done_chunks += 1

        status.state = "done"
        logger.info(
            "GraphRAG 인덱싱 완료: doc_id=%s, chunks=%d (추출실패 스킵=%d)",
            doc_id,
            status.done_chunks,
            status.skipped_chunks,
        )
        return status

    async def reindex_all(self) -> int:
        """LanceDB의 모든 doc_id를 백필 스케줄. 반환: 스케줄된 문서 수."""
        loop = asyncio.get_running_loop()
        doc_ids: list[str] = await loop.run_in_executor(None, self._all_doc_ids)
        for doc_id in doc_ids:
            self.schedule_index_document(doc_id)
        return len(doc_ids)

    def _all_doc_ids(self) -> list[str]:
        try:
            tbl = getattr(self._vstore, "_tbl", None)
            if tbl is None:
                return []
            rows = tbl.search().select(["doc_id"]).limit(100_000).to_list()
            return sorted({str(r["doc_id"]) for r in rows if r.get("doc_id")})
        except Exception as exc:
            logger.warning("reindex_all doc_id 수집 실패: %s", exc)
            return []

    async def index_note(self, slug: str, title: str) -> None:
        """노트 저장/수정 후 호출 — 노트 청크 재인덱싱."""
        doc_id = f"{_KNOWLEDGE_CATEGORY}:{slug}"
        self.schedule_index_document(doc_id)

    def index_statuses(self) -> list[dict[str, Any]]:
        return [st.as_dict() for st in self._statuses.values()]

    # ── CR-22 엔티티 정규화 ───────────────────────────────────────────────────

    async def normalize_entities(self) -> dict[str, Any]:
        """같은 대상의 표기 변형 엔티티를 LLM 제안으로 병합 (타입별, 보수적).

        Returns: {"groups": [[대표, 변형...], ...], "merged": 병합된 엔티티 수}
        """
        if not self.available:
            return {"groups": [], "merged": 0, "error": "그래프 저장소 연결 불가"}

        loop = asyncio.get_running_loop()
        entities = await loop.run_in_executor(None, lambda: self._graph.all_entities(2000))
        by_type: dict[str, list[Any]] = {}
        for e in entities:
            by_type.setdefault(e.type, []).append(e)

        merged_groups: list[list[str]] = []
        merged_count = 0
        for type_, ents in by_type.items():
            if len(ents) < 2:
                continue
            groups = await self._extractor.propose_merges([e.name for e in ents])
            name_to_ent = {e.name: e for e in ents}
            for g in groups:
                target = name_to_ent.get(g[0])
                source_ids = [name_to_ent[n].id for n in g[1:] if n in name_to_ent]
                if target is None or not source_ids:
                    continue
                try:
                    n = await loop.run_in_executor(
                        None, lambda t=target, s=source_ids: self._graph.merge_entities(t.id, s)
                    )
                except Exception as exc:
                    logger.warning("정규화 병합 실패 (그룹 스킵, type=%s): %s", type_, exc)
                    continue
                if n > 0:
                    merged_count += n
                    merged_groups.append(g)

        logger.info(
            "GraphRAG 정규화 완료: 그룹 %d개, 엔티티 %d개 병합", len(merged_groups), merged_count
        )
        return {"groups": merged_groups, "merged": merged_count}

    def delete_document(self, doc_id: str) -> None:
        """문서 삭제 연쇄 (sync — 라우트에서 executor로 호출)."""
        try:
            self._graph.delete_by_doc_id(doc_id)
            self._statuses.pop(doc_id, None)
        except Exception as exc:
            logger.warning("GraphRAG delete_document 실패 (무시): %s", exc)

    # ── 검색 ─────────────────────────────────────────────────────────────────

    async def graph_retrieve(
        self, query: str, top_k: int = 5
    ) -> tuple[list[SearchHit], EvidenceSubgraph | None]:
        """그래프 탐색 검색: 질의 용어 → 엔티티 매칭 → 이웃 확장 → 연결 청크."""
        terms = _TERM_RE.findall(query or "")
        if not terms:
            return [], None

        loop = asyncio.get_running_loop()
        try:
            matched = await loop.run_in_executor(
                None, lambda: self._graph.find_entities(terms, limit=10)
            )
            if not matched:
                return [], None
            matched_ids = [e.id for e in matched]
            neighbor_entities = await loop.run_in_executor(
                None, lambda: self._graph.neighbors(matched_ids, hops=self._max_hops, limit=40)
            )
            all_ids = matched_ids + [e.id for e in neighbor_entities]
            chunk_counts = await loop.run_in_executor(
                None, lambda: self._graph.chunks_for_entities(all_ids, limit=top_k * 4)
            )
        except Exception as exc:
            self._warn_fallback(f"그래프 질의 실패: {exc}")
            return [], None

        if not chunk_counts:
            return [], None

        chunk_ids = [cid for cid, _ in chunk_counts[: top_k * 2]]
        rows = await loop.run_in_executor(
            None, lambda: self._vstore.get_chunks_by_chunk_ids(chunk_ids)
        )
        by_id = {str(r.get("chunk_id")): r for r in rows}
        max_n = max(n for _, n in chunk_counts) or 1

        hits: list[SearchHit] = []
        for cid, n in chunk_counts:
            row = by_id.get(cid)
            if row is None:  # 고아 링크 (LanceDB에서 삭제된 청크) — 무시
                continue
            score = _GRAPH_SCORE_MIN + (_GRAPH_SCORE_MAX - _GRAPH_SCORE_MIN) * (n / max_n)
            hits.append(_row_to_hit(row, score))
            if len(hits) >= top_k:
                break

        evidence: EvidenceSubgraph | None = None
        try:
            snap: GraphSnapshot = await loop.run_in_executor(
                None,
                lambda: self._graph.subgraph(all_ids, [h.chunk_id for h in hits]),
            )
            evidence = EvidenceSubgraph(
                query=query,
                created=datetime.now().isoformat(timespec="seconds"),
                nodes=snap.nodes,
                edges=snap.edges,
                chunk_ids=[h.chunk_id for h in hits],
            )
        except Exception as exc:
            logger.debug("evidence 서브그래프 조립 실패 (무시): %s", exc)

        logger.info(
            "GraphRAG 검색: terms=%d, 매칭 엔티티=%d, 이웃=%d, 청크 hits=%d (query=%r)",
            len(terms),
            len(matched),
            len(neighbor_entities),
            len(hits),
            (query or "")[:50],
        )
        return hits, evidence

    async def hybrid_retrieve(
        self,
        query: str,
        top_k: int = 5,
        source: str = "both",
    ) -> RetrievalResult:
        """벡터 RAG + 그래프 검색 RRF 융합 (스펙 §3.4).

        found 판정은 벡터 결과 기준을 유지한다 (기존 계약 불변).
        그래프 저장소 미가용 시 벡터 결과를 그대로 반환한다.
        """
        loop = asyncio.get_running_loop()
        vector_result: RetrievalResult = await loop.run_in_executor(
            None, lambda: self._rag.retrieve(query, top_k, source=source)
        )

        if not self.available:
            self._warn_fallback("hybrid_retrieve 시 저장소 미연결")
            return vector_result

        graph_hits, evidence = await self.graph_retrieve(query, top_k=top_k)
        if evidence is not None:
            self._evidence.append(evidence)
            del self._evidence[: -self._evidence_buffer]

        if not graph_hits:
            return vector_result

        fused = _rrf_fuse(vector_result.hits, graph_hits)[:top_k]
        logger.info(
            "GraphRAG 하이브리드 융합: 벡터=%d + 그래프=%d → %d (query=%r)",
            len(vector_result.hits),
            len(graph_hits),
            len(fused),
            (query or "")[:50],
        )
        return RetrievalResult(
            hits=fused,
            found=vector_result.found or bool(graph_hits),
            no_match_reason=None
            if (vector_result.found or graph_hits)
            else vector_result.no_match_reason,
        )

    def latest_evidence(self) -> EvidenceSubgraph | None:
        return self._evidence[-1] if self._evidence else None

    # ── 시각화/운영 ───────────────────────────────────────────────────────────

    async def snapshot(
        self, limit: int = 500, entity_types: list[str] | None = None
    ) -> GraphSnapshot:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._graph.snapshot(limit=limit, entity_types=entity_types)
        )

    async def stats(self) -> dict[str, int]:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._graph.stats)
        except Exception:
            return {}

    def close(self) -> None:
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
        try:
            self._graph.close()
        except Exception:
            pass


def _row_to_hit(row: dict[str, Any], score: float) -> SearchHit:
    """LanceDB row dict → SearchHit (그래프 유래 점수 부여)."""
    bbox_raw = row.get("bbox")
    bbox = tuple(float(v) for v in bbox_raw) if bbox_raw else None
    return SearchHit(
        doc_id=str(row.get("doc_id") or ""),
        doc_name=str(row.get("doc_name") or ""),
        category=row.get("category"),
        page=row.get("page"),
        section=row.get("section"),
        chunk_id=str(row.get("chunk_id") or ""),
        text=str(row.get("text") or ""),
        bbox=bbox,  # type: ignore[arg-type]
        source_path=str(row.get("source_path") or ""),
        score=score,
    )

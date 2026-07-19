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
from .types import (
    EvidenceSubgraph,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    IndexStatus,
    KeywordMention,
    ProjectInfo,
)

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
        # CR-30 graceful 중단 플래그 — 신규 투입만 멈추고 현재 문서는 완료
        self._cancel_requested = False

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
        self._cancel_requested = False  # 새 스케줄 = 중단 해제 (CR-30)
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
            # CR-30 graceful 중단: 신규 투입 중지 — 현재 문서는 끝까지, 다음부터 스킵
            if self._cancel_requested:
                st = self._statuses.get(doc_id)
                if st is not None and st.state == "pending":
                    st.state = "cancelled"
                    st.error = "사용자 중단"
                continue
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

    # 문서 단위 추출 입력 상한 — 과제 정보·키워드는 문서 앞부분에 밀집하므로
    # 앞에서부터 자른다 (전체 임베딩과 무관, 추출 전용)
    _DOC_EXTRACT_MAX_CHARS = 9_000

    async def index_document(self, doc_id: str) -> IndexStatus:
        """CR-30: 문서 단위 추출 — 과제 정보(title/rfp_no/project_no) + 역할 키워드(≤10).

        (구) 청크별 엔티티 추출은 폐기. 문서당 LLM 1회 호출.
        노트는 문서와 동일하게 처리하되 Note 노드를 부모로 유지한다.
        """
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

        first = rows[0]
        is_note = first.get("category") == _KNOWLEDGE_CATEGORY
        doc_name = str(first.get("doc_name") or doc_id)

        # 문서 전문 조립 (추출 입력 상한까지)
        parts: list[str] = []
        total = 0
        for row in rows:
            t = str(row.get("text") or "")
            if total + len(t) > self._DOC_EXTRACT_MAX_CHARS:
                parts.append(t[: self._DOC_EXTRACT_MAX_CHARS - total])
                break
            parts.append(t)
            total += len(t)
        doc_text = "\n".join(parts)

        try:
            if is_note:
                slug = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                await loop.run_in_executor(
                    None, lambda: self._graph.upsert_note(slug, doc_name)
                )
            extraction = await self._extractor.extract_project(doc_id, doc_text)
            project = extraction.project
            if not project.title:
                project = ProjectInfo(
                    doc_id=doc_id,
                    title=doc_name,
                    rfp_no=project.rfp_no,
                    project_no=project.project_no,
                )
            # 문서 단위 단일 트랜잭션 저장
            await loop.run_in_executor(
                None, lambda: self._graph.upsert_project_bundle(project, extraction.keywords)
            )
        except asyncio.CancelledError:
            status.state = "cancelled"
            status.error = "사용자 중단"
            raise
        except Exception as exc:
            status.state = "failed"
            status.error = str(exc)[:200]
            return status

        status.done_chunks = status.total_chunks
        status.state = "done"
        logger.info(
            "GraphRAG 인덱싱 완료: doc_id=%s, 키워드=%d, 과제번호=%r",
            doc_id,
            len(extraction.keywords),
            extraction.project.project_no or extraction.project.rfp_no or "",
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

    # ── CR-26: 인덱싱 중단 · 그래프 초기화 ───────────────────────────────────

    def cancel_indexing(self) -> int:
        """CR-30 graceful 중단: 대기 큐를 비우고 신규 투입을 멈춘다.

        진행 중인 문서 1건은 끝까지 완료된다 (요청 도중 하드 취소로 인한
        반쪽 트랜잭션 방지). 반환: 중단(취소 표시)된 문서 수.
        """
        self._cancel_requested = True
        cancelled = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        for st in self._statuses.values():
            if st.state == "pending":
                st.state = "cancelled"
                st.error = "사용자 중단"
                cancelled += 1
        logger.info("GraphRAG 인덱싱 중단 요청: 대기 %d건 취소 (진행 중 문서는 완료 후 정지)", cancelled)
        return cancelled

    async def clear_graph(self) -> dict[str, int]:
        """CR-26: 그래프 전체 초기화 — 인덱싱 중단 후 모든 노드 삭제.

        Returns: 삭제 전 stats.
        """
        self.cancel_indexing()
        loop = asyncio.get_running_loop()
        before = await loop.run_in_executor(None, self._graph.clear_all)
        self._statuses.clear()
        self._evidence.clear()
        logger.info("GraphRAG 초기화 완료 (삭제 전 stats: %s)", before)
        return before

    # ── CR-22 엔티티 정규화 ───────────────────────────────────────────────────

    async def normalize_entities(self) -> dict[str, Any]:
        """CR-30 키워드 정규화 후처리 — 노드 병합 없이 속성만 갱신.

        같은 개념의 표기 변형(raw_term)을 역할별로 LLM이 묶으면, 해당 키워드
        노드들의 normalized_term/status를 갱신한다. raw_term과 문서별 언급
        노드는 보존된다 (전역 병합 금지 — 문맥별 의미 차이 보호).

        Returns: {"groups": [[대표, 변형...], ...], "merged": 갱신된 키워드 언급 수}
        """
        if not self.available:
            return {"groups": [], "merged": 0, "error": "그래프 저장소 연결 불가"}

        loop = asyncio.get_running_loop()
        keywords: list[KeywordMention] = await loop.run_in_executor(
            None, lambda: self._graph.all_keywords(5000)
        )
        by_role: dict[str, list[KeywordMention]] = {}
        for k in keywords:
            by_role.setdefault(k.role, []).append(k)

        norm_groups: list[list[str]] = []
        updated_count = 0
        for role, kws in by_role.items():
            distinct_terms = sorted({k.raw_term for k in kws})
            if len(distinct_terms) < 2:
                continue
            groups = await self._extractor.propose_merges(distinct_terms)
            for g in groups:
                canonical = g[0]
                # 그룹에 속한 raw_term의 모든 문서별 언급 id 수집 (노드는 그대로)
                ids = [k.id for k in kws if k.raw_term in set(g)]
                if not ids:
                    continue
                try:
                    n = await loop.run_in_executor(
                        None,
                        lambda i=ids, c=canonical: self._graph.update_keyword_normalization(i, c),
                    )
                except Exception as exc:
                    logger.warning("키워드 정규화 실패 (그룹 스킵, role=%s): %s", role, exc)
                    continue
                if n > 0:
                    updated_count += n
                    norm_groups.append(g)

        logger.info(
            "GraphRAG 키워드 정규화 완료: 그룹 %d개, 언급 %d건 갱신 (노드 병합 없음)",
            len(norm_groups),
            updated_count,
        )
        return {"groups": norm_groups, "merged": updated_count}

    # ── CR-30: 시험 인덱싱 모드 ──────────────────────────────────────────────

    async def test_index(self, limit: int = 10) -> dict[str, Any]:
        """문서 N건만 인덱싱해 추출 결과·노드 수를 즉시 반환 (지침 튜닝용)."""
        if not self.available:
            return {"error": "그래프 저장소 연결 불가", "results": [], "stats": {}}

        loop = asyncio.get_running_loop()
        doc_ids: list[str] = await loop.run_in_executor(None, self._all_doc_ids)
        picked = [d for d in doc_ids if not d.startswith(_KNOWLEDGE_CATEGORY)][: max(1, limit)]

        results: list[dict[str, Any]] = []
        for doc_id in picked:
            status = await self.index_document(doc_id)
            kws: list[KeywordMention] = []
            if status.state == "done":
                kws = await loop.run_in_executor(
                    None, lambda d=doc_id: self._graph.keywords_for_doc(d)
                )
            results.append(
                {
                    "doc_id": doc_id,
                    "state": status.state,
                    "error": status.error,
                    "keywords": [
                        {
                            "raw_term": k.raw_term,
                            "role": k.role,
                            "confidence": k.confidence,
                        }
                        for k in kws
                    ],
                }
            )

        stats = await self.stats()
        logger.info("GraphRAG 시험 인덱싱: %d건 완료", len(results))
        return {"results": results, "stats": stats}

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
        """CR-30 그래프 탐색: 질의 용어 → 키워드 매칭 → 소속 과제(문서) → 대표 청크.

        문서는 매칭된 키워드 수·confidence로 랭킹한다.
        """
        terms = _TERM_RE.findall(query or "")
        if not terms:
            return [], None

        loop = asyncio.get_running_loop()
        try:
            matched: list[KeywordMention] = await loop.run_in_executor(
                None, lambda: self._graph.find_keywords(terms, limit=40)
            )
        except Exception as exc:
            self._warn_fallback(f"그래프 질의 실패: {exc}")
            return [], None

        if not matched:
            return [], None

        # 문서별 매칭 점수 (키워드 수 + confidence 합)
        doc_scores: dict[str, float] = {}
        doc_keywords: dict[str, list[KeywordMention]] = {}
        for k in matched:
            doc_scores[k.doc_id] = doc_scores.get(k.doc_id, 0.0) + 1.0 + k.confidence
            doc_keywords.setdefault(k.doc_id, []).append(k)
        ranked_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)[: top_k * 2]
        max_score = ranked_docs[0][1] if ranked_docs else 1.0

        hits: list[SearchHit] = []
        for doc_id, score in ranked_docs:
            rows = await loop.run_in_executor(
                None, lambda d=doc_id: self._vstore.get_chunks_by_doc_id(d, limit=2)
            )
            norm = _GRAPH_SCORE_MIN + (_GRAPH_SCORE_MAX - _GRAPH_SCORE_MIN) * (
                score / max_score
            )
            for row in rows:
                hits.append(_row_to_hit(row, norm))
                if len(hits) >= top_k:
                    break
            if len(hits) >= top_k:
                break

        # evidence: 매칭 키워드 + 소속 문서 노드
        evidence: EvidenceSubgraph | None = None
        try:
            nodes: dict[str, GraphNode] = {}
            edges: list[GraphEdge] = []
            for doc_id, _s in ranked_docs:
                if doc_id not in nodes:
                    nodes[doc_id] = GraphNode(id=doc_id, label=doc_id, kind="document")
                for k in doc_keywords.get(doc_id, []):
                    if k.id not in nodes:
                        nodes[k.id] = GraphNode(
                            id=k.id,
                            label=k.normalized_term or k.raw_term,
                            kind="keyword",
                            type=k.role,
                        )
                    edges.append(GraphEdge(source=doc_id, target=k.id, kind="has_keyword"))
            evidence = EvidenceSubgraph(
                query=query,
                created=datetime.now().isoformat(timespec="seconds"),
                nodes=list(nodes.values()),
                edges=edges,
                chunk_ids=[h.chunk_id for h in hits],
            )
        except Exception as exc:
            logger.debug("evidence 서브그래프 조립 실패 (무시): %s", exc)

        logger.info(
            "GraphRAG 검색: terms=%d, 매칭 키워드=%d, 문서=%d, hits=%d (query=%r)",
            len(terms),
            len(matched),
            len(ranked_docs),
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

    async def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """CR-31: 제목·키워드로 과제(문서) 검색 — 결과는 문서만. 미가용 시 빈 목록."""
        if not self.available:
            return []
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: self._graph.search_documents(query, limit)
            )
        except Exception as exc:
            logger.warning("search_documents 실패: %s", exc)
            return []

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

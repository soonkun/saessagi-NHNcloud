# src/app/graphrag_routes.py
"""M_19 GraphRAG REST API (스펙 §5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/graphrag", tags=["graphrag"])


class GraphNodeResp(BaseModel):
    id: str
    label: str
    kind: str
    type: str = ""


class GraphEdgeResp(BaseModel):
    source: str
    target: str
    kind: str
    weight: float = 1.0


class GraphResp(BaseModel):
    nodes: list[GraphNodeResp]
    edges: list[GraphEdgeResp]
    stats: dict[str, int] = {}


class StatusResp(BaseModel):
    enabled: bool
    connected: bool
    stats: dict[str, int] = {}
    indexing: list[dict[str, Any]] = []


class ReindexReq(BaseModel):
    doc_id: str | None = None
    only_missing: bool = False  # CR-35: 그래프에 없는 문서만 (증분)
    # CR-61: M_23 그래프가 있는데도 굳이 구버전 인덱싱을 돌리겠다는 명시적 의사표시.
    force_legacy: bool = False


class ReindexResp(BaseModel):
    scheduled: bool
    count: int = 0


class EvidenceResp(BaseModel):
    query: str
    created: str
    nodes: list[GraphNodeResp]
    edges: list[GraphEdgeResp]
    chunk_ids: list[str]


def _get_context(request: Request) -> Any:
    ctx = getattr(request.app.state, "service_context", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="service context unavailable")
    return ctx


def _get_service(request: Request) -> Any:
    svc = getattr(_get_context(request), "graph_rag_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503, detail="GraphRAG 비활성 (graphrag.enabled=false 또는 초기화 실패)"
        )
    return svc


def _get_kg_store(request: Request) -> Any:
    """M_23 정규 엔티티 그래프 스토어 (CR-61).

    적재가 끝나 있으면 이쪽이 그래프 탭의 데이터 출처다. 없거나 비어 있으면 `None`을
    돌려주고, 호출자는 M_19 키워드 경로로 폴백한다 — 구축 전에도 탭이 죽지 않아야 한다.
    """
    store = getattr(_get_context(request), "kg_graph_store", None)
    if store is None:
        return None
    try:
        if not store.ping():
            return None
        rows = store._run("MATCH (c:CanonicalEntity) RETURN count(c) AS c LIMIT 1")  # noqa: SLF001
        return store if (rows and rows[0]["c"] > 0) else None
    except Exception as exc:
        logger.warning(f"M_23 그래프 스토어 확인 실패 (키워드 경로 폴백): {exc}")
        return None


def _dict_to_resp(snap: dict[str, Any], stats: dict[str, int]) -> GraphResp:
    """KgGraphStore가 돌려주는 dict를 기존 응답 형태로 옮긴다.

    M_19 스냅샷과 형태를 맞춰 두었으므로 프론트는 출처가 바뀐 줄 모른다.
    """
    return GraphResp(
        nodes=[
            GraphNodeResp(
                id=n["id"], label=n["label"], kind=n["kind"], type=str(n.get("type") or "")
            )
            for n in snap.get("nodes", [])
        ],
        edges=[
            GraphEdgeResp(
                source=e["source"],
                target=e["target"],
                kind=e["kind"],
                weight=float(e.get("weight") or 1.0),
            )
            for e in snap.get("edges", [])
        ],
        stats=stats,
    )


def _snapshot_to_resp(snap: Any, stats: dict[str, int]) -> GraphResp:
    return GraphResp(
        nodes=[GraphNodeResp(id=n.id, label=n.label, kind=n.kind, type=n.type) for n in snap.nodes],
        edges=[
            GraphEdgeResp(source=e.source, target=e.target, kind=e.kind, weight=e.weight)
            for e in snap.edges
        ],
        stats=stats,
    )


@router.get("/graph", response_model=GraphResp)
async def get_graph(
    request: Request, limit: int = 500, types: str = "", min_df: int = 2
) -> GraphResp:
    entity_types = [t.strip() for t in types.split(",") if t.strip()] or None

    # CR-61: M_23 정규 엔티티 그래프가 적재돼 있으면 그쪽을 보여준다.
    # min_df 기본 2 — 엔티티 207,674개를 다 그리면 아무것도 안 보인다(스펙 §5.2).
    kg = _get_kg_store(request)
    if kg is not None:
        try:
            snap = await run_in_threadpool(
                kg.snapshot, limit=limit, entity_types=entity_types, min_df=min_df
            )
            stats = await run_in_threadpool(kg.graph_stats)
            return _dict_to_resp(snap, stats)
        except Exception as exc:
            logger.error(f"M_23 /graph 실패 (키워드 경로 폴백): {exc}")

    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    try:
        snap = await svc.snapshot(limit=limit, entity_types=entity_types)
        stats = await svc.stats()
    except Exception as exc:
        logger.error(f"graphrag /graph 실패: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return _snapshot_to_resp(snap, stats)


@router.get("/doc-focus", response_model=GraphResp)
async def get_doc_focus(request: Request, doc_id: str, limit: int = 40) -> GraphResp:
    """CR-37: 한 문서 중심 포커스 서브그래프 — 검색→선택 시 그 과제와 연결만 로드."""
    kg = _get_kg_store(request)
    if kg is not None:
        try:
            snap_d = await run_in_threadpool(kg.doc_focus_snapshot, doc_id, limit=max(limit, 60))
            stats = await run_in_threadpool(kg.graph_stats)
            return _dict_to_resp(snap_d, stats)
        except Exception as exc:
            logger.error(f"M_23 /doc-focus 실패 (키워드 경로 폴백): {exc}")

    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    try:
        snap = await svc.doc_focus(doc_id, limit=limit)
        stats = await svc.stats()
    except Exception as exc:
        logger.error(f"graphrag /doc-focus 실패: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return _snapshot_to_resp(snap, stats)


@router.get("/status", response_model=StatusResp)
async def get_status(request: Request) -> StatusResp:
    ctx = _get_context(request)
    svc = getattr(ctx, "graph_rag_service", None)
    if svc is None:
        return StatusResp(enabled=False, connected=False)
    connected = bool(svc.available)
    stats: dict[str, int] = {}
    if connected:
        stats = await svc.stats()
    return StatusResp(
        enabled=True,
        connected=connected,
        stats=stats,
        indexing=svc.index_statuses(),
    )


def _refuse_legacy_if_m23(request: Request, force: bool, what: str) -> None:
    """M_23 그래프가 살아 있으면 구버전(M_19) 작업을 거부한다 (CR-61).

    UI에서 버튼을 없앴지만 엔드포인트는 되돌릴 여지를 위해 남겨 뒀는데, **그것만으로는
    부족했다.** 실제로 작업 중에 이 엔드포인트를 한 번 호출했다가 폐기한 Keyword 노드
    163개가 되살아났다(E-92). UI를 지우는 것과 실행 경로를 막는 것은 다른 일이다.

    되돌릴 필요가 생기면 `force_legacy: true`로 명시하면 된다.
    """
    if force:
        logger.warning(f"구버전 {what} 강제 실행 (force_legacy) — Keyword 그래프가 생성됩니다")
        return
    if _get_kg_store(request) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"M_23 정규 엔티티 그래프가 이미 적재돼 있어 구버전 {what}을 거부합니다. "
                "실행하면 폐기한 Keyword 노드가 되살아납니다. "
                "정말 필요하면 force_legacy=true 로 호출하세요."
            ),
        )


@router.post("/reindex", response_model=ReindexResp)
async def reindex(request: Request, body: ReindexReq) -> ReindexResp:
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    _refuse_legacy_if_m23(request, body.force_legacy, "인덱싱")
    if body.doc_id:
        svc.schedule_index_document(body.doc_id)
        return ReindexResp(scheduled=True, count=1)
    count = await (svc.reindex_missing() if body.only_missing else svc.reindex_all())
    return ReindexResp(scheduled=True, count=count)


# CR-26: 그래프 초기화 확인 문구 — GitHub 저장소 삭제처럼 정확히 입력해야 실행
_CLEAR_CONFIRM_PHRASE = "그래프를 초기화 합니다"


class CancelResp(BaseModel):
    cancelled: int


@router.post("/cancel", response_model=CancelResp)
async def cancel_indexing(request: Request) -> CancelResp:
    """CR-26: 진행·대기 중인 그래프 인덱싱 중단."""
    svc = _get_service(request)
    return CancelResp(cancelled=svc.cancel_indexing())


class ClearReq(BaseModel):
    confirm: str


class ClearResp(BaseModel):
    ok: bool
    before: dict[str, int]


@router.post("/clear", response_model=ClearResp)
async def clear_graph(request: Request, body: ClearReq) -> ClearResp:
    """CR-26: 그래프 전체 초기화 — confirm 문구가 정확히 일치해야 실행."""
    if body.confirm.strip() != _CLEAR_CONFIRM_PHRASE:
        raise HTTPException(
            status_code=422,
            detail=f"확인 문구가 일치하지 않습니다. 정확히 '{_CLEAR_CONFIRM_PHRASE}'를 입력하세요.",
        )
    # CR-61: M_23이 적재돼 있으면 M_23을 아는 초기화를 쓴다.
    # M_19의 clear_all은 Document·Chunk를 지우는데 그 둘은 M_23도 쓰므로, 그대로 두면
    # Mention 216,509개가 고아가 된다 — 노드는 남고 연결만 끊긴 최악의 상태.
    kg = _get_kg_store(request)
    if kg is not None:
        before = await run_in_threadpool(kg.clear_all)
        logger.warning(
            f"그래프 초기화 실행 (Neo4j 전용): {before} — "
            "추출 후보(entity_candidates)는 보존됨. kg_build.py load 로 재적재 가능."
        )
        return ClearResp(ok=True, before=before)

    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    before = await svc.clear_graph()
    return ClearResp(ok=True, before=before)


class NormalizeResp(BaseModel):
    groups: list[list[str]]
    merged: int


class NormalizeReq(BaseModel):
    only_new: bool = True
    force_legacy: bool = False  # CR-61: M_23 적재 상태에서 구버전 정규화 강행  # CR-35: 아직 정규화 안 된 키워드만 (증분). False면 전체 재정규화


@router.post("/normalize", response_model=NormalizeResp)
async def normalize(request: Request, body: NormalizeReq | None = None) -> NormalizeResp:
    _refuse_legacy_if_m23(request, bool(body and body.force_legacy), "정규화")
    """CR-22/35: 키워드 정규화 — 표기 변형을 LLM 제안으로 묶어 normalized_term 갱신.

    기본은 증분(only_new=True) — 새로 인덱싱된 키워드만 기존 대표어에 붙인다.
    """
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    only_new = body.only_new if body is not None else True
    result = await svc.normalize_entities(only_new=only_new)
    if result.get("error"):
        raise HTTPException(status_code=503, detail=str(result["error"]))
    return NormalizeResp(groups=result["groups"], merged=result["merged"])


class DocSearchResp(BaseModel):
    docs: list[dict[str, Any]]


@router.get("/search-docs", response_model=DocSearchResp)
async def search_docs(request: Request, q: str, limit: int = 20) -> DocSearchResp:
    """CR-31: 제목·엔티티로 과제(문서) 검색 — 결과는 문서만."""
    kg = _get_kg_store(request)
    if kg is not None:
        try:
            docs = await run_in_threadpool(kg.search_documents, q, max(1, min(limit, 50)))
            return DocSearchResp(docs=docs)
        except Exception as exc:
            logger.error(f"M_23 /search-docs 실패 (키워드 경로 폴백): {exc}")

    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    docs = await svc.search_documents(q, limit=max(1, min(limit, 50)))
    return DocSearchResp(docs=docs)


@router.get("/evidence/latest", response_model=EvidenceResp)
async def latest_evidence(request: Request) -> EvidenceResp:
    svc = _get_service(request)
    ev = svc.latest_evidence()
    if ev is None:
        raise HTTPException(status_code=404, detail="근거 그래프가 아직 없습니다")
    return EvidenceResp(
        query=ev.query,
        created=ev.created,
        nodes=[GraphNodeResp(id=n.id, label=n.label, kind=n.kind, type=n.type) for n in ev.nodes],
        edges=[
            GraphEdgeResp(source=e.source, target=e.target, kind=e.kind, weight=e.weight)
            for e in ev.edges
        ],
        chunk_ids=ev.chunk_ids,
    )


# ── CR-30: 시험 인덱싱 모드 ──────────────────────────────────────────────────


class TestIndexReq(BaseModel):
    limit: int = 10


class TestIndexResp(BaseModel):
    results: list[dict[str, Any]]
    stats: dict[str, int]


@router.post("/test-index", response_model=TestIndexResp)
async def test_index(request: Request, body: TestIndexReq) -> TestIndexResp:
    """CR-30: 문서 N건(기본 10)만 인덱싱하고 추출 결과·노드 수를 즉시 반환 — 지침 튜닝용."""
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    result = await svc.test_index(limit=max(1, min(body.limit, 50)))
    if result.get("error"):
        raise HTTPException(status_code=503, detail=str(result["error"]))
    return TestIndexResp(results=result["results"], stats=result["stats"])

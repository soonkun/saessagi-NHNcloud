# src/app/graphrag_routes.py
"""M_19 GraphRAG REST API (스펙 §5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
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
async def get_graph(request: Request, limit: int = 500, types: str = "") -> GraphResp:
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    entity_types = [t.strip() for t in types.split(",") if t.strip()] or None
    try:
        snap = await svc.snapshot(limit=limit, entity_types=entity_types)
        stats = await svc.stats()
    except Exception as exc:
        logger.error(f"graphrag /graph 실패: {exc}")
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


@router.post("/reindex", response_model=ReindexResp)
async def reindex(request: Request, body: ReindexReq) -> ReindexResp:
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    if body.doc_id:
        svc.schedule_index_document(body.doc_id)
        return ReindexResp(scheduled=True, count=1)
    count = await svc.reindex_all()
    return ReindexResp(scheduled=True, count=count)


class NormalizeResp(BaseModel):
    groups: list[list[str]]
    merged: int


@router.post("/normalize", response_model=NormalizeResp)
async def normalize(request: Request) -> NormalizeResp:
    """CR-22: 엔티티 정규화 — 표기 변형(정식명/약칭 등)을 LLM 제안으로 병합."""
    svc = _get_service(request)
    if not svc.available:
        raise HTTPException(status_code=503, detail="그래프 저장소(Neo4j) 연결 불가")
    result = await svc.normalize_entities()
    if result.get("error"):
        raise HTTPException(status_code=503, detail=str(result["error"]))
    return NormalizeResp(groups=result["groups"], merged=result["merged"])


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

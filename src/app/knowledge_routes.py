"""M_15 — Knowledge Notes API."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from knowledge import KnowledgeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_ROOT = Path(os.environ.get("SAESSAGI_ROOT", "."))

# 단일 인스턴스 — service_context를 거치지 않고 동작 (rag 없이도 노트 CRUD 가능)
_service_singleton: KnowledgeService | None = None


def _get_service(request: Request) -> KnowledgeService:
    """request.app.state.service_context 의 rag_service를 사용한 KnowledgeService 반환."""
    global _service_singleton
    if _service_singleton is None:
        ctx = getattr(request.app.state, "service_context", None)
        rag = getattr(ctx, "rag_service", None) if ctx else None
        _service_singleton = KnowledgeService(root=_ROOT, rag_service=rag)
    return _service_singleton


# ── Pydantic ────────────────────────────────────────────────────────────────


class NoteMetaResp(BaseModel):
    slug: str
    title: str
    tags: list[str]
    related_docs: list[str]
    created: str
    updated: str


class NoteResp(NoteMetaResp):
    content: str


class CreateNoteRequest(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []
    related_docs: list[str] = []


class UpdateNoteRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    related_docs: list[str] | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str


class GraphResp(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[GraphEdge]


# ── 엔드포인트 ──────────────────────────────────────────────────────────────


@router.get("/notes", response_model=list[NoteMetaResp])
async def list_notes(request: Request) -> list[NoteMetaResp]:
    svc = _get_service(request)
    return [NoteMetaResp(**asdict(m)) for m in svc.list_notes()]


@router.get("/notes/{slug}", response_model=NoteResp)
async def get_note(request: Request, slug: str) -> NoteResp:
    svc = _get_service(request)
    note = svc.get_note(slug)
    if note is None:
        raise HTTPException(status_code=404, detail=f"note not found: {slug}")
    return NoteResp(**asdict(note))


@router.post("/notes", response_model=NoteResp, status_code=201)
async def create_note(request: Request, body: CreateNoteRequest) -> NoteResp:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title이 비어있습니다")
    svc = _get_service(request)
    note = svc.create_note(
        title=title,
        content=body.content,
        tags=body.tags,
        related_docs=body.related_docs,
    )
    return NoteResp(**asdict(note))


@router.patch("/notes/{slug}", response_model=NoteResp)
async def update_note(request: Request, slug: str, body: UpdateNoteRequest) -> NoteResp:
    svc = _get_service(request)
    note = svc.update_note(
        slug,
        title=body.title,
        content=body.content,
        tags=body.tags,
        related_docs=body.related_docs,
    )
    if note is None:
        raise HTTPException(status_code=404, detail=f"note not found: {slug}")
    return NoteResp(**asdict(note))


@router.delete("/notes/{slug}")
async def delete_note(request: Request, slug: str) -> dict[str, Any]:
    svc = _get_service(request)
    if not svc.delete_note(slug):
        raise HTTPException(status_code=404, detail=f"note not found: {slug}")
    return {"ok": True, "slug": slug}


@router.get("/graph", response_model=GraphResp)
async def get_graph(request: Request) -> GraphResp:
    svc = _get_service(request)
    g = svc.build_graph()
    return GraphResp(
        nodes=g.nodes,
        edges=[GraphEdge(source=e.source, target=e.target, kind=e.kind) for e in g.edges],
    )

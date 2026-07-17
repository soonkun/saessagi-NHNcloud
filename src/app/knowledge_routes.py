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


class RelatedDocInfo(BaseModel):
    id: str
    filename: str | None = None


class NoteResp(NoteMetaResp):
    content: str
    related_docs_info: list[RelatedDocInfo] = []


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


def _note_resp_with_info(svc: KnowledgeService, note: Any) -> NoteResp:
    """NoteResp에 related_docs_info(첨부 파일 filename) 추가."""
    info = svc.resolve_related_docs(list(note.related_docs))
    return NoteResp(
        **asdict(note),
        related_docs_info=[RelatedDocInfo(**i) for i in info],
    )


@router.get("/notes/{slug}", response_model=NoteResp)
async def get_note(request: Request, slug: str) -> NoteResp:
    svc = _get_service(request)
    note = svc.get_note(slug)
    if note is None:
        raise HTTPException(status_code=404, detail=f"note not found: {slug}")
    return _note_resp_with_info(svc, note)


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
    return _note_resp_with_info(svc, note)


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
    return _note_resp_with_info(svc, note)


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


# ── CR-23: 노트 AI 편집 — 프롬프트 지시로 노트 작성/부분 수정 ─────────────────

_AI_EDIT_WHOLE_SYSTEM = """당신은 사내 업무 노트를 편집하는 어시스턴트입니다.
사용자의 지시에 따라 아래 노트의 마크다운 본문을 수정하거나 새로 작성하세요.

규칙:
- 완성된 노트 마크다운 본문 전체만 출력한다 (코드펜스·설명·인사말 금지)
- 지시와 무관한 기존 내용은 그대로 보존한다
- [[doc:...]]·[[...]] 형태의 기존 링크 마커는 삭제하지 않는다
- 참고 자료가 주어지면 그 내용을 근거로 작성하고, 지어내지 않는다"""

_AI_EDIT_SELECTION_SYSTEM = """당신은 사내 업무 노트를 편집하는 어시스턴트입니다.
노트 전체는 맥락 참고용이며, 사용자가 선택한 부분만 지시에 따라 바꿉니다.

규칙:
- 선택된 부분을 대체할 텍스트만 출력한다 (코드펜스·설명·앞뒤 문맥 반복 금지)
- 선택 부분 밖의 내용은 출력하지 않는다
- 대체 텍스트는 앞뒤 문맥과 자연스럽게 이어져야 한다"""

_AI_EDIT_MAX_ATTACHMENT_CHARS = 12_000


@router.post("/notes/ai-edit")
async def ai_edit_note(request: Request) -> dict[str, Any]:
    """노트 AI 편집 (multipart form: instruction, content, title?, selection?, file?).

    - selection 있음: 그 부분의 대체 텍스트만 반환 (mode="selection")
    - selection 없음: 노트 전문 재작성 반환 (mode="whole")
    저장은 하지 않는다 — 프론트 편집 버퍼에 반영되고 사용자가 저장한다.
    """
    form = await request.form()
    instruction = str(form.get("instruction") or "").strip()
    content = str(form.get("content") or "")
    title = str(form.get("title") or "")
    selection = str(form.get("selection") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="instruction이 비어 있습니다.")

    ctx = getattr(request.app.state, "service_context", None)
    agent = getattr(ctx, "gemma_agent", None) if ctx else None
    if agent is None:
        raise HTTPException(status_code=503, detail="LLM이 준비되지 않았습니다 (백엔드 초기화 대기).")

    # 첨부 파일 → 텍스트 (격리 파서 재사용, 벡터 스토어 등록 안 함)
    attachment_text = ""
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        from .rag_routes import _parse_isolated

        data = await upload.read()  # type: ignore[union-attr]
        try:
            segments = await _parse_isolated(str(upload.filename), data)  # type: ignore[union-attr]
            attachment_text = "\n".join(t for t, _p in segments if t)[:_AI_EDIT_MAX_ATTACHMENT_CHARS]
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("노트 AI 편집 첨부 파싱 실패: %s", exc)
            raise HTTPException(status_code=422, detail="첨부 파일을 읽지 못했습니다.") from exc

    parts = [f"## 노트 제목\n{title}" if title else "", f"## 현재 노트 본문\n{content or '(빈 노트)'}"]
    if attachment_text:
        parts.append(f"## 참고 자료 (첨부)\n{attachment_text}")
    if selection:
        parts.append(f"## 선택된 부분 (이 부분만 바꿀 것)\n{selection}")
    parts.append(f"## 지시\n{instruction}")
    user_prompt = "\n\n".join(p for p in parts if p)

    system = _AI_EDIT_SELECTION_SYSTEM if selection else _AI_EDIT_WHOLE_SYSTEM
    try:
        result = await agent.complete_text(
            system, user_prompt, max_tokens=4096, temperature=0.3, timeout_seconds=300.0
        )
    except Exception as exc:
        logger.error("노트 AI 편집 LLM 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI 편집 실패: {exc}") from exc

    # 소형 모델이 규칙을 어기고 코드펜스로 감싸는 경우 방어
    cleaned = result.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    logger.info(
        "노트 AI 편집 완료: mode=%s, 지시=%r, 결과=%d자",
        "selection" if selection else "whole",
        instruction[:40],
        len(cleaned),
    )
    return {"mode": "selection" if selection else "whole", "result": cleaned}

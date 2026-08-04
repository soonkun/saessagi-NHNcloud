# src/app/kg_routes.py
"""M_23 지식그래프 파이프라인 REST API.

기존 그래프 탭에 재인덱싱·중단 버튼이 이미 있는데 새 파이프라인만 CLI로 두면
사용자가 터미널을 열어야 한다(사용자 지적). 같은 자리에서 시작·중단·진행 확인이
되도록 노출한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/kg", tags=["kg"])


def _svc(request: Request) -> Any:
    ctx = getattr(request.app.state, "service_context", None)
    svc = getattr(ctx, "kg_service", None) if ctx else None
    if svc is None:
        raise HTTPException(status_code=503, detail="지식그래프 파이프라인이 준비되지 않았습니다.")
    return svc


class StartReq(BaseModel):
    folder_id: str = ""
    doc_ids: list[str] | None = None
    limit: int = 0
    resume: bool = True
    chunks_per_document: int | None = None


@router.get("/folders")
async def folders(request: Request) -> dict[str, Any]:
    """폴더별 문서 수와 추출 완료 수 — UI에서 대상을 고르는 데 쓴다."""
    return {"folders": _svc(request).folders()}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    return {"running": svc.running, "progress": svc.progress(), "stats": svc.stats()}


@router.post("/start")
async def start(body: StartReq, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    result = await svc.start(
        folder_id=body.folder_id,
        doc_ids=body.doc_ids,
        limit=body.limit,
        resume=body.resume,
        chunks_per_document=body.chunks_per_document,
    )
    logger.info(f"KG 추출 시작 요청: folder={body.folder_id!r} → {result.get('started')}")
    return result


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    """진행 중인 청크가 끝나면 멈춘다 (강제 종료 아님)."""
    return await _svc(request).stop()

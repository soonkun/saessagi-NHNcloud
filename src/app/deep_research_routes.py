# src/app/deep_research_routes.py
"""M_20 DeepResearch — FastAPI 라우터 (SSE 스트리밍, CR-20).

회의록(meeting_minutes_routes)과 동일한 SSE 진행률 패턴.
첨부는 document_ingest 격리 파서(E-48)로 텍스트만 추출 — 벡터 스토어 등록 안 함.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024  # 30MB


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/deep-research/run-stream")
async def run_deep_research_stream(
    request: Request,
    project_id: str = Form(""),
    # CR-62 이전 프론트·스크립트는 mode를 보낸다. 시드 방의 project_id를 옛 mode 값과
    # 같게 뒀으므로 그대로 받아 준다 — 호출부를 깨지 않는다.
    mode: str = Form(""),
    prompt: str = Form(""),
    scope_doc_ids: str = Form(""),  # JSON 배열 — 그래프 핀 문서 범위 (CR-21 연동)
    file: UploadFile | None = File(None),
) -> StreamingResponse:
    """딥 리서치 실행 — SSE로 진행 이벤트 + 최종 보고서 스트리밍."""
    ctx = getattr(request.app.state, "service_context", None)
    service = getattr(ctx, "deep_research_service", None) if ctx else None
    store = getattr(ctx, "research_project_store", None) if ctx else None
    target_id = (project_id or mode or "").strip()

    # 첨부는 StreamingResponse 제너레이터 실행 시점엔 이미 닫혀 있으므로 먼저 읽어둔다
    # (meeting_minutes transcribe-stream과 동일 이유)
    attachment_name = ""
    attachment_bytes = b""
    if file is not None and file.filename:
        attachment_name = file.filename
        attachment_bytes = await file.read()

    scope: list[str] = []
    if scope_doc_ids.strip():
        try:
            parsed = json.loads(scope_doc_ids)
            if isinstance(parsed, list):
                scope = [str(s) for s in parsed if s]
        except Exception:
            logger.warning("scope_doc_ids 파싱 실패 (무시): %r", scope_doc_ids[:100])

    async def event_stream() -> Any:
        if service is None:
            yield _sse(
                {
                    "stage": "error",
                    "message": "딥 리서치 서비스가 준비되지 않았습니다 (백엔드 초기화 대기).",
                }
            )
            return

        attachment_text = ""
        if attachment_bytes:
            if len(attachment_bytes) > _MAX_ATTACHMENT_BYTES:
                yield _sse({"stage": "error", "message": "첨부 파일이 30MB를 초과합니다."})
                return
            yield _sse({"stage": "parsing", "message": f"첨부 파싱 중: {attachment_name}"})
            try:
                attachment_text = await _extract_text(attachment_name, attachment_bytes)
            except HTTPException as exc:
                yield _sse({"stage": "error", "message": str(exc.detail)})
                return
            except Exception as exc:
                logger.error("딥 리서치 첨부 파싱 실패 (%s): %s", attachment_name, exc)
                yield _sse(
                    {
                        "stage": "error",
                        "message": "첨부 파일을 읽지 못했습니다. PDF·DOCX·PPTX·HWPX·TXT·MD를 지원합니다.",
                    }
                )
                return
            if not attachment_text.strip():
                yield _sse(
                    {
                        "stage": "error",
                        "message": "첨부에서 텍스트를 추출하지 못했습니다 (스캔 이미지 PDF 등).",
                    }
                )
                return

        if store is None:
            yield _sse({"stage": "error", "message": "리서치 방 저장소가 준비되지 않았습니다."})
            return
        project = store.get_project(target_id) if target_id else None
        if project is None:
            yield _sse(
                {
                    "stage": "error",
                    "message": f"리서치 방을 찾을 수 없습니다: {target_id or '(미지정)'}",
                }
            )
            return
        profile = _profile_from(project)

        try:
            # 사용자 턴을 먼저 기록한다 — 실행이 실패해도 무엇을 물었는지는 남아야 한다.
            store.add_turn(
                project.project_id,
                "user",
                prompt,
                attachments=[attachment_name] if attachment_name else [],
            )
            async for event in service.run(profile, prompt, attachment_text, scope_doc_ids=scope):
                if event.get("stage") == "done":
                    store.add_turn(
                        project.project_id,
                        "assistant",
                        str(event.get("report") or ""),
                        sources=list(event.get("sources") or []),
                    )
                yield _sse(event)
        except Exception as exc:  # 파이프라인 밖 예외 — SSE로 전달 (연결 하드 종료 방지)
            logger.error("딥 리서치 스트림 예외: %s", exc)
            yield _sse({"stage": "error", "message": f"딥 리서치 실행 오류: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _profile_from(project: Any) -> Any:
    """방 → 실행 프로필. 서비스는 방을 모르고 프로필만 안다."""
    from deep_research.service import ResearchProfile

    return ResearchProfile(
        project_id=project.project_id,
        name=project.name,
        instructions=project.instructions,
        planner_hint=project.planner_hint,
        sub_queries=project.sub_queries,
        top_k_per_query=project.top_k_per_query,
        gap_rounds=project.gap_rounds,
        max_evidence_chunks=project.max_evidence_chunks,
    )


async def _extract_text(filename: str, data: bytes) -> str:
    """격리 파서(E-48)로 첨부에서 텍스트만 추출 — 벡터 스토어 등록 안 함."""
    from .rag_routes import _parse_isolated

    segments = await _parse_isolated(filename, data)
    return "\n".join(text for text, _page in segments if text)


def init_deep_research_routes() -> APIRouter:
    return router


# ── 방(프로젝트) 관리 (CR-62) ────────────────────────────────────────────────
#
# 지침 편집이 설정 화면을 떠나 여기로 왔다. 방마다 지침이 다르고 버전으로 관리되므로
# 전역 지침 목록(M_17)으로는 담을 수 없다.


class ProjectReq(BaseModel):
    name: str = ""
    description: str = ""
    icon: str = ""
    planner_hint: str = ""
    instructions: str = ""
    sub_queries: int | None = None
    top_k_per_query: int | None = None
    gap_rounds: int | None = None
    max_evidence_chunks: int | None = None


class InstructionsReq(BaseModel):
    instructions: str
    note: str = ""


class RestoreReq(BaseModel):
    version_no: int


def _store(request: Request) -> Any:
    ctx = getattr(request.app.state, "service_context", None)
    store = getattr(ctx, "research_project_store", None) if ctx else None
    if store is None:
        raise HTTPException(status_code=503, detail="리서치 방 저장소가 준비되지 않았습니다.")
    return store


def _require(store: Any, project_id: str) -> Any:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"방을 찾을 수 없습니다: {project_id}")
    return project


@router.get("/api/deep-research/projects")
async def list_projects(request: Request) -> dict[str, Any]:
    return {"projects": [p.as_dict() for p in _store(request).list_projects()]}


@router.post("/api/deep-research/projects", status_code=201)
async def create_project(body: ProjectReq, request: Request) -> dict[str, Any]:
    store = _store(request)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="방 이름을 입력하세요.")
    kw: dict[str, Any] = {
        k: v
        for k, v in {
            "description": body.description,
            "icon": body.icon,
            "planner_hint": body.planner_hint,
            "sub_queries": body.sub_queries,
            "top_k_per_query": body.top_k_per_query,
            "gap_rounds": body.gap_rounds,
            "max_evidence_chunks": body.max_evidence_chunks,
        }.items()
        if v is not None
    }
    project = store.create_project(name=body.name, instructions=body.instructions, **kw)
    logger.info("딥 리서치 방 생성: %s (%s)", project.name, project.project_id)
    return project.as_dict()


@router.patch("/api/deep-research/projects/{project_id}")
async def update_project(project_id: str, body: ProjectReq, request: Request) -> dict[str, Any]:
    """이름·설명·검색설정만. **지침은 별도 엔드포인트** — 버전이 남아야 하기 때문."""
    store = _store(request)
    _require(store, project_id)
    updated = store.update_project(
        project_id,
        name=body.name or None,
        description=body.description or None,
        icon=body.icon or None,
        planner_hint=body.planner_hint or None,
        sub_queries=body.sub_queries,
        top_k_per_query=body.top_k_per_query,
        gap_rounds=body.gap_rounds,
        max_evidence_chunks=body.max_evidence_chunks,
    )
    return updated.as_dict() if updated else {}


@router.delete("/api/deep-research/projects/{project_id}")
async def delete_project(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _require(store, project_id)
    counts = store.delete_project(project_id)
    return {"deleted": True, "removed": counts}


@router.get("/api/deep-research/projects/{project_id}/instructions")
async def get_instructions(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    project = _require(store, project_id)
    return {
        "project_id": project_id,
        "instructions": project.instructions,
        "version_no": project.version_no,
        "versions": [v.as_dict() for v in store.list_versions(project_id)],
    }


@router.post("/api/deep-research/projects/{project_id}/instructions")
async def save_instructions(
    project_id: str, body: InstructionsReq, request: Request
) -> dict[str, Any]:
    store = _store(request)
    _require(store, project_id)
    version = store.save_instructions(project_id, body.instructions, note=body.note)
    return {"saved": True, "version_no": version.version_no}


@router.post("/api/deep-research/projects/{project_id}/instructions/restore")
async def restore_instructions(
    project_id: str, body: RestoreReq, request: Request
) -> dict[str, Any]:
    """옛 버전을 **새 버전으로** 복원한다 — 이력을 자르지 않는다."""
    store = _store(request)
    _require(store, project_id)
    version = store.restore_version(project_id, body.version_no)
    if version is None:
        raise HTTPException(status_code=404, detail=f"버전 {body.version_no}을 찾을 수 없습니다.")
    return {"restored": True, "version_no": version.version_no, "content": version.content}


@router.get("/api/deep-research/projects/{project_id}/turns")
async def list_turns(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _require(store, project_id)
    return {"turns": [t.as_dict() for t in store.list_turns(project_id)]}


@router.delete("/api/deep-research/projects/{project_id}/turns")
async def clear_turns(project_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _require(store, project_id)
    return {"cleared": store.clear_turns(project_id)}

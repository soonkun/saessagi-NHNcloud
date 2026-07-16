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

logger = logging.getLogger(__name__)

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024  # 30MB


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/deep-research/run-stream")
async def run_deep_research_stream(
    request: Request,
    mode: str = Form(...),
    prompt: str = Form(""),
    file: UploadFile | None = File(None),
) -> StreamingResponse:
    """딥 리서치 실행 — SSE로 진행 이벤트 + 최종 보고서 스트리밍."""
    ctx = getattr(request.app.state, "service_context", None)
    service = getattr(ctx, "deep_research_service", None) if ctx else None

    # 첨부는 StreamingResponse 제너레이터 실행 시점엔 이미 닫혀 있으므로 먼저 읽어둔다
    # (meeting_minutes transcribe-stream과 동일 이유)
    attachment_name = ""
    attachment_bytes = b""
    if file is not None and file.filename:
        attachment_name = file.filename
        attachment_bytes = await file.read()

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

        try:
            async for event in service.run(mode, prompt, attachment_text):
                yield _sse(event)
        except Exception as exc:  # 파이프라인 밖 예외 — SSE로 전달 (연결 하드 종료 방지)
            logger.error("딥 리서치 스트림 예외: %s", exc)
            yield _sse({"stage": "error", "message": f"딥 리서치 실행 오류: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


async def _extract_text(filename: str, data: bytes) -> str:
    """격리 파서(E-48)로 첨부에서 텍스트만 추출 — 벡터 스토어 등록 안 함."""
    from .rag_routes import _parse_isolated

    segments = await _parse_isolated(filename, data)
    return "\n".join(text for text, _page in segments if text)


def init_deep_research_routes() -> APIRouter:
    return router

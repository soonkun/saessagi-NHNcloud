# src/app/meeting_minutes_routes.py
"""M_13 MeetingMinutes — FastAPI 다운로드 라우터."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/download/{file_id}")
async def download_meeting_minutes(file_id: str, request: Request) -> FileResponse:
    """임시 회의록 파일을 스트리밍 반환.

    - 404: file_id 미존재 또는 24h 초과로 삭제됨.
    - 422: file_id가 UUIDv4 형식 아님.
    - 503: meeting_minutes_service 미초기화(템플릿 부재 등).
    - 200: HWPX 파일 (Content-Type: application/vnd.hancom.hwpx).
    """
    ctx = request.app.state.service_context  # AppServiceContext
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="meeting_minutes_service unavailable")

    from meeting_minutes.errors import MeetingFileNotFoundError

    try:
        path = service.resolve(file_id)
    except ValueError as exc:
        logger.info(f"download: 잘못된 file_id={file_id!r}: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except MeetingFileNotFoundError:
        logger.info(f"download: 파일 없음 또는 만료: file_id={file_id}")
        raise HTTPException(status_code=404, detail="file expired or not found")

    logger.info(f"download: file_id={file_id}, path={path}")
    return FileResponse(
        path=path,
        media_type="application/vnd.hancom.hwpx",
        filename=f"회의결과보고서_{file_id[:8]}.hwpx",
    )

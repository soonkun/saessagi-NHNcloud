# src/app/meeting_minutes_routes.py
"""M_13 MeetingMinutes — FastAPI 라우터 (다운로드 + 생성)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ────────────────────────────────────────────────────────────
# 생성 엔드포인트 — JSON body (transcript 직접 입력)
# ────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="회의 녹취 텍스트")
    pages: Literal[2, 3] = Field(2, description="보고서 분량 (2 또는 3페이지)")


@router.post("/api/meeting-minutes/generate")
async def generate_meeting_minutes_json(
    body: GenerateRequest, request: Request
) -> dict[str, str]:
    """녹취 텍스트 → HWPX 회의록 생성.

    - 200: {"file_id", "download_url", "expires_at"}
    - 503: service 미초기화
    """
    ctx = request.app.state.service_context
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="meeting_minutes_service unavailable")

    try:
        result = await service.generate(body.transcript, body.pages)
    except Exception as exc:
        logger.error(f"generate failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ────────────────────────────────────────────────────────────
# 생성 엔드포인트 — multipart (오디오 파일 업로드)
# ────────────────────────────────────────────────────────────

@router.post("/api/meeting-minutes/generate-audio")
async def generate_meeting_minutes_audio(
    request: Request,
    audio_file: UploadFile = File(...),
    pages: int = Form(2),
    transcript: str = Form(""),
) -> dict[str, str]:
    """오디오 파일 → STT → HWPX 회의록 생성.

    STT 미가용 시 transcript(text) 폴백.
    - 200: {"file_id", "download_url", "expires_at"}
    - 503: service 미초기화
    - 422: pages가 2 또는 3이 아님
    """
    if pages not in (2, 3):
        raise HTTPException(status_code=422, detail="pages must be 2 or 3")

    ctx = request.app.state.service_context
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="meeting_minutes_service unavailable")

    # 오디오 → STT 시도, 실패 시 transcript 폴백
    final_transcript = transcript.strip()

    stt_service = getattr(ctx, "asr_service", None)
    if stt_service is not None:
        try:
            audio_bytes = await audio_file.read()
            with tempfile.NamedTemporaryFile(
                suffix=Path(audio_file.filename or "audio.wav").suffix, delete=False
            ) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            stt_result = await stt_service.transcribe(Path(tmp_path))
            final_transcript = stt_result or final_transcript
        except Exception as exc:
            logger.warning(f"STT 실패, transcript 폴백: {exc}")
    else:
        logger.info("asr_service 없음 — transcript 폴백 사용")

    if not final_transcript:
        raise HTTPException(
            status_code=422,
            detail="transcript가 비어 있고 STT도 실패했습니다. 녹취 텍스트를 직접 입력하세요.",
        )

    try:
        result = await service.generate(final_transcript, pages)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error(f"generate failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ────────────────────────────────────────────────────────────
# 다운로드 엔드포인트
# ────────────────────────────────────────────────────────────

@router.get("/download/{file_id}")
async def download_meeting_minutes(file_id: str, request: Request) -> FileResponse:
    """임시 회의록 파일을 스트리밍 반환.

    - 404: file_id 미존재 또는 24h 초과로 삭제됨.
    - 422: file_id가 UUIDv4 형식 아님.
    - 503: meeting_minutes_service 미초기화.
    - 200: HWPX 파일 (Content-Type: application/vnd.hancom.hwpx).
    """
    ctx = request.app.state.service_context
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

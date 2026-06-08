# src/app/meeting_minutes_routes.py
"""M_13 MeetingMinutes — FastAPI 라우터 (다운로드 + 생성)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

import numpy as np

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

_TARGET_SR = 16_000


def _decode_audio(audio_bytes: bytes, suffix: str) -> np.ndarray:
    """오디오 바이트 → 16kHz mono float32 numpy 배열.

    1차: soundfile (WAV·FLAC·OGG — 외부 의존성 없음)
    2차: afconvert (M4A·QTA 등 Apple 포맷 — macOS 내장, ffmpeg 불필요)
    3차: pydub (기타 포맷 — ffmpeg 필요)
    """
    import io

    import soundfile as sf

    data: np.ndarray
    sr: int

    try:
        with sf.SoundFile(io.BytesIO(audio_bytes)) as f:
            sr = f.samplerate
            data = f.read(dtype="float32", always_2d=False)
    except Exception:
        data, sr = _decode_via_afconvert(audio_bytes, suffix)

    # stereo → mono
    if data.ndim > 1:
        data = data.mean(axis=1)

    # 리샘플링 (필요 시)
    if sr != _TARGET_SR:
        import librosa

        data = librosa.resample(data, orig_sr=sr, target_sr=_TARGET_SR)

    return data


def _decode_via_afconvert(audio_bytes: bytes, suffix: str) -> tuple[np.ndarray, int]:
    """macOS afconvert로 오디오 → 16kHz mono WAV → numpy 배열.

    afconvert 실패 시 pydub(ffmpeg 필요) 폴백.
    """
    import io
    import shutil
    import subprocess
    import tempfile

    import soundfile as sf

    if shutil.which("afconvert"):
        with (
            tempfile.NamedTemporaryFile(suffix=suffix or ".m4a", delete=False) as src,
            tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst,
        ):
            src.write(audio_bytes)
            src_path, dst_path = src.name, dst.name
        try:
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", src_path, dst_path],
                check=True,
                capture_output=True,
            )
            with sf.SoundFile(dst_path) as f:
                sr = f.samplerate
                data = f.read(dtype="float32", always_2d=False)
            return data, sr
        finally:
            Path(src_path).unlink(missing_ok=True)
            Path(dst_path).unlink(missing_ok=True)

    # 최종 폴백: pydub (ffmpeg 필요)
    from pydub import AudioSegment

    seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=suffix.lstrip(".") or None)
    seg = seg.set_channels(1).set_frame_rate(_TARGET_SR).set_sample_width(2)
    data = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    return data, _TARGET_SR


# ────────────────────────────────────────────────────────────
# 생성 엔드포인트 — JSON body (transcript 직접 입력)
# ────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="회의 녹취 텍스트")
    pages: Literal[1, 2] = Field(1, description="보고서 분량 (1 또는 2페이지)")


@router.post("/api/meeting-minutes/generate")
async def generate_meeting_minutes_json(body: GenerateRequest, request: Request) -> dict[str, str]:
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
    pages: int = Form(1),
    transcript: str = Form(""),
) -> dict[str, str]:
    """오디오 파일 → STT → HWPX 회의록 생성.

    STT 미가용 시 transcript(text) 폴백.
    - 200: {"file_id", "download_url", "expires_at"}
    - 503: service 미초기화
    - 422: pages가 1 또는 2가 아님
    """
    if pages not in (1, 2):
        raise HTTPException(status_code=422, detail="pages must be 1 or 2")

    ctx = request.app.state.service_context
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="meeting_minutes_service unavailable")

    # 오디오 → STT 시도, 실패 시 transcript 폴백
    final_transcript = transcript.strip()

    asr_engine = getattr(ctx, "asr_engine", None)
    if asr_engine is not None:
        try:
            audio_bytes = await audio_file.read()
            suffix = Path(audio_file.filename or "audio.wav").suffix.lower()
            audio_array = await asyncio.to_thread(_decode_audio, audio_bytes, suffix)
            stt_result = await asr_engine.async_transcribe_np(audio_array)
            final_transcript = stt_result.strip() or final_transcript
            logger.info(f"STT 성공: {len(final_transcript)}자")
        except Exception as exc:
            logger.warning(f"STT 실패, transcript 폴백: {exc}")
    else:
        logger.info("asr_engine 없음 — transcript 폴백 사용")

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
# SSE 헬퍼
# ────────────────────────────────────────────────────────────


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ────────────────────────────────────────────────────────────
# Step 1: 전사 (오디오 → 텍스트)
# ────────────────────────────────────────────────────────────


@router.post("/api/meeting-minutes/transcribe-stream")
async def transcribe_stream(
    request: Request,
    audio_file: UploadFile = File(...),
) -> StreamingResponse:
    """오디오 파일 → STT → 녹취 텍스트 SSE 스트리밍.

    이벤트: {"stage":"transcribing","message":"..."} → {"stage":"done","transcript":"..."}
    """
    ctx = request.app.state.service_context
    # UploadFile은 StreamingResponse 제너레이터 실행 시점엔 이미 닫혀 있으므로 먼저 읽어둔다
    audio_bytes = await audio_file.read()
    suffix = Path(audio_file.filename or "audio.wav").suffix.lower()

    async def run():
        asr_engine = getattr(ctx, "asr_engine", None)
        if asr_engine is None:
            yield _sse(
                {
                    "stage": "error",
                    "message": "STT 엔진이 초기화되지 않았습니다. conf.yaml의 asr_config를 확인하세요.",
                }
            )
            return
        try:
            yield _sse({"stage": "transcribing", "message": "음성 인식 중..."})
            arr = await asyncio.to_thread(_decode_audio, audio_bytes, suffix)
            transcript = await asr_engine.async_transcribe_np(arr)
            if not transcript.strip():
                yield _sse(
                    {
                        "stage": "error",
                        "message": "음성이 인식되지 않았습니다. 오디오 품질을 확인하거나 텍스트를 직접 입력하세요.",
                    }
                )
                return
            yield _sse({"stage": "done", "transcript": transcript.strip()})
        except Exception as exc:
            logger.error(f"transcribe-stream 실패: {exc}")
            yield _sse({"stage": "error", "message": str(exc) or "전사 중 오류가 발생했습니다."})

    return StreamingResponse(run(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ────────────────────────────────────────────────────────────
# Step 2: 회의록 작성 (녹취 텍스트 → 회의록 텍스트)
# ────────────────────────────────────────────────────────────


@router.post("/api/meeting-minutes/summarize-stream")
async def summarize_stream(
    request: Request,
    transcript: str = Form(...),
    pages: int = Form(1),
) -> StreamingResponse:
    """녹취 텍스트 → 회의록 텍스트 SSE 스트리밍 (Step 2).

    plain text 출력 — JSON 스키마 없이 호출하여 빈 응답 문제 회피.
    pages: 1 또는 2 (보고서 분량 — 회의록 상세도 결정에 사용)
    이벤트: 진행 메시지 → {"stage":"done","meeting_notes":"..."}
    """
    if pages not in (1, 2):
        pages = 1
    ctx = request.app.state.service_context
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:

        async def _err():
            yield _sse({"stage": "error", "message": "회의록 서비스가 준비되지 않았습니다."})

        return StreamingResponse(_err(), media_type="text/event-stream", headers=_SSE_HEADERS)

    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def progress_cb(stage: str, message: str) -> None:
        await queue.put({"stage": stage, "message": message})

    async def run() -> None:
        try:
            if not transcript.strip():
                await queue.put({"stage": "error", "message": "녹취 텍스트가 비어 있습니다."})
                return
            notes = await service._generator.summarize_to_text(
                transcript.strip(),
                progress_cb,
                pages=pages,  # type: ignore[arg-type]
            )
            await queue.put({"stage": "done", "meeting_notes": notes})
        except TimeoutError:
            await queue.put(
                {
                    "stage": "error",
                    "message": "LLM 응답 시간 초과. 텍스트가 너무 길면 일부만 붙여넣어 시도하세요.",
                }
            )
        except Exception as exc:
            logger.error(f"summarize-stream 실패: {exc}")
            await queue.put(
                {"stage": "error", "message": str(exc) or "회의록 작성 중 오류가 발생했습니다."}
            )

    task = asyncio.create_task(run())

    async def event_stream():
        try:
            while True:
                item = await queue.get()
                yield _sse(item)
                if item["stage"] in ("done", "error"):
                    break
        finally:
            task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/api/meeting-minutes/generate-stream")
async def generate_meeting_minutes_stream(
    request: Request,
    audio_file: UploadFile | None = File(None),
    pages: int = Form(1),
    transcript: str = Form(""),
) -> StreamingResponse:
    """진행 상황을 SSE로 스트리밍하며 회의록 생성.

    이벤트 형식:
      data: {"stage": "stt",          "message": "음성 인식 중..."}
      data: {"stage": "chunk_start",  "message": "N개 구간으로 나눠 요약합니다..."}
      data: {"stage": "chunk",        "message": "구간 요약 중... (1/N)"}
      data: {"stage": "generate",     "message": "회의록 초안 작성 중..."}
      data: {"stage": "done",         "file_id": ..., "download_url": ..., "expires_at": ...}
      data: {"stage": "error",        "message": "오류 내용"}
    """
    if pages not in (1, 2):

        async def _err():
            yield _sse({"stage": "error", "message": "pages는 1 또는 2여야 합니다."})

        return StreamingResponse(_err(), media_type="text/event-stream")

    ctx = request.app.state.service_context
    service = getattr(ctx, "meeting_minutes_service", None)
    if service is None:

        async def _err():
            yield _sse({"stage": "error", "message": "회의록 서비스가 준비되지 않았습니다."})

        return StreamingResponse(_err(), media_type="text/event-stream")

    # 오디오·텍스트 수집
    final_transcript = transcript.strip()
    audio_bytes: bytes | None = None
    audio_suffix = ".wav"
    if audio_file and audio_file.filename:
        audio_bytes = await audio_file.read()
        audio_suffix = Path(audio_file.filename).suffix.lower()

    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def progress_cb(stage: str, message: str) -> None:
        await queue.put({"stage": stage, "message": message})

    async def run() -> None:
        try:
            # STT
            if audio_bytes:
                await queue.put({"stage": "stt", "message": "음성 인식 중..."})
                asr_engine = getattr(ctx, "asr_engine", None)
                if asr_engine is not None:
                    try:
                        arr = await asyncio.to_thread(_decode_audio, audio_bytes, audio_suffix)
                        stt_result = await asr_engine.async_transcribe_np(arr)
                        final = stt_result.strip() or final_transcript
                    except Exception as exc:
                        logger.warning(f"STT 실패: {exc}")
                        final = final_transcript
                else:
                    final = final_transcript
            else:
                final = final_transcript

            if not final:
                await queue.put(
                    {
                        "stage": "error",
                        "message": "녹취 텍스트가 없습니다. 텍스트를 직접 입력하거나 오디오 파일을 첨부하세요.",
                    }
                )
                return

            result = await service.generate(final, pages, progress_cb=progress_cb)  # type: ignore[arg-type]
            await queue.put({"stage": "done", **result})
        except Exception as exc:
            logger.error(f"generate-stream failed: {exc}")
            msg = str(exc) or "회의록 생성에 실패했습니다."
            await queue.put({"stage": "error", "message": msg})

    task = asyncio.create_task(run())

    async def event_stream():
        try:
            while True:
                item = await queue.get()
                yield _sse(item)
                if item["stage"] in ("done", "error"):
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

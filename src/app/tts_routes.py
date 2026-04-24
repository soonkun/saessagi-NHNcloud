"""TTS REST 엔드포인트 — 텍스트를 MeloTTS로 합성해 base64 오디오 반환."""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/tts", tags=["tts"])


class SpeakRequest(BaseModel):
    text: str


@router.post("/speak")
async def speak(body: SpeakRequest, request: Request) -> dict[str, str]:
    """텍스트를 MeloTTS로 합성해 base64 WAV를 반환한다."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text가 비어 있습니다.")

    ctx = getattr(request.app.state, "service_context", None)
    tts = getattr(ctx, "tts_engine", None) if ctx else None
    if tts is None:
        raise HTTPException(status_code=503, detail="TTS 엔진이 초기화되지 않았습니다.")

    try:
        audio_path: str = await tts.async_generate_audio(text)
        data = Path(audio_path).read_bytes()
        b64 = base64.b64encode(data).decode()
        logger.debug(f"TTS /api/tts/speak: {len(data)} bytes for {len(text)} chars")
        return {"audio": b64, "format": "wav"}
    except Exception as exc:
        logger.error(f"TTS speak 실패: {exc}")
        raise HTTPException(status_code=500, detail=f"TTS 합성 실패: {exc}") from exc

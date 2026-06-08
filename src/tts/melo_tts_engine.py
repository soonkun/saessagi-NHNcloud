# src/tts/melo_tts_engine.py
"""MeloTTS 한국어 전용 TTS 엔진."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from open_llm_vtuber.tts.tts_interface import TTSInterface  # upstream

from ._device import resolve_device
from .errors import TTSInitError, TTSRuntimeError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 공통 상수
MELOTTS_SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"KR"})
MELOTTS_SUPPORTED_SAMPLE_RATES: frozenset[int] = frozenset({16000, 22050, 24000, 44100, 48000})
MELOTTS_MIN_SPEED: float = 0.5
MELOTTS_MAX_SPEED: float = 2.0

# MeloTTS 필수 파일 목록
MELO_REQUIRED_FILES: list[str] = ["config.json", "checkpoint.pth"]

MAX_TEXT_CHARS: int = 1000

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _korean_time(text: str) -> str:
    """HH:MM 형식을 한국어 구어체로 변환한다.

    09:00 → 오전 9시
    09:30 → 오전 9시 30분
    14:00 → 오후 2시
    13:45 → 오후 1시 45분
    """

    def _replace(m: re.Match[str]) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        period = "오전" if h < 12 else "오후"
        h12 = h % 12 or 12
        return f"{period} {h12}시" if mi == 0 else f"{period} {h12}시 {mi}분"

    return _TIME_RE.sub(_replace, text)


def _project_root() -> str:
    """src/tts/_device.py 기준으로 프로젝트 루트 디렉토리를 반환한다."""
    # src/tts/melo_tts_engine.py → ../../ = project root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _set_offline_env_vars() -> None:
    """오프라인 강제 환경변수를 설정한다. 이미 설정된 값은 건드리지 않는다."""
    if not os.environ.get("HF_HUB_OFFLINE"):
        os.environ["HF_HUB_OFFLINE"] = "1"
    if not os.environ.get("TRANSFORMERS_OFFLINE"):
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    if not os.environ.get("NLTK_DATA"):
        os.environ["NLTK_DATA"] = os.path.join(_project_root(), "assets", "nltk_data")
    logger.debug(
        "offline env: HF_HUB_OFFLINE=%s TRANSFORMERS_OFFLINE=%s NLTK_DATA=%s",
        os.environ.get("HF_HUB_OFFLINE"),
        os.environ.get("TRANSFORMERS_OFFLINE"),
        os.environ.get("NLTK_DATA"),
    )


def _ensure_cache_dir(cache_dir: str) -> None:
    """캐시 디렉토리가 없으면 생성한다."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as exc:
        logger.error("Failed to create cache_dir %s: %s", cache_dir, exc)
        raise TTSInitError(f"cache dir not writable: {cache_dir}") from exc


class MeloTTSEngine(TTSInterface):  # type: ignore[misc]
    """한국어(KR) 전용 MeloTTS 엔진.

    upstream TTSInterface를 상속. async_generate_audio는 asyncio.Lock + to_thread를 사용.
    """

    model_dir: str
    language: str  # 항상 "KR"
    speaker: str  # 항상 "KR"
    speaker_id: int
    sample_rate: int
    speed: float
    device: str
    resolved_device: str
    cache_dir: str

    def __init__(
        self,
        model_dir: str,
        speaker: str = "KR",
        language: str = "KR",
        speaker_id: int | None = None,
        sample_rate: int = 24000,
        speed: float = 1.0,
        device: str = "auto",
        cache_dir: str = "cache",
    ) -> None:
        """즉시 로드. 모든 유효성 검증은 import 전에 수행.

        Raises:
            TTSInitError: 설정 오류 또는 모델 로드 실패.
        """
        # 1. model_dir 검증 (설정된 경우에만; melo는 HF Hub 캐시에서 자동 로딩)
        model_path = Path(model_dir) if model_dir else None
        if model_path is not None:
            if not model_path.exists() or not model_path.is_dir():
                logger.warning("model_dir not found: %s — melo 자동 다운로드 경로 사용", model_dir)
                model_path = None
            else:
                missing_files = [f for f in MELO_REQUIRED_FILES if not (model_path / f).exists()]
                if missing_files:
                    logger.warning(
                        "model weights missing %s — melo 자동 다운로드 경로 사용", missing_files
                    )
                    model_path = None

        # 2. language 검증
        if language not in MELOTTS_SUPPORTED_LANGUAGES:
            logger.error("unsupported language: %s", language)
            raise TTSInitError(f"unsupported language: {language!r} (must be 'KR')")

        # 3. speaker 검증
        if speaker != "KR":
            logger.error("unsupported speaker: %s", speaker)
            raise TTSInitError(f"unsupported speaker: {speaker!r} (must be 'KR')")

        # 4. speed 검증
        if not (MELOTTS_MIN_SPEED <= speed <= MELOTTS_MAX_SPEED):
            logger.error("speed out of range: %s", speed)
            raise TTSInitError(
                f"speed out of range: {speed} (must be [{MELOTTS_MIN_SPEED}, {MELOTTS_MAX_SPEED}])"
            )

        # 5. sample_rate 검증
        if sample_rate not in MELOTTS_SUPPORTED_SAMPLE_RATES:
            logger.error("unsupported sample_rate: %s", sample_rate)
            raise TTSInitError(
                f"unsupported sample_rate: {sample_rate} "
                f"(allowed: {sorted(MELOTTS_SUPPORTED_SAMPLE_RATES)})"
            )

        # 6. device 검증
        supported_devices: frozenset[str] = frozenset({"auto", "cpu", "cuda"})
        if device not in supported_devices:
            logger.error("unsupported device: %s", device)
            raise TTSInitError(
                f"unsupported device: {device!r} (must be one of {supported_devices})"
            )

        # 7. CUDA 가용성 체크 (device="cuda" 강제 시 미가용이면 즉시 실패)
        resolved_device = resolve_device(device)

        # 8. 환경변수 설정 (오프라인 강제) — import 전에 설정
        _set_offline_env_vars()

        # 9. melo 지연 import + 모델 로드
        try:
            from melo.api import TTS as MeloTTS
        except ImportError as exc:
            logger.error("Failed to import melo: %s", exc)
            raise TTSInitError(f"Failed to import melo.api.TTS: {exc}") from exc

        try:
            model = MeloTTS(language="KR", device=resolved_device)
        except Exception as exc:
            logger.error("MeloTTS init failed: %s", exc)
            raise TTSInitError(str(exc)) from exc

        # 10. spk2id 검증
        try:
            spk2id: dict[str, int] = model.hps.data.spk2id
        except AttributeError as exc:
            logger.error("Cannot access hps.data.spk2id: %s", exc)
            raise TTSInitError(f"Cannot access hps.data.spk2id: {exc}") from exc

        if "KR" not in spk2id:
            logger.error("'KR' not found in spk2id: %s", spk2id)
            raise TTSInitError(f"'KR' not found in hps.data.spk2id: {spk2id}")

        if speaker_id is None:
            resolved_speaker_id = spk2id["KR"]
        else:
            max_id = max(spk2id.values()) if spk2id else 0
            if speaker_id < 0 or speaker_id > max_id:
                logger.error("speaker_id out of range: %d", speaker_id)
                raise TTSInitError(f"speaker_id out of range: {speaker_id} (max: {max_id})")
            resolved_speaker_id = speaker_id

        # 캐시 디렉토리 생성
        _ensure_cache_dir(cache_dir)

        # 인스턴스 속성 저장
        self.model_dir = str(model_path.resolve()) if model_path is not None else ""
        self.language = language
        self.speaker = speaker
        self.speaker_id = resolved_speaker_id
        self.sample_rate = sample_rate
        self.speed = speed
        self.device = device
        self.resolved_device = resolved_device
        self.cache_dir = cache_dir
        self._model = model
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()

        logger.info(
            "MeloTTSEngine initialized: language=%s speaker=%s speaker_id=%d "
            "sample_rate=%d speed=%.1f device=%s resolved=%s",
            language,
            speaker,
            resolved_speaker_id,
            sample_rate,
            speed,
            device,
            resolved_device,
        )

    def generate_audio(
        self,
        text: str,
        file_name_no_ext: str | None = None,
    ) -> str:
        """동기 TTS 합성.

        Args:
            text: 합성할 텍스트.
            file_name_no_ext: 출력 파일명(확장자 제외). None이면 "temp".

        Returns:
            str: 생성된 WAV 파일의 절대 경로.

        Raises:
            TTSRuntimeError: 백엔드 예외 또는 출력 파일 누락.
        """
        # 1. 빈 텍스트 검증
        if not text or not text.strip():
            logger.error("empty text passed to generate_audio")
            raise TTSRuntimeError("empty text")

        # 1-a. 시간 포맷 전처리 (09:00 → 오전 9시)
        text = _korean_time(text)

        # 2. 1000자 초과 절단
        if len(text) > MAX_TEXT_CHARS:
            logger.warning(
                "text length %d exceeds max %d chars; truncating",
                len(text),
                MAX_TEXT_CHARS,
            )
            text = text[:MAX_TEXT_CHARS]

        # 3. 출력 경로 생성 — cache_dir 사용, basename 강제로 path traversal 방지
        _stem = Path(file_name_no_ext).name if file_name_no_ext is not None else "temp"
        if not _stem:
            _stem = "temp"
        output_path: str = str(os.path.abspath(os.path.join(self.cache_dir, f"{_stem}.wav")))

        # 4. 캐시 디렉토리 존재 확인
        output_dir = os.path.dirname(output_path)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            logger.error("cache dir not writable: %s", output_dir)
            raise TTSRuntimeError(f"cache dir not writable: {output_dir}") from exc

        # 5. 합성 실행 (thread lock으로 직렬화)
        with self._thread_lock:
            try:
                self._model.tts_to_file(
                    text, self.speaker_id, output_path, speed=self.speed, quiet=True
                )
            except Exception as exc:
                logger.error("TTS synthesis failed: %s", exc)
                raise TTSRuntimeError(str(exc)) from exc

        # 6. 출력 파일 존재 확인
        if not os.path.exists(output_path):
            logger.error("output file not written: %s", output_path)
            raise TTSRuntimeError(f"output file not written: {output_path}")

        logger.debug("generate_audio OK: path=%s", output_path)
        return output_path

    async def async_generate_audio(
        self,
        text: str,
        file_name_no_ext: str | None = None,
    ) -> str:
        """비동기 TTS 합성 — asyncio.Lock으로 동시 호출 직렬화."""
        async with self._lock:
            result: str = await asyncio.to_thread(self.generate_audio, text, file_name_no_ext)
        return result

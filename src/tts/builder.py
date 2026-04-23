# src/tts/builder.py
"""TTS 엔진 빌더 — AppConfig에서 MeloTTSEngine 또는 XttsV2Engine을 구성한다."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Union

from .errors import TTSInitError
from .melo_tts_engine import MeloTTSEngine

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)

try:
    from .xtts_v2_engine import XttsV2Engine  # type: ignore[assignment]

    TtsEngine = Union[MeloTTSEngine, XttsV2Engine]
except Exception:
    # TTS 패키지 미설치(macOS/Python 3.12 등) — MeloTTS 단독 운용
    XttsV2Engine = None  # type: ignore[assignment,misc]
    TtsEngine = MeloTTSEngine  # type: ignore[assignment,misc]


def resolve_melotts_dir(asset_root: str = "assets/models") -> str:
    """<asset_root>/melotts-ko 경로를 반환한다."""
    return f"{asset_root}/melotts-ko"


def resolve_xtts_v2_dir(asset_root: str = "assets/models") -> str:
    """<asset_root>/xtts_v2 경로를 반환한다."""
    return f"{asset_root}/xtts_v2"


def build_tts_engine(
    app_config: "AppConfig",
    asset_root: str = "assets/models",
    cache_dir: str = "cache",
) -> TtsEngine:
    """AppConfig.tts에 따라 TTS 엔진을 구성한다.

    - app_config.tts.engine == "melo" -> MeloTTSEngine
    - app_config.tts.engine == "xtts_v2" -> XttsV2Engine
        - app_config.tts.xtts.speaker_wav가 None이면 TTSInitError.

    Args:
        app_config: 본 프로젝트 AppConfig 인스턴스.
        asset_root: 모델이 위치한 루트 디렉토리.
        cache_dir: TTS 캐시 디렉토리.

    Returns:
        TtsEngine: 초기화된 TTS 엔진 인스턴스.

    Raises:
        TTSInitError: 설정 값 위반 또는 모델 로드 실패.
    """
    tts_config = app_config.tts
    engine_kind = str(
        tts_config.engine.value if hasattr(tts_config.engine, "value") else tts_config.engine
    )

    logger.info("build_tts_engine: engine=%s", engine_kind)

    if engine_kind == "melo":
        melo_cfg = tts_config.melo
        model_dir = melo_cfg.model_dir if melo_cfg.model_dir else resolve_melotts_dir(asset_root)
        logger.info("Building MeloTTSEngine: model_dir=%s", model_dir)
        return MeloTTSEngine(
            model_dir=model_dir,
            speaker=melo_cfg.speaker,
            language=melo_cfg.language,
            speaker_id=melo_cfg.speaker_id,
            sample_rate=melo_cfg.sample_rate,
            speed=melo_cfg.speed,
            device=melo_cfg.device,
            cache_dir=cache_dir,
        )

    elif engine_kind == "xtts_v2":
        if XttsV2Engine is None:
            raise TTSInitError("XTTS v2 engine unavailable: TTS package not installed")

        xtts_cfg = tts_config.xtts
        speaker_wav = xtts_cfg.speaker_wav
        if speaker_wav is None:
            logger.error("speaker_wav required for xtts_v2 but not set")
            raise TTSInitError("speaker_wav required for xtts_v2")

        model_dir = xtts_cfg.model_dir if xtts_cfg.model_dir else resolve_xtts_v2_dir(asset_root)
        logger.info("Building XttsV2Engine: model_dir=%s speaker_wav=%s", model_dir, speaker_wav)
        return XttsV2Engine(
            model_dir=model_dir,
            speaker_wav=speaker_wav,
            language=xtts_cfg.language,
            device=xtts_cfg.device,
            cache_dir=cache_dir,
        )

    else:
        raise TTSInitError(f"unknown tts engine: {engine_kind!r}")

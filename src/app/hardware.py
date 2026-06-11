# src/app/hardware.py
"""하드웨어 자동 감지 및 최적 설정 추천."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class HardwareProfile:
    """감지된 하드웨어 프로파일 및 권장 설정."""

    platform: str  # "darwin" | "windows" | "linux"
    arch: str  # "arm64" | "x86_64" | ...
    ram_gb: float  # 전체 RAM (GB)
    has_cuda: bool
    cuda_vram_gb: float  # CUDA VRAM (없으면 0.0)
    cuda_device_name: str  # 예: "NVIDIA GeForce RTX 4090"
    has_mps: bool  # Apple Metal (macOS ARM)

    # 권장 Whisper 설정
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str

    # 권장 TTS 설정
    tts_device: str

    # 권장 RAG 임베딩 배치 크기 (BGE-M3). VRAM이 클수록 배치를 키워 처리량 확보.
    embed_batch_size: int


def _get_ram_gb() -> float:
    try:
        import psutil

        return float(psutil.virtual_memory().total) / (1024**3)
    except Exception:
        return 0.0


def _get_cuda_info() -> tuple[bool, float, str]:
    """(has_cuda, vram_gb, device_name)"""
    try:
        import torch

        if not torch.cuda.is_available():
            return False, 0.0, ""
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024**3)
        return True, vram_gb, props.name
    except Exception:
        return False, 0.0, ""


def _has_mps() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_available())
    except Exception:
        return False


def detect() -> HardwareProfile:
    """현재 하드웨어를 감지하고 최적 설정을 반환한다."""
    sys_platform = platform.system().lower()  # "darwin" / "windows" / "linux"
    arch = platform.machine().lower()  # "arm64" / "x86_64" / "amd64"
    ram_gb = _get_ram_gb()
    has_cuda, cuda_vram_gb, cuda_device_name = _get_cuda_info()
    mps_ok = _has_mps()

    # ── RAG 임베딩 배치 크기 결정 ────────────────────────────────────────────
    # 청크 크기는 검색 품질 기준이라 하드웨어에 연동하지 않는다. 배치만 VRAM에 연동.
    if has_cuda and cuda_vram_gb >= 16:
        embed_batch_size = 128
    elif has_cuda and cuda_vram_gb >= 6:
        embed_batch_size = 64
    elif has_cuda or mps_ok:
        embed_batch_size = 32
    else:
        embed_batch_size = 16

    # ── Whisper 설정 결정 ────────────────────────────────────────────────────
    if has_cuda:
        # NVIDIA GPU
        if cuda_vram_gb >= 16:
            # RTX 4090, A100 등 — large-v3-turbo float16
            whisper_model = "large-v3-turbo"
            whisper_device = "cuda"
            whisper_compute_type = "float16"
        elif cuda_vram_gb >= 6:
            # RTX 3060 수준 — large-v3-turbo int8_float16
            whisper_model = "large-v3-turbo"
            whisper_device = "cuda"
            whisper_compute_type = "int8_float16"
        else:
            # 저사양 CUDA — medium int8_float16
            whisper_model = "medium"
            whisper_device = "cuda"
            whisper_compute_type = "int8_float16"
        tts_device = "cuda"
    elif mps_ok:
        # Apple Silicon — CPU int8 (faster-whisper MPS 미지원, CTranslate2 metal 미지원)
        whisper_model = "large-v3-turbo"
        whisper_device = "cpu"
        whisper_compute_type = "int8"
        tts_device = "auto"  # PyTorch MPS 사용
    else:
        # CPU 전용
        if ram_gb >= 16:
            whisper_model = "large-v3-turbo"
        else:
            whisper_model = "medium"
        whisper_device = "cpu"
        whisper_compute_type = "int8"
        tts_device = "cpu"

    return HardwareProfile(
        platform=sys_platform,
        arch=arch,
        ram_gb=ram_gb,
        has_cuda=has_cuda,
        cuda_vram_gb=cuda_vram_gb,
        cuda_device_name=cuda_device_name,
        has_mps=mps_ok,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        whisper_compute_type=whisper_compute_type,
        tts_device=tts_device,
        embed_batch_size=embed_batch_size,
    )


def apply_to_config(upstream_config: Any, hw: HardwareProfile) -> None:
    """upstream_config의 ASR/TTS 설정을 하드웨어 프로파일에 맞게 오버라이드한다.

    conf.yaml 값보다 우선 적용. SAESSAGI_NO_HW_ADAPT=1 환경변수로 비활성화.
    """
    import os

    if os.environ.get("SAESSAGI_NO_HW_ADAPT", "").strip() == "1":
        logger.info("SAESSAGI_NO_HW_ADAPT=1 — 하드웨어 자동 설정 비활성화")
        return

    try:
        asr = upstream_config.character_config.asr_config.faster_whisper
        # conf.yaml에 로컬 경로(슬래시 포함)가 있으면 model_path를 건드리지 않는다.
        existing = asr.model_path or ""
        if existing and ("/" in existing or "\\" in existing):
            logger.info(f"HW adapt ASR: model_path는 로컬 경로이므로 유지 ({existing[:60]}…)")
        else:
            asr.model_path = hw.whisper_model
            logger.info(f"HW adapt ASR: model_path={hw.whisper_model}")
        asr.device = hw.whisper_device
        asr.compute_type = hw.whisper_compute_type
        logger.info(
            f"HW adapt ASR: device={hw.whisper_device} compute_type={hw.whisper_compute_type}"
        )
    except AttributeError as exc:
        logger.warning(f"HW adapt ASR 오버라이드 실패: {exc}")

    try:
        tts = upstream_config.character_config.tts_config.melo_tts
        tts.device = hw.tts_device
        logger.info(f"HW adapt TTS: device={hw.tts_device}")
    except AttributeError as exc:
        logger.warning(f"HW adapt TTS 오버라이드 실패: {exc}")


def apply_to_app_config(app_config: Any, hw: HardwareProfile) -> None:
    """app_config의 RAG 임베딩 배치 크기를 하드웨어 프로파일에 맞게 오버라이드한다.

    conf.yaml 값보다 우선 적용(ASR/TTS adapt와 동일 정책).
    SAESSAGI_NO_HW_ADAPT=1 환경변수로 비활성화.
    """
    import os

    if os.environ.get("SAESSAGI_NO_HW_ADAPT", "").strip() == "1":
        logger.info("SAESSAGI_NO_HW_ADAPT=1 — RAG 하드웨어 자동 설정 비활성화")
        return

    try:
        app_config.rag_embed_batch_size = hw.embed_batch_size
        logger.info(f"HW adapt RAG: embed_batch_size={hw.embed_batch_size}")
    except (AttributeError, ValueError) as exc:
        logger.warning(f"HW adapt RAG 오버라이드 실패: {exc}")


def log_summary(hw: HardwareProfile) -> None:
    """감지된 하드웨어 정보를 로그로 출력한다."""
    gpu_info = (
        f"CUDA ({hw.cuda_device_name}, {hw.cuda_vram_gb:.1f}GB VRAM)"
        if hw.has_cuda
        else ("Apple MPS" if hw.has_mps else "없음")
    )
    logger.info(
        f"하드웨어 감지: OS={hw.platform} arch={hw.arch} RAM={hw.ram_gb:.1f}GB GPU={gpu_info}"
    )
    logger.info(
        f"권장 설정: Whisper={hw.whisper_model}/{hw.whisper_device}"
        f"/{hw.whisper_compute_type} TTS_device={hw.tts_device}"
        f" embed_batch={hw.embed_batch_size}"
    )

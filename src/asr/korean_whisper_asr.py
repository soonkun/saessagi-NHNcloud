# src/asr/korean_whisper_asr.py
"""KoreanWhisperASR — 한국어/영어 faster-whisper large-v3 int8 전용 STT."""

import logging
import threading
from pathlib import Path

import numpy as np
from open_llm_vtuber.asr.asr_interface import ASRInterface  # upstream

from .errors import ASRInitError, ASRRuntimeError

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES: frozenset[str | None] = frozenset({"ko", "en", None})
SUPPORTED_COMPUTE_TYPES: frozenset[str] = frozenset({"int8", "float16", "float32"})
SUPPORTED_DEVICES: frozenset[str] = frozenset({"auto", "cpu", "cuda"})


def _check_cuda_available() -> bool:
    """ctranslate2를 사용해 CUDA 가용성을 확인한다.

    ImportError: ctranslate2가 설치되지 않은 경우 → CUDA 없음으로 처리.
    AttributeError: ctranslate2 API 시그니처 변경 → CUDA 없음으로 처리.
    OSError: CUDA 드라이버/라이브러리 로드 실패 → CUDA 없음으로 처리.
    그 외 예상치 못한 예외는 잡지 않고 상위로 전파한다.
    """
    try:
        import ctranslate2

        providers = ctranslate2.get_supported_compute_types("cuda")
        return len(providers) > 0
    except (ImportError, AttributeError, OSError, ValueError):
        return False


class KoreanWhisperASR(ASRInterface):  # type: ignore[misc]
    """한국어·영어 faster-whisper large-v3 int8 전용 STT.

    upstream ASRInterface를 상속하므로 async_transcribe_np는 부모 기본 구현을 사용한다
    (asyncio.to_thread(self.transcribe_np, audio)).
    """

    model_path: str
    language: str | None
    compute_type: str
    device: str
    resolved_device: str  # "cuda" | "cpu" (auto 해석 결과)

    def __init__(
        self,
        model_path: str,
        language: str | None = "ko",
        compute_type: str = "int8",
        device: str = "auto",
        beam_size: int = 5,
        initial_prompt: str | None = None,
        min_audio_seconds: float = 0.2,
        download_root: str = "",
    ) -> None:
        """모델을 즉시 로드한다 (지연 로드 아님 — 첫 발화 지연 방지).

        Raises:
            ASRInitError:
              - language가 지원 세트 외
              - compute_type이 지원 세트 외
              - device가 지원 세트 외
              - download_root가 비어있지 않고 존재하지 않는 경우
              - model_path 디렉토리가 존재하지 않음
              - model_path 디렉토리에 model.bin 또는 config.json 없음
              - initial_prompt 길이가 200 초과
              - device="cuda" 요청 but CUDA 미가용
              - WhisperModel 생성자 예외
        """
        # 1. language 검증
        if language not in SUPPORTED_LANGUAGES:
            msg = f"unsupported language: {language}"
            logger.error(msg)
            raise ASRInitError(msg)

        # 2. compute_type 검증
        if compute_type not in SUPPORTED_COMPUTE_TYPES:
            msg = f"unsupported compute_type: {compute_type}"
            logger.error(msg)
            raise ASRInitError(msg)

        # 3. device 검증
        if device not in SUPPORTED_DEVICES:
            msg = f"unsupported device: {device}"
            logger.error(msg)
            raise ASRInitError(msg)

        # 4. download_root 검증 (비어있지 않고 존재하지 않으면 거부)
        if download_root and not Path(download_root).exists():
            msg = "download_root must be empty or an existing directory"
            logger.error(msg)
            raise ASRInitError(msg)

        # 5. model_path 디렉토리 존재 검증
        model_dir = Path(model_path)
        if not model_dir.exists():
            msg = f"model_path not found: {model_path}"
            logger.error(msg)
            raise ASRInitError(msg)

        # 6. model.bin 과 config.json 존재 검증
        if not (model_dir / "model.bin").exists() or not (model_dir / "config.json").exists():
            msg = f"model weights missing in: {model_path}"
            logger.error(msg)
            raise ASRInitError(msg)

        # 7. initial_prompt 길이 검증
        if initial_prompt is not None and len(initial_prompt) > 200:
            msg = "initial_prompt exceeds 200 characters"
            logger.error(msg)
            raise ASRInitError(msg)

        # 8 & 9. device 결정
        if device == "cuda":
            if not _check_cuda_available():
                msg = "cuda requested but not available"
                logger.error(msg)
                raise ASRInitError(msg)
            resolved_device = "cuda"
        elif device == "auto":
            if _check_cuda_available():
                resolved_device = "cuda"
                logger.info("CUDA 사용 가능. device=cuda로 설정.")
            else:
                resolved_device = "cpu"
                # INFO 레벨: device=auto에서 CPU로 폴백하는 것은 정상 동작이다.
                # WARNING을 발생시키지 않는다(스펙 §테스트 E-7 "WARNING 없음" 준수).
                logger.info("CUDA 사용 불가. device=cpu로 폴백 (정상 동작).")
        else:
            resolved_device = "cpu"

        # 10. WhisperModel 로드 (lazy import — 테스트 가능성 및 오프라인 강제)
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(
                model_path,
                device=resolved_device,
                compute_type=compute_type,
                download_root=download_root or None,
            )
        except Exception as exc:
            msg = str(exc)
            logger.error(f"WhisperModel 로드 실패: {msg}")
            raise ASRInitError(msg) from exc

        # 속성 저장
        self.model_path = model_path
        self.language = language
        self.compute_type = compute_type
        self.device = device
        self.resolved_device = resolved_device
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self.min_audio_seconds = min_audio_seconds
        # NOTE: threading.Lock (asyncio.Lock 아님).
        #
        # 스펙 §스펙 외 사항 10에는 "asyncio.Lock으로 직렬화"라고 기술되어 있으나,
        # 실제 실행 경로는 부모 async_transcribe_np → asyncio.to_thread(self.transcribe_np)
        # 이므로 transcribe_np는 이미 스레드풀 워커 스레드에서 실행된다.
        # asyncio.Lock은 이벤트 루프 스레드 전용이며, 스레드풀 워커에서 await 없이
        # 획득하면 데드락이 발생한다. 따라서 threading.Lock이 올바른 선택이다.
        # asyncio.to_thread가 기본 스레드풀(ThreadPoolExecutor)을 사용하므로
        # 동시에 여러 워커가 transcribe_np를 호출하는 상황에서 직렬화를 보장한다.
        self._lock = threading.Lock()

        logger.info(
            f"KoreanWhisperASR 초기화 완료: model_path={model_path}, "
            f"language={language}, compute_type={compute_type}, "
            f"resolved_device={resolved_device}"
        )

    def transcribe_np(self, audio: np.ndarray) -> str:
        """동기 전사. 부모의 async_transcribe_np가 스레드로 래핑해 호출한다.

        Args:
            audio: 1D float32 numpy 배열 (SAMPLE_RATE=16000 Hz).

        Returns:
            str: 전사 결과. 빈 문자열이면 발화 없음.

        Raises:
            ASRRuntimeError: 잘못된 오디오 형태 또는 백엔드 예외.
        """
        # 1. None 또는 ndim != 1 검증
        if audio is None or audio.ndim != 1:
            msg = "invalid audio shape"
            logger.error(msg)
            raise ASRRuntimeError(msg)

        # 2. float32 캐스트
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # 3. 0길이 또는 초단시간 오디오 조기 반환
        if len(audio) == 0 or len(audio) / self.SAMPLE_RATE < self.min_audio_seconds:
            logger.debug(f"오디오가 너무 짧습니다 ({len(audio)} samples). 빈 문자열 반환.")
            return ""

        # 4. NaN/Inf 체크 및 치환
        if not np.isfinite(audio).all():
            logger.warning("오디오에 NaN/Inf가 포함되어 있어 np.nan_to_num으로 치환합니다.")
            audio = np.nan_to_num(audio)

        # 5. lock 획득 후 transcribe 호출
        try:
            with self._lock:
                segments, info = self.model.transcribe(
                    audio,
                    beam_size=self.beam_size,
                    language=self.language,
                    condition_on_previous_text=False,
                    initial_prompt=self.initial_prompt,
                )

                # 세그먼트를 즉시 소비 (generator이므로 lock 내에서 평가)
                segment_texts = [seg.text for seg in segments]
        except Exception as exc:
            msg = str(exc)
            logger.error(f"transcribe 실패: {msg}")
            raise ASRRuntimeError(msg) from exc

        # 7. language 불일치 경고
        if self.language is not None and info.language != self.language:
            logger.warning(f"언어 불일치: 요청={self.language}, 감지={info.language}")

        # 8. 세그먼트 없음
        if not segment_texts:
            logger.debug("세그먼트가 없습니다. 빈 문자열 반환.")
            return ""

        # 9. 결과 조합
        return "".join(segment_texts).strip()

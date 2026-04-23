# src/app/config.py
"""AppConfig, FullConfig, load_full_config — 본 프로젝트 고유 설정 스키마."""

import os
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, Field, field_validator


if TYPE_CHECKING:
    pass  # upstream


# ---------------------------------------------------------------------------
# TTS 설정 스키마 (M_04 TTSEngine 연동)
# ---------------------------------------------------------------------------


class TtsEngineKind(str, Enum):
    """TTS 엔진 종류."""

    MELO = "melo"
    XTTS_V2 = "xtts_v2"


class MeloTtsSubConfig(BaseModel):
    """MeloTTS 서브 설정."""

    speaker: str = Field(default="KR")
    language: str = Field(default="KR")
    speaker_id: int | None = Field(default=None)
    sample_rate: int = Field(default=24000)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    device: str = Field(default="auto")
    model_dir: str | None = Field(default=None)


class XttsV2SubConfig(BaseModel):
    """XTTS v2 서브 설정."""

    speaker_wav: str | None = Field(default=None)
    language: str = Field(default="ko")
    device: str = Field(default="auto")
    model_dir: str | None = Field(default=None)


class TtsConfig(BaseModel):
    """TTS 설정 루트."""

    engine: TtsEngineKind = Field(default=TtsEngineKind.MELO)
    cache_dir: str = Field(default="cache")
    speaker_refs_dir: str = Field(default="data/speaker_refs")
    melo: MeloTtsSubConfig = Field(default_factory=MeloTtsSubConfig)
    xtts: XttsV2SubConfig = Field(default_factory=XttsV2SubConfig)


# ---------------------------------------------------------------------------


class HardwareProfile(str, Enum):
    """하드웨어 프로파일.

    min: Whisper medium, 최소 메모리 예산 (REQUIREMENTS.md §9)
    recommended: Whisper large-v3, 권장 메모리 예산
    """

    MIN = "min"
    RECOMMENDED = "recommended"


class OllamaConfig(BaseModel):
    base_url: str = Field(default="http://127.0.0.1:11434")
    model: str = Field(default="gemma4:e4b")
    keep_alive_seconds: int = Field(default=300)
    request_timeout_seconds: int = Field(default=120)


class PathsConfig(BaseModel):
    data_dir: str = Field(default="data")
    assets_dir: str = Field(default="assets")
    vector_store_dir: str = Field(default="data/vector_store")
    calendar_db_path: str = Field(default="data/calendar.db")
    chat_history_dir: str = Field(default="data/chat_history")
    log_dir: str = Field(default="data/logs")
    asr_model_path: str | None = Field(
        default=None,
        description=(
            "ASR 모델 디렉토리 절대경로. 설정하면 profile 기본 경로보다 우선한다. "
            "None이면 AppConfig.profile에 따라 자동 결정."
        ),
    )


class AgentConfig(BaseModel):
    """M_05 LLMAgent 설정 서브스키마."""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_context_tokens: int = Field(default=131_000, ge=1024, le=1_048_576)
    faster_first_response: bool = Field(default=True)
    interrupt_method: Literal["system", "user"] = Field(default="user")
    use_mcpp: bool = Field(default=True)


class ProactiveConfig(BaseModel):
    """프로액티브 알림 관련 설정 (M_10 IdleMonitor + M_11 ProactiveDispatcher 공용).

    스펙 M_10 §13.1 / §16.1 — conf.yaml proactive 섹션 3개 필드.
    """

    idle_threshold_min: int = Field(
        default=45,
        ge=1,
        le=1440,
        description="유휴 판정 임계값(분). 마지막 입력 이후 이 분수 이상 무입력 시 idle_rest 방출.",
    )
    overwork_threshold_min: int = Field(
        default=120,
        ge=1,
        le=1440,
        description="연속 활동 임계값(분). active_gap_seconds 이내 연속 입력이 이 분수 이상 지속 시 overwork 방출.",
    )
    active_gap_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="연속 활동 판정 간격(초). 마지막 입력 이후 이 초 이하이면 '계속 활동 중'으로 간주.",
    )
    cooldown_min: int = Field(
        default=30,
        ge=1,
        le=1440,
        description="M_11 ProactiveDispatcher 쿨다운(분). 동일 이벤트 재발행 억제.",
    )


class AppConfig(BaseModel):
    """본 프로젝트 고유 설정.

    # MIN 프로파일: Whisper medium int8 (~12.6 GB RSS), REQUIREMENTS.md §9 14GB 이하
    # RECOMMENDED 프로파일: Whisper large-v3 int8 (~13.6 GB RSS), REQUIREMENTS.md §9 20GB 이하
    """

    profile: HardwareProfile = Field(default=HardwareProfile.MIN)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    morning_briefing_time: str = Field(default="09:00")
    dnd_enabled: bool = Field(default=False)
    rag_min_score: float = Field(default=0.35, ge=0.0, le=1.0)
    screenshot_continuous_interval_sec: int = Field(default=5, ge=1, le=60)
    meeting_download_base_url: str = Field(
        default="http://127.0.0.1:12393",
        description="회의록 다운로드 URL 기본 주소. loopback 또는 사설 IP만 허용.",
    )
    meeting_temp_dir: str = Field(
        default="data/temp/meeting_minutes",
        description="회의록 임시 파일 저장 디렉토리.",
    )
    meeting_template_path: str = Field(
        default="data/Template/회의 결과보고 템플릿.hwpx",
        description="HWPX 회의록 템플릿 파일 경로.",
    )

    @field_validator("morning_briefing_time", mode="before")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        """HH:MM 포맷 검증 및 정규화.

        "9:5" 같은 zero-padding 없는 입력은 "09:05"로 정규화.
        "25:00" 같은 범위 초과는 ValidationError.
        """
        if not isinstance(v, str):
            raise ValueError(f"morning_briefing_time은 문자열이어야 합니다: {v!r}")

        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"HH:MM 형식이어야 합니다: {v!r}")

        try:
            hh = int(parts[0])
            mm = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"HH:MM 형식이어야 합니다: {v!r}") from exc

        if not (0 <= hh <= 23):
            raise ValueError(f"시간은 0~23 범위여야 합니다: {hh}")
        if not (0 <= mm <= 59):
            raise ValueError(f"분은 0~59 범위여야 합니다: {mm}")

        return f"{hh:02d}:{mm:02d}"


class FullConfig(BaseModel):
    """upstream Config + 본 프로젝트 AppConfig 병합 객체."""

    model_config = {"arbitrary_types_allowed": True}

    upstream: Any  # upstream Config 타입 (타입 힌트는 TYPE_CHECKING에서만 사용)
    app: AppConfig


def load_full_config(config_path: str) -> FullConfig:
    """YAML을 읽어 upstream validate_config로 검증 후 FullConfig 반환.

    환경변수 OLLAMA_BASE_URL이 설정되어 있으면 app.ollama.base_url과
    upstream character_config.agent_config.llm_configs.ollama_llm.base_url 모두 오버라이드.

    Raises:
        FileNotFoundError: config_path 부재
        PrivacyViolationError: OLLAMA_BASE_URL이 화이트리스트에 없음
        pydantic.ValidationError: 스키마 위반
    """
    from open_llm_vtuber.config_manager.utils import read_yaml, validate_config

    logger.info(f"설정 파일 로딩: {config_path}")

    raw: dict[str, Any] = read_yaml(config_path)  # FileNotFoundError 전파

    # upstream 섹션 검증
    upstream_data = {k: v for k, v in raw.items() if k != "app"}
    upstream_config = validate_config(upstream_data)  # ValidationError 전파

    # 본 프로젝트 app 섹션 (없으면 기본값 사용)
    app_data: dict[str, Any] = raw.get("app", {})
    app_config = AppConfig(**app_data)  # ValidationError 전파

    # 환경변수 오버라이드 (우선순위: env > yaml)
    env_ollama_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if env_ollama_url:
        logger.info(f"OLLAMA_BASE_URL 환경변수 오버라이드: {env_ollama_url}")
        app_config = app_config.model_copy(
            update={"ollama": app_config.ollama.model_copy(update={"base_url": env_ollama_url})}
        )
        # upstream llm_configs.ollama_llm.base_url 오버라이드
        _override_upstream_ollama_url(upstream_config, env_ollama_url)

    env_profile = os.environ.get("SAESSAGI_PROFILE", "").strip()
    if env_profile:
        app_config = app_config.model_copy(update={"profile": HardwareProfile(env_profile)})

    return FullConfig(upstream=upstream_config, app=app_config)


def _override_upstream_ollama_url(upstream_config: Any, url: str) -> None:
    """upstream Config의 ollama_llm.base_url을 직접 패치 (참조 업데이트)."""
    try:
        agent_cfg = upstream_config.character_config.agent_config
        llm_cfgs = agent_cfg.llm_configs
        if hasattr(llm_cfgs, "ollama_llm") and llm_cfgs.ollama_llm is not None:
            llm_cfgs.ollama_llm.base_url = url
            logger.debug(f"upstream ollama_llm.base_url 오버라이드: {url}")
    except AttributeError as exc:
        logger.warning(f"upstream ollama_llm.base_url 오버라이드 실패: {exc}")

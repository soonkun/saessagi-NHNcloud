# src/app/config.py
"""AppConfig, FullConfig, load_full_config — 본 프로젝트 고유 설정 스키마."""

import os
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator


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

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> "TtsConfig":
        """SAESSAGI_ROOT 기준으로 상대경로를 절대경로로 변환 (PathsConfig와 동일 정책).

        런처가 백엔드를 다른 cwd에서 실행하면 'assets/models/melotts-ko' 같은
        상대경로가 빗나가 로컬 모델을 못 찾고 자동 다운로드 경로로 빠진다.
        """
        root = os.environ.get("SAESSAGI_ROOT")
        if not root:
            return self
        base = Path(root)
        if self.cache_dir and not Path(self.cache_dir).is_absolute():
            self.cache_dir = str(base / self.cache_dir)
        if self.speaker_refs_dir and not Path(self.speaker_refs_dir).is_absolute():
            self.speaker_refs_dir = str(base / self.speaker_refs_dir)
        if self.melo.model_dir and not Path(self.melo.model_dir).is_absolute():
            self.melo.model_dir = str(base / self.melo.model_dir)
        if self.xtts.model_dir and not Path(self.xtts.model_dir).is_absolute():
            self.xtts.model_dir = str(base / self.xtts.model_dir)
        return self


# ---------------------------------------------------------------------------


class HardwareProfile(str, Enum):
    """하드웨어 프로파일.

    min: Whisper medium, 최소 메모리 예산 (REQUIREMENTS.md §9)
    recommended: Whisper large-v3, 권장 메모리 예산
    """

    MIN = "min"
    RECOMMENDED = "recommended"


class LlmProviderKind(str, Enum):
    """LLM 공급자 선택."""

    OLLAMA = "ollama"
    OPENAI = "openai"


class OllamaConfig(BaseModel):
    base_url: str = Field(default="http://127.0.0.1:11434")
    model: str = Field(default="gemma4:e4b")
    # 이미지(스크린샷) 첨부 턴 전용 비전 모델. 비우면 라우팅 안 함(메인 모델 사용).
    # 메인 모델(gemma4 등)이 이미지 OCR을 못 할 때 비전 전용 모델로 분기한다.
    vision_model: str = Field(default="")
    keep_alive_seconds: int = Field(default=300)
    request_timeout_seconds: int = Field(default=120)


class OpenAISubConfig(BaseModel):
    """OpenAI API 설정 (테스트용 외부 API)."""

    api_key: str = Field(default="")
    model: str = Field(default="gpt-4o-mini")


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

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> "PathsConfig":
        """SAESSAGI_ROOT 환경변수가 있으면 상대경로를 절대경로로 변환.

        upstream 디렉토리에서 서버를 실행할 때 data/assets 경로가
        프로젝트 루트를 기준으로 해석되도록 한다.
        """
        root = os.environ.get("SAESSAGI_ROOT")
        if not root:
            return self
        base = Path(root)
        for field_name in (
            "data_dir",
            "assets_dir",
            "vector_store_dir",
            "calendar_db_path",
            "chat_history_dir",
            "log_dir",
        ):
            val = getattr(self, field_name)
            if val and not Path(val).is_absolute():
                object.__setattr__(self, field_name, str(base / val))
        return self


class AgentConfig(BaseModel):
    """M_05 LLMAgent 설정 서브스키마."""

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_context_tokens: int = Field(default=131_000, ge=1024, le=1_048_576)
    faster_first_response: bool = Field(default=True)
    interrupt_method: Literal["system", "user"] = Field(default="user")
    use_mcpp: bool = Field(default=True)


class IntentGateProviderKind(str, Enum):
    """의도 분류기 공급자 선택 (M_16)."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    SAME_AS_CHAT = "same_as_chat"


class IntentGateConfig(BaseModel):
    """M_16 IntentGate 설정 서브스키마."""

    enabled: bool = Field(default=True)
    provider: IntentGateProviderKind = Field(default=IntentGateProviderKind.SAME_AS_CHAT)
    ollama_model: str = Field(default="gemma4:e4b")
    openai_model: str = Field(default="gpt-4o-mini")
    confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)


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


class AgentPromptsConfig(BaseModel):
    """M_17 AgentInstructions — 에이전트별 지침 커스텀 값 저장.

    빈 문자열 = 기본값(코드 상수) 사용. persona 빈값 저장은 API 레이어에서 422 차단.
    """

    persona: str = Field(
        default="",
        description="대화 페르소나 교체본. 빈값이면 character_config.persona_prompt 사용.",
    )
    knowledge_note: str = Field(
        default="", description="업무노트 작성 지침. 빈값이면 기본값 사용(미주입)."
    )
    doc_query_answer: str = Field(
        default="", description="자료질의 답변 지침. 빈값이면 기본값 사용(미주입)."
    )
    work_query_answer: str = Field(
        default="", description="업무질의 답변 지침. 빈값이면 기본값 사용(미주입)."
    )
    intent_classify: str = Field(
        default="", description="의도 분류 기준 SYSTEM 텍스트. 빈값이면 SYSTEM_PROMPT 사용."
    )
    meeting_minutes: str = Field(
        default="", description="회의록 작성 지침. 빈값이면 SYSTEM_PROMPT 사용."
    )


class AppConfig(BaseModel):
    """본 프로젝트 고유 설정.

    # MIN 프로파일: Whisper medium int8 (~12.6 GB RSS), REQUIREMENTS.md §9 14GB 이하
    # RECOMMENDED 프로파일: Whisper large-v3 int8 (~13.6 GB RSS), REQUIREMENTS.md §9 20GB 이하
    """

    profile: HardwareProfile = Field(default=HardwareProfile.MIN)
    llm_provider: LlmProviderKind = Field(default=LlmProviderKind.OLLAMA)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    openai: OpenAISubConfig = Field(default_factory=OpenAISubConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    intent_gate: IntentGateConfig = Field(default_factory=IntentGateConfig)  # M_16
    morning_briefing_time: str = Field(default="09:00")
    dnd_enabled: bool = Field(default=False)
    rag_min_score: float = Field(default=0.35, ge=0.0, le=1.0)
    rag_device: str = Field(
        default="auto",
        description=(
            "BGE-M3 임베딩 디바이스. 'auto'=CUDA>MPS>CPU 자동 감지, 'cuda'/'mps'/'cpu' 강제. "
            "GPU 추론 실패 시 런타임에 CPU로 자동 fallback."
        ),
    )
    rag_embed_batch_size: int = Field(
        default=32,
        ge=1,
        le=256,
        description=(
            "BGE-M3 임베딩 배치 크기. hardware.py가 시작 시 VRAM 기준으로 자동 조정한다 "
            "(SAESSAGI_NO_HW_ADAPT=1로 비활성화 가능)."
        ),
    )
    rag_chunk_chars: int = Field(
        default=800,
        ge=100,
        le=4000,
        description=(
            "문서 업로드 청크 크기(문자). 검색 품질 기준으로 정하는 값이며 하드웨어와 무관. "
            "한국어 업무 문서 기준 500~1000 권장."
        ),
    )
    rag_chunk_overlap: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="청크 간 오버랩 크기(문자). rag_chunk_chars보다 작아야 한다.",
    )
    rag_rerank_enabled: bool = Field(
        default=True,
        description=(
            "M_18 cross-encoder 리랭커 사용 여부. 모델(assets/models/bge-reranker-v2-m3) "
            "미배치 시 자동으로 꺼진다."
        ),
    )
    rag_rerank_candidates: int = Field(
        default=30,
        ge=8,
        le=100,
        description="리랭커/하이브리드 융합에 넣을 벡터 검색 후보 수.",
    )
    rag_hybrid_enabled: bool = Field(
        default=True,
        description="M_18 하이브리드 검색(FTS BM25 + RRF 융합) 사용 여부.",
    )
    tts_brief_enabled: bool = Field(
        default=True,
        description=(
            "긴 답변을 전부 음성으로 읽지 않고 '~ 완료되었어요. 내용을 확인해 주세요' "
            "완료 멘트만 말한다. 짧은 답변(tts_brief_max_chars 이하)은 그대로 읽음."
        ),
    )
    tts_brief_max_chars: int = Field(
        default=80,
        ge=20,
        le=500,
        description="이 길이(문자)를 넘는 답변은 완료 멘트로 대체. 기본 80자(약 8초 분량).",
    )

    @model_validator(mode="after")
    def _validate_rag_chunk(self) -> "AppConfig":
        if self.rag_chunk_overlap >= self.rag_chunk_chars:
            raise ValueError(
                f"rag_chunk_overlap({self.rag_chunk_overlap})은 "
                f"rag_chunk_chars({self.rag_chunk_chars})보다 작아야 합니다"
            )
        return self
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
    meeting_minutes_prompt: str = Field(
        default="",
        description=(
            "회의록 생성 시스템 프롬프트. 빈 문자열이면 기본값(prompts.py SYSTEM_PROMPT) 사용. "
            "[DEPRECATED: M_17] agent_prompts.meeting_minutes로 이전됨. 하위 호환용으로 유지."
        ),
    )
    # M_17: 에이전트별 지침 커스텀 값 (6키)
    agent_prompts: AgentPromptsConfig = Field(default_factory=AgentPromptsConfig)

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

    # M_17: meeting_minutes_prompt → agent_prompts.meeting_minutes 1회 마이그레이션
    _migrate_meeting_minutes_prompt(app_config)

    return FullConfig(upstream=upstream_config, app=app_config)


def _migrate_meeting_minutes_prompt(app_config: "AppConfig") -> None:
    """M_17: meeting_minutes_prompt → agent_prompts.meeting_minutes 1회 마이그레이션.

    기존 app.meeting_minutes_prompt(구 필드)가 있고
    app.agent_prompts.meeting_minutes가 비어 있으면 후자로 in-memory 복사.
    파일은 다음 저장 시 정규화됨.
    두 필드 모두 채워진 경우 agent_prompts.meeting_minutes 우선.
    """
    old_val = (app_config.meeting_minutes_prompt or "").strip()
    new_val = (app_config.agent_prompts.meeting_minutes or "").strip()

    if old_val and not new_val:
        # in-memory만 갱신
        app_config.agent_prompts.meeting_minutes = old_val
        logger.info(
            f"M_17 마이그레이션: meeting_minutes_prompt → agent_prompts.meeting_minutes "
            f"(길이={len(old_val)})"
        )


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

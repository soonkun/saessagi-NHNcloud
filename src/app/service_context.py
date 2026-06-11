# src/app/service_context.py
"""AppServiceContext — upstream ServiceContext를 상속해 본 프로젝트 서비스 필드를 추가."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket
from loguru import logger

from open_llm_vtuber.service_context import ServiceContext  # upstream

from avatar_state import AvatarState
from idle_monitor import IdleMonitor

if TYPE_CHECKING:
    from .config import AppConfig

    # 하위 모듈(M_06~M_11) — 미구현 상태. TYPE_CHECKING 블록에서만 import해 런타임 오류 방지.
    # 각 모듈 구현 완료 후 실제 타입으로 교체할 것.
    from vector_search import RagService
    from typing import Any as CalendarService
    from proactive import ProactiveDispatcher
    from tool_router import ScreenshotService, ToolRouter, ToolRouterAdapter
    from meeting_minutes import MeetingMinutesService


class AppServiceContext(ServiceContext):  # type: ignore[misc]
    """upstream ServiceContext 서브클래스.

    하위 모듈(M_06~M_11)이 미완성인 동안 모든 확장 필드는 None.
    각 필드는 해당 모듈 구현 완료 후 load_app_services에서 주입.
    """

    def __init__(self) -> None:
        super().__init__()
        # M_07 완료 후 주입
        self.rag_service: "RagService | None" = None
        # M_09 완료 후 주입
        self.calendar_service: "CalendarService | None" = None
        # M_10 완료 후 주입
        self.idle_monitor: IdleMonitor | None = None
        # M_08 완료 후 주입
        self.avatar_state: AvatarState | None = None
        # M_11 완료 후 주입
        self.proactive_dispatcher: "ProactiveDispatcher | None" = None
        # M_05b 완료 후 주입 (CR-05에서 타입 확정)
        self.screenshot_service: "ScreenshotService | None" = None
        # CR-05: ToolRouter/Adapter 필드 신설
        self.tool_router: "ToolRouter | None" = None
        self.tool_router_adapter: "ToolRouterAdapter | None" = None
        # M_13: MeetingMinutesService 슬롯 + 임시파일 정리 스케줄러
        self.meeting_minutes_service: "MeetingMinutesService | None" = None
        self._temp_cleanup_scheduler: Any = None  # apscheduler.AsyncIOScheduler | None
        # M_15: KnowledgeService 슬롯 (Phase 1 후 추가)
        self.knowledge_service: Any = None  # KnowledgeService | None
        # M_16: IntentClassifier 슬롯
        self.intent_classifier: Any = None  # IntentClassifier | None
        self._intent_classifier_agent: Any = None  # 분류 전용 GemmaChatAgent | None (cleanup용)
        # load_full_config 후 주입
        self.app_config: "AppConfig | None" = None
        # D-13: 마지막으로 연결된 활성 WebSocket (단일 사용자 전제)
        self._active_ws: WebSocket | None = None

    def _get_active_client_send_text(
        self,
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """D-13: 가장 최근 연결된 활성 WebSocket에 JSON 페이로드를 송신하는 async 콜러블 반환.

        활성 연결이 없으면 조용히 skip (logger.debug).
        send_text 예외는 ProactiveDispatcher의 D-7 정책으로 삼켜진다.
        """

        async def _active_client_send_text(payload: dict[str, Any]) -> None:
            ws = self._active_ws
            if ws is None:
                logger.debug("no active client, proactive message dropped")
                return
            await ws.send_text(json.dumps(payload))

        return _active_client_send_text

    def init_asr(self, asr_config: Any) -> None:
        """upstream init_asr 오버라이드 — 모델 미배치 시 WARNING 후 계속."""
        # CWD가 upstream 디렉토리일 때 상대 경로를 SAESSAGI_ROOT 기준으로 보정
        root = os.environ.get("SAESSAGI_ROOT")
        if root:
            try:
                fw = asr_config.faster_whisper
                if fw is not None:
                    if fw.model_path and not Path(fw.model_path).is_absolute():
                        fw.model_path = str(Path(root) / fw.model_path)
                        logger.debug(f"ASR model_path resolved: {fw.model_path}")
                    if (
                        hasattr(fw, "download_root")
                        and fw.download_root
                        and not Path(fw.download_root).is_absolute()
                    ):
                        fw.download_root = str(Path(root) / fw.download_root)
            except AttributeError:
                pass
        try:
            super().init_asr(asr_config)
        except Exception as exc:
            logger.warning(
                f"ASR 초기화 실패 (모델 미배치 등): {exc!r}. 음성 입력 없이 텍스트 채팅만 동작합니다."
            )
            self.asr_engine = None  # type: ignore[assignment]

    def init_vad(self, vad_config: Any) -> None:  # spec: §N-4, §E-1
        """upstream init_vad 오버라이드.

        추가 동작 (실제 재초기화가 필요한 경우에만):
        - vad_model=None: WARNING 로그 1건 (spec §N-4, §에러 처리)
        - target_sr != 16000: WARNING 로그 1건 (spec §E-1)

        upstream short-circuit (동일 config 재호출) 시에는 WARNING을 발생시키지 않는다.
        """
        should_init = not self.vad_engine or self.character_config.vad_config != vad_config
        if should_init:
            if vad_config.vad_model is None:
                logger.warning(
                    "VAD is disabled (vad_model=null). "
                    "Speech detection will not function. "
                    "This is only acceptable in development/debug mode."
                )
            elif (
                vad_config.silero_vad is not None
                and getattr(vad_config.silero_vad, "target_sr", 16000) != 16000
            ):
                logger.warning(
                    f"VAD target_sr={vad_config.silero_vad.target_sr} is not 16000. "
                    "Only 16000 Hz has been validated for this project. "
                    "Proceeding, but results may be unreliable."
                )
        super().init_vad(vad_config)

    def init_tts(self, tts_config: Any) -> None:  # spec: M_04 §배선 정책
        """upstream init_tts 오버라이드 — 모델 미배치/패키지 미설치 시 WARNING 후 계속.

        app_config가 있으면 build_tts_engine으로 초기화. 없으면(startup 순서상
        load_from_config→load_app_services 순) upstream 경로를 시도하되 실패 시 graceful.
        """
        should_init = not self.tts_engine or self.character_config.tts_config != tts_config  # type: ignore[has-type]
        if not should_init:
            return
        if self.app_config is not None:
            from tts.builder import build_tts_engine
            from tts.errors import TTSInitError

            try:
                self.tts_engine = build_tts_engine(self.app_config)
                logger.info("TTS engine initialized via build_tts_engine")
            except TTSInitError as exc:
                logger.warning(
                    f"TTSInitError: TTS engine init failed: {exc!r}. "
                    "Text chat will continue without TTS."
                )
                self.tts_engine = None  # type: ignore[assignment]
            self.character_config.tts_config = tts_config
        else:
            try:
                super().init_tts(tts_config)
            except Exception as exc:
                logger.warning(
                    f"TTS 초기화 실패 (패키지/모델 미설치): {exc!r}. "
                    "Text chat will continue without TTS."
                )
                self.tts_engine = None  # type: ignore[assignment]

    async def construct_system_prompt(self, persona_prompt: str) -> str:
        """live2d_model이 None일 때 emo_str 참조를 건너뜀. 현재 날짜·시각을 주입."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        _KST = ZoneInfo("Asia/Seoul")
        now = datetime.now(_KST)
        ko_days = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        date_block = (
            f"\n\n## 현재 날짜·시각\n"
            f"{now.year}년 {now.month}월 {now.day}일 {ko_days[now.weekday()]} "
            f"{now.strftime('%H:%M')} (KST)\n"
            f"(오늘 기준으로 '내일', '다음 주 수요일' 등 상대적 날짜를 정확히 계산해서 사용해.)"
        )

        # M_15 Phase 2: 업무 노트 자동 저장 가이드 + 도구 우선순위
        notes_block = (
            "\n\n## 도구 선택 우선순위 (반드시 준수)\n"
            "사용자 입력이 들어오면 다음 순서로 도구 사용 여부를 결정하세요:\n\n"
            "1. **save_knowledge_note (업무 노트)** — 가장 자주 호출되는 도구.\n"
            "   사용자가 자기가 처리한 일·사례·절차·노하우를 보고하는 흐름이면 "
            "**거의 항상** 이 도구를 호출하세요. 패턴 예:\n"
            "   - '오늘 ~~ 처리했어/했어/완료했어/해결했어' (과거 시제 업무 보고)\n"
            "   - '이렇게 진행했어' '이런 절차로 했어'\n"
            "   - 사용자 메시지에 `[첨부 자료: 파일명 (doc_id: xxx); ...]` 메타가 포함된 경우\n"
            "   - 명시 요청: '저장해/기록해/노트로/메모해'\n\n"
            "2. **create_meeting_minutes (회의록)** — 호출 조건이 매우 좁음. 다음을 **모두 충족**해야:\n"
            "   - 사용자가 명시적으로 '회의록 만들어줘' '회의결과보고서 작성해줘'라고 요청\n"
            "   - 회의 녹취록(STT 출력) 텍스트가 함께 제공됨\n"
            "   사용자가 자연어로 회의를 보고했다고 해서 이 도구를 호출하면 안 됩니다. "
            "이 경우 save_knowledge_note를 호출하세요.\n\n"
            "3. **search_docs (문서 검색)** — 사용자가 질문 '~~ 어떻게 해?', '~~ 뭐야?'를 한 경우. "
            "또는 save_knowledge_note 호출 전 관련 doc_id 확보용.\n\n"
            "## save_knowledge_note 호출 절차\n"
            "1) 사용자 메시지에 `[첨부 자료: 파일명 (doc_id: xxx); ...]` prefix가 있으면 그 doc_id들을 "
            "그대로 `related_docs`에 사용. 추가로 필요하면 search_docs 호출.\n"
            "2) **첨부 파일 본문은 자동으로 [관련 문서 검색 결과] 블록에 포함되어 있습니다.** "
            "사용자가 본문 내용을 말로 다 설명하지 않아도 그 청크에서 절차/날짜/담당자/주요 결정사항/"
            "핵심 결론 등을 직접 추출하세요. summary는 사용자 발언 + 첨부 본문 추출 정보를 함께 정리. "
            '절대 사용자 한 줄 발언만으로 빈약한 노트("문서를 작성했다" 같은 한 줄)를 만들지 마세요. '
            "첨부 본문에 실제 내용이 있으면 그것을 노트의 핵심으로 옮겨담아야 합니다.\n"
            "3) `summary`는 한국어 markdown — 가능하면 `## 상황 / ## 절차 / ## 사용 자료 / ## 교훈` "
            "섹션으로 구조화. 사용자 말을 그대로 옮기지 말고 핵심을 정리.\n"
            "4) `tags`는 1~3개의 분류 단어 (예: ['회계','출장']).\n\n"
            "## save_knowledge_note 호출 후 응답\n"
            '사용자에게 자연어로 "방금 ⟨제목⟩ 노트로 저장해 두었어요"라고 한국어로 알리고, '
            "답변 끝에 ToolResult payload의 `note_marker` (예: `[[note:생성된-슬러그]]`)를 "
            "반드시 정확히 한 번 포함하세요. 사용자에게는 이것이 노트로 점프하는 칩으로 보입니다.\n"
            "ToolResult payload에 `related_docs_info`가 있으면 그 안의 각 `filename`을 "
            '답변에 그대로 한 번씩 자연어로 언급하세요 — 예: "관련 자료는 ⟨회의보고서.hwpx⟩에 있어요." '
            "프론트가 파일명을 인식해 다운로드 칩으로 렌더하므로, 사용자가 그 자리에서 바로 파일을 받을 수 있습니다.\n\n"
            "## 검색 결과(search_docs)에 노트가 등장한 경우\n"
            "각 hit의 `is_note=true` 이면 사용자가 저장한 업무 노트입니다. "
            "그 hit에 `note_related_docs`가 있으면 그 안의 각 `filename`도 답변에 그대로 한 번 언급하세요. "
            "그러면 사용자가 노트 칩 + 자료 다운로드 칩을 함께 받게 됩니다.\n\n"
            "## RAG 인용 마커 (정확한 다운로드 칩 자동 렌더)\n"
            "답변에 참고한 자료는 **마커**로 정확히 표시하세요:\n"
            "- 일반 문서를 인용했으면 답변 어딘가에 `[[doc:<doc_id>]]`를 정확히 한 번 포함.\n"
            "  doc_id는 search_docs hit의 `doc_id` 값 또는 [관련 문서 검색 결과] 블록의 doc_id를 그대로.\n"
            "- 업무 노트를 인용했으면 `[[note:<slug>]]` 사용.\n"
            "마커는 본문에 보이지 않고 사용자에겐 다운로드/점프 칩으로 렌더됩니다. "
            "여러 자료를 인용했으면 각 자료마다 마커를 한 번씩 포함하세요. "
            "마커 없이는 사용자가 자료에 접근할 수 없으므로 반드시 포함하세요.\n"
        )

        if self.live2d_model is None:
            return persona_prompt + date_block + notes_block
        result = await super().construct_system_prompt(persona_prompt)
        return result + date_block + notes_block

    async def load_from_config(self, config: Any) -> None:
        """upstream Config를 받아 부모 load_from_config 호출.

        init_agent 오버라이드가 _init_mcp_components 직후에 디스패치되므로
        build_chat_agent/CompositeToolExecutor 배선은 init_agent에서 완결됨 (CR-03).
        """
        await super().load_from_config(config)

    async def init_agent(self, agent_config: Any, persona_prompt: str) -> None:  # type: ignore[override]
        """upstream init_agent 오버라이드 (CR-03).

        AgentFactory.create_agent를 회피하고 build_chat_agent +
        BasicMemoryAgentAdapter + CompositeToolExecutor를 배선한다.
        M_04 init_tts와 동일 패턴.

        Args:
            agent_config: upstream character_config.agent_config (Any로 받아 순환 import 방지).
            persona_prompt: upstream character_config.persona_prompt.

        Raises:
            AgentInitError: self.app_config is None 또는 build_chat_agent 초기화 실패.
            AgentBackendError: Ollama 헬스체크 3회 모두 실패 (전파, 폴백 금지).
        """
        # (1) idempotency 가드
        if (
            self.agent_engine is not None
            and agent_config == self.character_config.agent_config
            and persona_prompt == self.character_config.persona_prompt
        ):
            logger.info("AppServiceContext.init_agent: 동일 config — 재초기화 건너뜀")
            return

        # (2) app_config 검증
        from agent.errors import AgentInitError

        if self.app_config is None:
            raise AgentInitError(
                "AppServiceContext.init_agent: app_config is None. "
                "load_app_services를 load_from_config 이전에 호출하십시오."
            )

        # (3) system_prompt 구성
        logger.info("AppServiceContext.init_agent: system_prompt 구성 중")
        system_prompt = await self.construct_system_prompt(persona_prompt)

        # (4) MCP tool_manager / tool_executor 확보 (_init_mcp_components 결과)
        mcp_tool_manager = self.tool_manager
        mcp_tool_executor = self.tool_executor

        # (5) ToolRouterAdapter가 있으면 CompositeToolExecutor 생성
        if self.tool_router_adapter is not None:
            composite = self.tool_router_adapter.as_upstream_tool_executor(
                fallback=mcp_tool_executor
            )
            self.tool_executor = composite
            extra_specs = self.tool_router.tool_specs()  # type: ignore[union-attr]
            logger.info(
                "AppServiceContext.init_agent: CompositeToolExecutor 생성 완료, "
                f"extra_specs={len(extra_specs)}건"
            )
        else:
            extra_specs = None
            logger.info(
                "AppServiceContext.init_agent: tool_router_adapter=None — "
                "degraded 모드, extra_specs=None"
            )

        # (6) GemmaChatAgent 빌드 (예외는 전파, 폴백 금지)
        from agent.builder import build_chat_agent
        from agent.upstream_adapter import BasicMemoryAgentAdapter

        logger.info("AppServiceContext.init_agent: build_chat_agent 호출")
        gemma_agent = await build_chat_agent(
            app_config=self.app_config,
            ollama_config=self.app_config.ollama,
            tool_manager=mcp_tool_manager,
            tool_executor=self.tool_executor,
            system_prompt=system_prompt,
            extra_tool_specs=extra_specs,
            tts_preprocessor_config=self.character_config.tts_preprocessor_config
            if self.character_config is not None
            else None,
        )

        # (7) M_16: IntentClassifier 조립
        from app.config import IntentGateProviderKind

        intent_cfg = self.app_config.intent_gate
        if not intent_cfg.enabled:
            self.intent_classifier = None
            logger.info("AppServiceContext.init_agent: IntentGate 비활성 (enabled=False)")
        else:
            try:
                from intent_gate import IntentClassifier

                # M_17: intent_classify 커스텀 프롬프트 조회 (빈값이면 None — 기본값 사용)
                _intent_prompt_override: str | None = None
                try:
                    _ic_custom = self.app_config.agent_prompts.intent_classify
                    if _ic_custom and _ic_custom.strip():
                        _intent_prompt_override = _ic_custom
                except Exception as _ic_exc:
                    logger.debug(f"intent_classify override 조회 실패 (무시): {_ic_exc!r}")

                if intent_cfg.provider == IntentGateProviderKind.SAME_AS_CHAT:
                    # 메인 대화 에이전트의 complete_json을 재사용
                    self.intent_classifier = IntentClassifier(
                        complete_json=gemma_agent.complete_json,
                        model_label=self.app_config.ollama.model,
                        confidence_threshold=intent_cfg.confidence_threshold,
                        timeout_seconds=intent_cfg.timeout_seconds,
                        system_prompt_override=_intent_prompt_override,  # M_17
                    )
                    logger.info(
                        "AppServiceContext.init_agent: IntentClassifier 배선 완료 "
                        f"(provider=same_as_chat, model={self.app_config.ollama.model}, "
                        f"custom_prompt={'yes' if _intent_prompt_override else 'no'})"
                    )
                elif intent_cfg.provider == IntentGateProviderKind.OLLAMA:
                    from agent.builder import build_chat_agent

                    # 분류 전용 경량 에이전트 생성
                    classify_agent = await build_chat_agent(
                        app_config=self.app_config,
                        ollama_config=self.app_config.ollama.model_copy(
                            update={"model": intent_cfg.ollama_model}
                        ),
                        tool_manager=None,
                        tool_executor=None,
                        system_prompt="",
                        extra_tool_specs=None,
                        tts_preprocessor_config=None,
                    )
                    self._intent_classifier_agent = classify_agent
                    self.intent_classifier = IntentClassifier(
                        complete_json=classify_agent.complete_json,
                        model_label=intent_cfg.ollama_model,
                        confidence_threshold=intent_cfg.confidence_threshold,
                        timeout_seconds=intent_cfg.timeout_seconds,
                        system_prompt_override=_intent_prompt_override,  # M_17
                    )
                    logger.info(
                        "AppServiceContext.init_agent: IntentClassifier 배선 완료 "
                        f"(provider=ollama, model={intent_cfg.ollama_model}, "
                        f"custom_prompt={'yes' if _intent_prompt_override else 'no'})"
                    )
                elif intent_cfg.provider == IntentGateProviderKind.OPENAI:
                    from agent.builder import build_chat_agent
                    from app.config import LlmProviderKind

                    openai_app_config = self.app_config.model_copy(
                        update={
                            "llm_provider": LlmProviderKind.OPENAI,
                            "openai": self.app_config.openai.model_copy(
                                update={"model": intent_cfg.openai_model}
                            ),
                        }
                    )
                    classify_agent = await build_chat_agent(
                        app_config=openai_app_config,
                        ollama_config=self.app_config.ollama,
                        tool_manager=None,
                        tool_executor=None,
                        system_prompt="",
                        extra_tool_specs=None,
                        tts_preprocessor_config=None,
                    )
                    self._intent_classifier_agent = classify_agent
                    self.intent_classifier = IntentClassifier(
                        complete_json=classify_agent.complete_json,
                        model_label=intent_cfg.openai_model,
                        confidence_threshold=intent_cfg.confidence_threshold,
                        timeout_seconds=intent_cfg.timeout_seconds,
                        system_prompt_override=_intent_prompt_override,  # M_17
                    )
                    logger.info(
                        "AppServiceContext.init_agent: IntentClassifier 배선 완료 "
                        f"(provider=openai, model={intent_cfg.openai_model}, "
                        f"custom_prompt={'yes' if _intent_prompt_override else 'no'})"
                    )
                else:
                    self.intent_classifier = None
                    logger.warning(
                        "AppServiceContext.init_agent: "
                        f"알 수 없는 intent_gate.provider={intent_cfg.provider} → 비활성"
                    )
            except Exception as exc:
                logger.warning(
                    f"AppServiceContext.init_agent: IntentClassifier 조립 실패 (비활성): {exc!r}"
                )
                self.intent_classifier = None

        # (7b) M_17: prompt_provider 클로저 배선 (lazy — 매 턴 최신 커스텀 지침 조회)
        # app_config 참조를 클로저로 캡처해, 저장 직후 다음 턴에 즉시 반영됨.
        # 중요: raw 커스텀 값만 반환 (빈 문자열이면 라우팅에서 None 정규화 → 미주입).
        # effective_prompt(기본값 폴백 포함)는 사용하지 않는다 — M_16 회귀 0 계약.
        _app_cfg_ref = self.app_config  # 클로저 캡처용 로컬 변수

        _prompt_provider = _make_prompt_provider(_app_cfg_ref)

        # (7c) 어댑터 래핑
        self.agent_engine = BasicMemoryAgentAdapter(
            gemma_agent,
            rag_service=self.rag_service,
            intent_classifier=self.intent_classifier,  # M_16
            prompt_provider=_prompt_provider,  # M_17
            tts_brief_enabled=self.app_config.tts_brief_enabled if self.app_config else True,
            tts_brief_max_chars=self.app_config.tts_brief_max_chars if self.app_config else 80,
        )
        logger.info("AppServiceContext.init_agent: BasicMemoryAgentAdapter 배선 완료")

        # (8) config 동기화
        self.character_config.agent_config = agent_config
        self.system_prompt = system_prompt

        # (9) MeetingMinutesService에 agent 주입 (load_app_services에서 None으로 초기화됨)
        if self.meeting_minutes_service is not None:
            self.meeting_minutes_service.set_agent(gemma_agent)
            logger.info("AppServiceContext.init_agent: MeetingMinutesService.set_agent 완료")

    async def load_app_services(self, app_config: "AppConfig") -> None:
        """본 프로젝트 고유 서비스(RAG/Calendar/Idle/Avatar/Proactive/Screenshot) 초기화.

        각 서비스의 생성자 호출만 수행. 실패해도 앱 기동은 계속 (로그 경고).
        CR-05: ScreenshotService → ToolRouter → ToolRouterAdapter 순으로 조립.
        """
        self.app_config = app_config
        logger.info("AppServiceContext.load_app_services: app_config 로드 완료")

        # M-09: CalendarService 초기화 (ToolRouter 조립 전)
        try:
            from calendar_service.service import CalendarService

            self.calendar_service = CalendarService(app_config.paths.calendar_db_path)
            logger.info("CalendarService 초기화 완료")
        except Exception as exc:
            logger.warning(f"calendar_service 초기화 실패: {exc}")
            self.calendar_service = None

        # M-08: AvatarState 배선 (스펙 §13.1 — load_app_services 내 1줄 추가)
        self.avatar_state = AvatarState(default="neutral")
        logger.info("AvatarState 초기화 완료 (default=neutral).")

        # CR-05: ScreenshotService 조립
        # send_text 콜백은 per-client이므로 여기서는 None (ws_handler가 직접 전달).
        from tool_router import (
            ScreenshotInitError,
            ScreenshotService,
            ToolRouter,
            ToolRouterAdapter,
        )

        try:
            self.screenshot_service = ScreenshotService(send_text=None)
            logger.info("ScreenshotService 초기화 완료")
        except ScreenshotInitError as exc:
            logger.warning(f"screenshot_service 초기화 실패(비-Windows 등): {exc}")
            self.screenshot_service = None

        # M_13: MeetingMinutesService 조립 (ToolRouter보다 먼저)
        try:
            from meeting_minutes import MeetingMinutesService
            from meeting_minutes.errors import HwpxTemplateError

            _root = os.environ.get("SAESSAGI_ROOT")
            _base = Path(_root) if _root else Path.cwd()

            meeting_template_path = Path(app_config.meeting_template_path)
            if not meeting_template_path.is_absolute():
                meeting_template_path = _base / meeting_template_path

            meeting_temp_dir = Path(app_config.meeting_temp_dir)
            if not meeting_temp_dir.is_absolute():
                meeting_temp_dir = _base / meeting_temp_dir

            download_base_url = app_config.meeting_download_base_url

            # loopback 검증
            if not (
                download_base_url.startswith("http://127.0.0.1")
                or download_base_url.startswith("http://localhost")
                or download_base_url.startswith("https://127.0.0.1")
                or download_base_url.startswith("https://localhost")
            ):
                logger.warning(
                    f"meeting_download_base_url이 loopback이 아님: {download_base_url} "
                    "— meeting_minutes_service=None 강등"
                )
                self.meeting_minutes_service = None
            else:
                # agent는 init_agent 이후에 주입됨; service는 agent를 나중에 받음.
                # 여기서는 임시로 None을 설정하고 init_agent에서 교체.
                # 실제로는 agent_engine을 통해 접근하지 않고, tool 호출 시 service를 직접 사용.
                # NOTE: agent가 필요하므로 실제 서비스는 init_agent 이후 배선.
                # 현재 단계에서는 template만 검증하고 나머지는 나중에 초기화.
                self.meeting_minutes_service = MeetingMinutesService(
                    agent=None,  # init_agent에서 set_agent로 교체
                    template_path=meeting_template_path,
                    temp_dir=meeting_temp_dir,
                    download_base_url=download_base_url,
                    custom_system_prompt=self.app_config.meeting_minutes_prompt
                    if self.app_config
                    else "",
                )
                logger.info("MeetingMinutesService 초기화 완료 (agent=None, init_agent에서 배선)")

                # B-1 fix: 1시간 주기 임시 파일 정리 스케줄러
                from apscheduler.schedulers.asyncio import AsyncIOScheduler
                from apscheduler.triggers.interval import IntervalTrigger

                self._temp_cleanup_scheduler = AsyncIOScheduler()
                self._temp_cleanup_scheduler.add_job(
                    self.meeting_minutes_service.cleanup_expired,
                    IntervalTrigger(hours=1),
                    id="meeting_minutes_cleanup",
                    replace_existing=True,
                )
                self._temp_cleanup_scheduler.start()
                logger.info("MeetingMinutes cleanup scheduler 시작 (1h interval)")
        except HwpxTemplateError as exc:
            logger.warning(f"MeetingMinutesService 초기화 실패 (템플릿 오류): {exc}")
            self.meeting_minutes_service = None
        except Exception as exc:
            logger.warning(f"MeetingMinutesService 초기화 실패: {exc}")
            self.meeting_minutes_service = None

        # M_07: RagService 조립 (ToolRouter 조립 전에 완료해야 rag= 인수로 전달 가능)
        try:
            from vector_search import Embedder, RagService, VectorStore

            bge_model_dir = str(Path(app_config.paths.assets_dir) / "models" / "bge-m3")
            vector_store_dir = app_config.paths.vector_store_dir
            # M_18: 리랭커 (모델 미배치 시 None — graceful degradation)
            reranker = None
            if app_config.rag_rerank_enabled:
                try:
                    from vector_search.reranker import Reranker

                    reranker_dir = str(
                        Path(app_config.paths.assets_dir) / "models" / "bge-reranker-v2-m3"
                    )
                    reranker = Reranker(model_dir=reranker_dir, device=app_config.rag_device)
                except Exception as exc:
                    logger.warning(f"리랭커 비활성 (모델 미배치 등): {exc}")

            store = VectorStore(db_path=vector_store_dir)
            self.rag_service = RagService(
                embedder=Embedder(
                    model_dir=bge_model_dir,
                    device=app_config.rag_device,
                    batch_size=app_config.rag_embed_batch_size,
                ),
                store=store,
                min_score=app_config.rag_min_score,
                reranker=reranker,
                hybrid_enabled=app_config.rag_hybrid_enabled,
                rerank_candidates=app_config.rag_rerank_candidates,
            )
            logger.info(
                f"RagService 초기화 완료: model={bge_model_dir}, store={vector_store_dir}, "
                f"device={app_config.rag_device}, batch={app_config.rag_embed_batch_size}, "
                f"reranker={'on' if reranker else 'off'}, hybrid={app_config.rag_hybrid_enabled}"
            )

            # M_18: 인덱스 생성은 수 초 걸릴 수 있어 기동 비차단 백그라운드로
            asyncio.get_running_loop().run_in_executor(None, store.ensure_indices)
        except Exception as exc:
            logger.warning(f"rag_service 초기화 실패 (search_docs 비활성화): {exc}")
            self.rag_service = None

        # M_15: KnowledgeService 초기화 — RagService 직후. RAG가 없어도 노트 CRUD는 가능.
        try:
            from knowledge import KnowledgeService

            saessagi_root = os.environ.get("SAESSAGI_ROOT", ".")
            self.knowledge_service = KnowledgeService(
                root=saessagi_root,
                rag_service=self.rag_service,
            )
            logger.info("KnowledgeService 초기화 완료")
        except Exception as exc:
            logger.warning(f"knowledge_service 초기화 실패 (save_knowledge_note 비활성화): {exc}")
            self.knowledge_service = None

        # CR-05: ToolRouter/Adapter 조립.
        # screenshot=None이면 take_screenshot만 service_unavailable, 나머지 툴은 정상 동작.
        self.tool_router = ToolRouter(
            calendar=self.calendar_service,
            rag=self.rag_service,
            screenshot=self.screenshot_service,  # None 허용 — 비-Windows 환경
            meeting_minutes=self.meeting_minutes_service,
            avatar_state=self.avatar_state,
            knowledge=self.knowledge_service,
        )
        self.tool_router_adapter = ToolRouterAdapter(self.tool_router)
        logger.info(
            "ToolRouter/ToolRouterAdapter 조립 완료 "
            f"(screenshot={'available' if self.screenshot_service is not None else 'unavailable'})"
        )

        # M-10: IdleMonitor 초기화 (스펙 §13.1)
        try:
            self.idle_monitor = IdleMonitor(
                idle_threshold_min=app_config.proactive.idle_threshold_min,
                overwork_threshold_min=app_config.proactive.overwork_threshold_min,
                active_gap_seconds=app_config.proactive.active_gap_seconds,
            )
            logger.info("M_10 IdleMonitor initialized.")
        except Exception as exc:
            logger.warning(f"idle_monitor 초기화 실패: {exc}")
            self.idle_monitor = None

        # M-11: ProactiveDispatcher 초기화 (스펙 §8.3)
        if self.calendar_service is not None and self.idle_monitor is not None:
            try:
                from proactive import ProactiveDispatcher

                self.proactive_dispatcher = ProactiveDispatcher(
                    calendar=self.calendar_service,
                    idle_monitor=self.idle_monitor,
                    send_text=self._get_active_client_send_text(),
                    morning_time=app_config.morning_briefing_time,
                    cooldown_min=app_config.proactive.cooldown_min,
                    dnd_enabled=app_config.dnd_enabled,
                )
                logger.info("M_11 ProactiveDispatcher initialized.")
            except Exception as exc:
                logger.warning(f"proactive_dispatcher 초기화 실패: {exc}")
                self.proactive_dispatcher = None
        else:
            logger.warning(
                "calendar_service 또는 idle_monitor가 None이므로 proactive_dispatcher 조립 건너뜀"
            )
            self.proactive_dispatcher = None

    async def close(self) -> None:
        """부모 close + 본 프로젝트 서비스 stop/close.

        순서 (CR-05 §필요 변경 3 기준):
          idle_monitor.stop() → proactive_dispatcher.stop()
          → screenshot_service.aclose() (연속 캡처 누수 방지)
          → rag_service.close() → calendar_service.close()
          → super().close()
        각 stop/close는 개별 try/except로 감싸 한 서비스 실패가 다른 정리를 막지 않도록.
        """
        # M_16: 분류 전용 에이전트 정리 (provider=ollama/openai일 때만 별도 인스턴스 존재)
        if self._intent_classifier_agent is not None:
            try:
                await self._intent_classifier_agent.aclose()
                logger.debug("intent_classifier_agent.aclose() 완료")
            except Exception as exc:
                logger.error(f"intent_classifier_agent.aclose() 실패: {exc}")

        if self.idle_monitor is not None:
            try:
                await _call_stop(self.idle_monitor, "idle_monitor")
            except Exception as exc:
                logger.error(f"idle_monitor.stop() 실패: {exc}")

        if self.proactive_dispatcher is not None:
            try:
                await _call_stop(self.proactive_dispatcher, "proactive_dispatcher")
            except Exception as exc:
                logger.error(f"proactive_dispatcher.stop() 실패: {exc}")

        # M_13: cleanup scheduler shutdown → meeting_minutes_service.aclose()
        if self._temp_cleanup_scheduler is not None:
            try:
                self._temp_cleanup_scheduler.shutdown(wait=True)
                logger.debug("_temp_cleanup_scheduler.shutdown 완료")
            except Exception as exc:
                logger.error(f"_temp_cleanup_scheduler.shutdown 실패: {exc}")
        if self.meeting_minutes_service is not None:
            try:
                await self.meeting_minutes_service.aclose()
                logger.debug("meeting_minutes_service.aclose() 완료")
            except Exception as exc:
                logger.error(f"meeting_minutes_service.aclose() 실패: {exc}")

        # CR-05: screenshot_service.aclose() — 연속 캡처 루프 종료 및 mss 리소스 해제
        if self.screenshot_service is not None:
            try:
                await self.screenshot_service.aclose()
                logger.debug("screenshot_service.aclose() 완료")
            except Exception as exc:
                logger.error(f"screenshot_service.aclose() 실패: {exc}")

        if self.rag_service is not None:
            try:
                await _call_close(self.rag_service, "rag_service")
            except Exception as exc:
                logger.error(f"rag_service.close() 실패: {exc}")

        if self.calendar_service is not None:
            try:
                await _call_close(self.calendar_service, "calendar_service")
            except Exception as exc:
                logger.error(f"calendar_service.close() 실패: {exc}")

        try:
            await super().close()
        except Exception as exc:
            logger.error(f"super().close() 실패: {exc}")


def _make_prompt_provider(
    app_config: "AppConfig | None",
) -> "Callable[[], dict[str, str]]":
    """M_17: prompt_provider 클로저 팩토리.

    매 턴 최신 커스텀 지침을 lazy 조회해 dict로 반환한다.
    **raw 커스텀 값만** 반환 — 빈 문자열이면 라우팅에서 None으로 정규화되어 미주입.
    effective_prompt(기본값 폴백 포함)를 사용하면 기본 상수가 항상 주입되어
    doc_query/work_query/note_save 모든 턴에 [작성 지침]이 강제 주입되므로 사용 금지.

    테스트: tests/app/test_service_context_prompt_provider.py
    """

    def _prompt_provider() -> dict[str, str]:
        try:
            if app_config is None:
                return {}
            ap = getattr(app_config, "agent_prompts", None)
            if ap is None:
                return {}
            # strip 후 비어있으면 ""(= 라우팅에서 None 정규화 → 미주입)
            doc = (getattr(ap, "doc_query_answer", "") or "").strip()
            work = (getattr(ap, "work_query_answer", "") or "").strip()
            note = (getattr(ap, "knowledge_note", "") or "").strip()
            return {
                "doc_query_answer": doc,
                "work_query_answer": work,
                "knowledge_note": note,
            }
        except Exception as _exc:
            logger.debug(f"prompt_provider 조회 실패 (무시): {_exc!r}")
            return {}

    return _prompt_provider


async def _call_stop(service: Any, name: str) -> None:
    """서비스의 stop() 메서드를 호출. 코루틴이면 await."""
    if hasattr(service, "stop"):
        result = service.stop()
        if hasattr(result, "__await__"):
            await result
        logger.debug(f"{name}.stop() 완료")


async def _call_close(service: Any, name: str) -> None:
    """서비스의 close() 메서드를 호출. 코루틴이면 await."""
    if hasattr(service, "close"):
        result = service.close()
        if hasattr(result, "__await__"):
            await result
        logger.debug(f"{name}.close() 완료")

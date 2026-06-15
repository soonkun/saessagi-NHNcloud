# src/agent/gemma_chat_agent.py
"""GemmaChatAgent — Ollama gemma4:e4b 대화 에이전트 (컴포지션 방식)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from open_llm_vtuber.agent.input_types import BatchInput
from open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import (
    AsyncLLM as OpenAICompatibleAsyncLLM,
)
from .no_think_llm import NoThinkLLM
from open_llm_vtuber.mcpp.tool_executor import ToolExecutor
from open_llm_vtuber.mcpp.tool_manager import ToolManager

from .errors import AgentBackendError, AgentInitError
from .events import (
    AgentError,
    AgentEvent,
    EndOfTurn,
    TextChunk,
    ToolCallResult,
    ToolCallStart,
)
from .health import OllamaHealth, probe_ollama

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [0.5, 1.0, 2.0]
_ERROR_CALLING_CHAT_PREFIX = "Error calling the chat endpoint"


# 이미지 턴(비전 모델) 전용 추출 프롬프트. persona("친절히 답해/정리해뒀어요")를 쓰면
# 모델이 대화체로 주제만 언급하고 세부를 전사하지 않아 노트에 알맹이가 없어진다.
# → 이미지 턴에선 이 추출 전용 시스템 프롬프트로 교체해 보이는 내용을 빠짐없이 뽑게 한다.
_VISION_EXTRACT_SYSTEM_PROMPT = (
    "당신은 첨부된 이미지(스크린샷·문서·사진)를 읽어 업무 노트의 근거 자료를 만드는 판독기입니다.\n"
    "이미지에 실제로 보이는 모든 텍스트·표·수치·날짜·인물·기관·금액·항목을 빠짐없이 그대로 추출해 정리하세요.\n"
    "가능한 한 누가(담당·참석자), 언제(일시·기한), 어디서(장소·기관), 무엇을(안건·내용), "
    "어떻게(방법·절차), 왜(목적·배경)가 드러나도록 항목별로 작성하세요.\n"
    "규칙: (1) 추측·창작 금지 — 이미지에 없는 항목은 비워 두세요. "
    "(2) '정리해 두었어요' 같은 인사·대화체·메타 발언 금지. 추출된 실제 내용만 출력하세요. "
    "(3) 한국어로, 항목/글머리 형태로 구체적으로 작성하세요."
)


def _normalize_openai_url(base_url: str) -> str:
    """base_url에서 /v1 suffix 포함 OpenAI 호환 URL 반환."""
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url
    return url + "/v1"


def _validate_params(
    base_url: str,
    temperature: float,
    max_context_tokens: int,
    system_prompt: str | None,
    use_mcpp: bool,
    tool_manager: ToolManager | None,
    tool_executor: ToolExecutor | None,
    is_external: bool = False,
) -> None:
    """파라미터 검증. 실패 시 AgentInitError 발생."""
    if not base_url:
        logger.error("base_url이 비어 있습니다.")
        raise AgentInitError("base_url required")

    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
    except Exception as e:
        raise AgentInitError(f"base_url 파싱 실패: {e}") from e

    if parsed.scheme not in ("http", "https"):
        logger.error(f"base_url scheme 오류: {parsed.scheme}")
        raise AgentInitError("scheme must be http/https")

    # 외부 공급자(OpenAI 등)는 URL 화이트리스트 검사 면제
    if not is_external:
        from src.app.url_guard import enforce_private_url
        from src.app.errors import PrivacyViolationError

        try:
            enforce_private_url(base_url, field_name="agent.base_url")
        except PrivacyViolationError as e:
            logger.error(f"base_url 화이트리스트 위반: {e}")
            raise AgentInitError("base_url must be loopback or private IP") from e

    if not (0.0 <= temperature <= 2.0):
        logger.error(f"temperature 범위 오류: {temperature}")
        raise AgentInitError("temperature out of range [0.0, 2.0]")

    if max_context_tokens <= 0:
        logger.error(f"max_context_tokens 오류: {max_context_tokens}")
        raise AgentInitError("max_context_tokens must be > 0")

    if system_prompt is None:
        logger.error("system_prompt가 None입니다.")
        raise AgentInitError("system_prompt must be str (use '' for empty)")

    if use_mcpp and (tool_manager is None or tool_executor is None):
        logger.error("use_mcpp=True인데 tool_manager 또는 tool_executor가 None입니다.")
        raise AgentInitError("tool_manager required when use_mcpp=True")


class GemmaChatAgent:
    """Ollama `gemma4:e4b`에 맞춰 구성된 대화 에이전트 (컴포지션).

    내부적으로 upstream `BasicMemoryAgent` 인스턴스를 보유하되, `chat()`만 본 모듈에서
    직접 구현해 upstream의 `_to_messages`와 `_openai_tool_interaction_loop`를 호출한다.
    출력은 본 프로젝트의 `AgentEvent`로 정규화된다.

    생성 방법:
        agent = await GemmaChatAgent.create(base_url=..., model=..., ...)

    직접 `GemmaChatAgent(...)` 호출은 헬스체크를 수행하지 않으므로 사용 금지.
    테스트 목적으로는 헬스체크를 monkeypatch 후 `create()`를 사용한다.
    """

    base_url: str
    model: str
    temperature: float
    max_context_tokens: int
    system_prompt: str
    _llm: OpenAICompatibleAsyncLLM
    _inner: BasicMemoryAgent
    _chat_lock: asyncio.Lock
    _use_mcpp: bool
    _formatted_tools_openai: list[dict[str, Any]]

    def __init__(
        self,
        base_url: str,
        model: str,
        system_prompt: str,
        tool_manager: ToolManager | None,
        tool_executor: ToolExecutor | None,
        temperature: float,
        max_context_tokens: int,
        faster_first_response: bool,
        interrupt_method: Literal["system", "user"],
        use_mcpp: bool,
        extra_tool_specs: list[dict[str, Any]] | None = None,
        tts_preprocessor_config: Any | None = None,
        llm_api_key: str = "z",
        is_external: bool = False,
        vision_model: str = "",
    ) -> None:
        """필드만 초기화. 직접 호출 금지 — create() classmethod를 사용하라.

        파라미터 검증과 헬스체크는 create()가 수행 후 이 메서드를 호출한다.
        """
        self.base_url = base_url
        self.model = model
        self._vision_model = vision_model  # 이미지 턴 전용 (빈 문자열이면 라우팅 안 함)
        self.temperature = temperature
        self.max_context_tokens = max_context_tokens
        self.system_prompt = system_prompt
        self._use_mcpp = use_mcpp

        openai_url = _normalize_openai_url(base_url)

        if is_external:
            # 외부 API(OpenAI 등): NoThinkLLM 패치 없이 순수 AsyncLLM 사용.
            # organization_id/project_id를 None으로 명시 — 기본값 "z"는 OpenAI API가 400으로 거부.
            self._llm = OpenAICompatibleAsyncLLM(
                model=model,
                base_url=openai_url,
                llm_api_key=llm_api_key,
                organization_id=None,
                project_id=None,
                temperature=temperature,
            )
        else:
            # Ollama: NoThinkLLM으로 extended-thinking 비활성화 (gemma4:e2b/e4b 성능 개선)
            self._llm = NoThinkLLM(
                model=model,
                base_url=openai_url,
                temperature=temperature,
            )

        # upstream BasicMemoryAgent 인스턴스 생성 (컴포지션)
        self._inner = BasicMemoryAgent(
            llm=self._llm,
            system=system_prompt,
            live2d_model=None,
            tts_preprocessor_config=tts_preprocessor_config,
            faster_first_response=faster_first_response,
            use_mcpp=use_mcpp,
            interrupt_method=interrupt_method,
            tool_manager=tool_manager,
            tool_executor=tool_executor,
        )

        # tool 목록 캐시 (MCP + extras 병합)
        if use_mcpp and tool_manager is not None:
            mcp_tools: list[dict[str, Any]] = tool_manager.get_formatted_tools("OpenAI")
        else:
            mcp_tools = []

        extras: list[dict[str, Any]] = list(extra_tool_specs) if extra_tool_specs else []

        # 이름 충돌 검사 (FAIL-fast)
        mcp_names = {t["function"]["name"] for t in mcp_tools}
        extra_names = {t["function"]["name"] for t in extras}
        overlap = mcp_names & extra_names
        if overlap:
            msg = f"tool name conflict: {sorted(overlap)}"
            logger.error(f"extra_tool_specs 이름 충돌: {sorted(overlap)}")
            raise AgentInitError(msg)

        self._formatted_tools_openai = mcp_tools + extras

        # 동시 chat 호출 직렬화 락
        self._chat_lock = asyncio.Lock()

        logger.info(
            f"GemmaChatAgent 초기화 완료: model={model}, base_url={openai_url}, "
            f"use_mcpp={use_mcpp}, tools={len(self._formatted_tools_openai)}"
        )

    @classmethod
    async def create(
        cls,
        base_url: str,
        model: str = "gemma4:e4b",
        vision_model: str = "",
        system_prompt: str = "",
        tool_manager: ToolManager | None = None,
        tool_executor: ToolExecutor | None = None,
        temperature: float = 0.7,
        max_context_tokens: int = 131_000,
        faster_first_response: bool = True,
        interrupt_method: Literal["system", "user"] = "user",
        use_mcpp: bool = True,
        extra_tool_specs: list[dict[str, Any]] | None = None,
        tts_preprocessor_config: Any | None = None,
        llm_api_key: str = "z",
        is_external: bool = False,
    ) -> "GemmaChatAgent":
        """GemmaChatAgent를 생성하는 공식 비동기 팩토리 메서드.

        is_external=True이면 URL 화이트리스트 검증과 Ollama 헬스체크를 건너뛴다.
        OpenAI 같은 외부 공급자 사용 시 설정.
        """
        _validate_params(
            base_url=base_url,
            temperature=temperature,
            max_context_tokens=max_context_tokens,
            system_prompt=system_prompt,
            use_mcpp=use_mcpp,
            tool_manager=tool_manager,
            tool_executor=tool_executor,
            is_external=is_external,
        )

        if not is_external:
            # Ollama 헬스체크 (3회 재시도, 0.5/1.0/2.0s sleep)
            health = await cls._run_health_check_with_retry(base_url, model)
            cls._validate_health(health, model)
        else:
            logger.info(f"외부 LLM 공급자 — Ollama 헬스체크 건너뜀: model={model}")

        return cls(
            base_url=base_url,
            model=model,
            vision_model=vision_model,
            system_prompt=system_prompt,
            tool_manager=tool_manager,
            tool_executor=tool_executor,
            temperature=temperature,
            max_context_tokens=max_context_tokens,
            faster_first_response=faster_first_response,
            interrupt_method=interrupt_method,
            use_mcpp=use_mcpp,
            extra_tool_specs=extra_tool_specs,
            tts_preprocessor_config=tts_preprocessor_config,
            llm_api_key=llm_api_key,
            is_external=is_external,
        )

    @staticmethod
    async def _run_health_check_with_retry(base_url: str, model: str) -> OllamaHealth:
        """3회 probe 시도. 각 실패 후 0.5/1.0/2.0초 sleep.

        시퀀스:
          probe() → 실패 → sleep(0.5) → probe() → 실패 → sleep(1.0) →
          probe() → 실패 → sleep(2.0) → raise AgentBackendError

        총 sleep 합 = 0.5 + 1.0 + 2.0 = 3.5s (3회 probe 모두 실패 시).
        """
        last_health: OllamaHealth | None = None
        for attempt in range(len(_RETRY_DELAYS)):
            health = await probe_ollama(base_url, model)
            last_health = health

            if health.reachable and health.model_available:
                logger.info(f"Ollama 헬스체크 성공 (attempt={attempt + 1})")
                return health

            if health.reachable and not health.model_available:
                # 모델 없음 — 재시도해도 의미 없음
                logger.error(f"Ollama 모델 '{model}' 없음: 재시도 중단")
                return health

            delay = _RETRY_DELAYS[attempt]
            logger.warning(
                f"Ollama 접근 불가 (attempt={attempt + 1}/{len(_RETRY_DELAYS)}): "
                f"{health.error} — {delay}s 후 재시도"
            )
            await asyncio.sleep(delay)

        assert last_health is not None
        return last_health

    @staticmethod
    def _validate_health(health: OllamaHealth, model: str) -> None:
        """헬스체크 결과 검증. 실패 시 AgentBackendError 발생."""
        if not health.reachable:
            msg = f"Ollama unreachable: {health.error}"
            logger.error(msg)
            raise AgentBackendError(msg)
        if not health.model_available:
            msg = f"model '{model}' not available in Ollama"
            logger.error(msg)
            raise AgentBackendError(msg)

    async def chat(self, batch: BatchInput) -> AsyncIterator[AgentEvent]:
        """한 턴의 대화를 AgentEvent 스트림으로 방출.

        주의: chat()은 AsyncGenerator이므로 async for로 소비해야 한다.
        """
        # 1) 빈 입력 조기 리턴 (texts=[] and images가 falsy)
        if not batch.texts and not batch.images:
            logger.warning("빈 입력 감지: texts=[], images=None or []")
            yield AgentError(code="empty_response", message="입력이 비어 있어요.")
            yield EndOfTurn(assistant_text_total="")
            return

        async with self._chat_lock:
            self._inner.reset_interrupt()
            self._inner.prompt_mode_flag = False

            # 이미지 첨부 턴 (A안): 전사 전용 모델(vision_model)로 이미지를 먼저 OCR 전사한 뒤,
            # 그 텍스트를 입력에 주입해 batch를 텍스트화한다. 이후 메인 모델(gemma4)이 평소처럼
            # 페르소나·도구(save_knowledge_note 등)로 답변·노트를 담당한다.
            # (gemma4는 이미지를 못 보지만, 전사 텍스트는 일반 텍스트로 처리 가능.)
            if batch.images and self._vision_model:
                batch = await self._transcribe_images_into_batch(batch)

            messages = self._inner._to_messages(batch)
            # use_mcpp=False여도 extra_tool_specs(ToolRouter)이 있으면 도구 호출 경로 사용
            tools = self._formatted_tools_openai  # MCP + ToolRouter extras

            if tools:
                raw_stream = self._inner._openai_tool_interaction_loop(messages, tools)
            else:
                raw_stream = self._simple_stream(messages)

            assistant_text_total = ""
            has_tool_call = False

            try:
                async for item in raw_stream:
                    if isinstance(item, str):
                        if item == "__API_NOT_SUPPORT_TOOLS__":
                            logger.error("__API_NOT_SUPPORT_TOOLS__ 감지 — AgentError 방출")
                            yield AgentError(
                                code="api_not_support_tools",
                                message="이 모델은 도구 호출을 지원하지 않습니다.",
                            )
                            return  # EndOfTurn 없이 종료
                        if item.startswith(_ERROR_CALLING_CHAT_PREFIX):
                            logger.error(f"backend 에러 문자열 감지: {item[:80]}")
                            yield AgentError(
                                code="backend_unreachable",
                                message="LLM 백엔드에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                            )
                            yield EndOfTurn(assistant_text_total="")
                            return
                        if item != "":
                            yield TextChunk(text=item)
                            assistant_text_total += item
                    elif isinstance(item, dict):
                        event = self._translate_tool_event(item)
                        if event is not None:
                            if isinstance(event, ToolCallStart):
                                has_tool_call = True
                            yield event
                    else:
                        logger.warning(f"알 수 없는 upstream 아이템 타입: {type(item)} — 무시")

            except asyncio.CancelledError:
                logger.debug("chat() CancelledError — 락 해제 후 재전파")
                raise

            # 8) tool만 있고 텍스트 없는 경우
            if not assistant_text_total and has_tool_call:
                fallback = "(도구 실행 결과를 확인했어요.)"
                assistant_text_total = fallback
                yield TextChunk(text=fallback)

            # 빈 응답 (tool도 없고 텍스트도 없음)
            if not assistant_text_total and not has_tool_call:
                logger.warning("빈 응답 감지: LLM이 아무것도 반환하지 않음")
                fallback = "(잠시만요, 생각이 정리되지 않았어요. 다시 질문해 주시겠어요?)"
                yield TextChunk(text=fallback)
                assistant_text_total = fallback

            # simple_stream 경로에서는 upstream이 _add_message를 호출하지 않으므로 직접 호출
            if not (self._use_mcpp and tools) and assistant_text_total:
                self._inner._add_message(assistant_text_total, "assistant")

            yield EndOfTurn(assistant_text_total=assistant_text_total)

    async def _transcribe_images_into_batch(self, batch: BatchInput) -> BatchInput:
        """이미지 첨부 batch를 전사 모델로 OCR해 텍스트화한다 (A안).

        vision_model로 이미지 내용을 추출 → '[첨부 이미지에서 추출한 내용]' 블록으로
        기존 텍스트에 덧붙이고 images=None인 새 BatchInput을 반환한다. 이렇게 하면
        이후 메인 모델(gemma4)이 일반 텍스트 턴으로 처리해 도구 호출까지 정상 수행한다.
        전사 실패 시 원본 batch를 그대로 반환(회귀 방지).
        """
        from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

        try:
            transcription = await self._transcribe_images(batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("이미지 전사 실패 (원본 유지): %s", exc)
            return batch

        if not transcription.strip():
            logger.warning("이미지 전사 결과 비어 있음 — 원본 batch 유지")
            return batch

        logger.info(
            "이미지 전사 완료(model=%s, %d자) → 메인 모델(%s)이 답변·노트 담당",
            self._vision_model,
            len(transcription),
            self.model,
        )
        extra = TextData(
            source=TextSource.INPUT,
            content="[첨부 이미지에서 추출한 내용 — 이 내용을 근거로 답변/노트를 작성]\n"
            + transcription.strip(),
            from_name="이미지전사",
        )
        return BatchInput(
            texts=list(batch.texts or []) + [extra],
            images=None,
            metadata=batch.metadata,
        )

    async def _transcribe_images(self, batch: BatchInput) -> str:
        """vision_model을 비스트리밍 호출해 batch의 이미지 내용을 전사한 텍스트를 반환."""
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": "이 이미지에 보이는 모든 내용을 빠짐없이 추출해 정리해줘."}
        ]
        for img in batch.images or []:
            data = getattr(img, "data", None)
            if isinstance(data, str) and data.startswith("data:image"):
                user_content.append(
                    {"type": "image_url", "image_url": {"url": data, "detail": "auto"}}
                )
        if len(user_content) == 1:  # 유효한 이미지가 없음
            return ""
        async with asyncio.timeout(120.0):
            resp = await self._llm.client.chat.completions.create(
                model=self._vision_model,
                messages=[
                    {"role": "system", "content": _VISION_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                stream=False,
                temperature=0.2,
            )
        return resp.choices[0].message.content or ""

    async def handle_interrupt(self, heard_text: str) -> None:
        """upstream BasicMemoryAgent.handle_interrupt에 단순 위임.

        upstream은 동기 메서드지만 본 시그니처는 async로 노출(상위 WebSocket 핸들러 호환).
        handle_interrupt는 _chat_lock을 획득하지 않는다 (데드락 방지).
        """
        logger.debug(f"handle_interrupt 호출: heard_text={heard_text!r}")
        self._inner.handle_interrupt(heard_text)

    async def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
        """upstream BasicMemoryAgent.set_memory_from_history에 위임(동기 → 비동기 래핑).

        메모리 변경과 chat()의 _to_messages 경합을 막기 위해 _chat_lock을 획득한다.
        """
        async with self._chat_lock:
            logger.debug(f"set_memory_from_history: conf_uid={conf_uid}, history_uid={history_uid}")
            self._inner.set_memory_from_history(conf_uid, history_uid)

    def set_system_prompt(self, prompt: str) -> None:
        """런타임 persona 교체용. upstream BasicMemoryAgent.set_system을 호출."""
        self.system_prompt = prompt
        self._inner.set_system(prompt)

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """비스트리밍 JSON 완성 메서드 (CR-MM-A).

        system_prompt + user_prompt를 Ollama에 보내고 응답을 JSON으로 파싱해 반환한다.
        response_format={"type": "json_object"}를 사용해 JSON 모드를 활성화한다.

        Args:
            system_prompt: 시스템 프롬프트.
            user_prompt: 사용자 프롬프트.
            json_schema: JSON Schema (현재 미사용, Protocol 호환용).
            max_tokens: 최대 생성 토큰 수.
            temperature: 샘플링 온도.
            timeout_seconds: 요청 타임아웃(초). 초과 시 asyncio.TimeoutError 전파.

        Returns:
            파싱된 JSON dict.

        Raises:
            ValueError: 응답이 유효한 JSON이 아닌 경우.
            asyncio.TimeoutError: timeout_seconds 초과.
        """
        logger.info(
            f"complete_json 호출: model={self.model}, max_tokens={max_tokens}, "
            f"temperature={temperature}, timeout={timeout_seconds}s"
        )

        async with asyncio.timeout(timeout_seconds):
            response = await self._llm.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                stream=False,
                **self._completion_params(max_tokens, temperature),
            )

        content = response.choices[0].message.content or ""
        logger.debug(f"complete_json 응답 길이: {len(content)}자")

        try:
            result: dict[str, Any] = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(f"complete_json JSON 파싱 실패: {exc}. 응답 앞 200자: {content[:200]}")
            raise ValueError(f"LLM 응답이 유효한 JSON이 아닙니다: {exc}") from exc

        return result

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        timeout_seconds: float = 120.0,
    ) -> str:
        """비스트리밍 플레인 텍스트 완성 메서드.

        response_format 없이 호출 — JSON 모드 불필요한 경우에 사용.
        NoThinkLLM이 think=False를 주입하므로 extra_body 불필요.
        """
        logger.info(
            f"complete_text 호출: model={self.model}, max_tokens={max_tokens}, "
            f"temperature={temperature}, timeout={timeout_seconds}s"
        )
        async with asyncio.timeout(timeout_seconds):
            response = await self._llm.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                **self._completion_params(max_tokens, temperature),
            )
        content = response.choices[0].message.content or ""
        logger.debug(f"complete_text 응답 길이: {len(content)}자")
        return content.strip()

    def _completion_params(self, max_tokens: int, temperature: float) -> dict[str, Any]:
        """모델별 completion 파라미터 호환 처리.

        GPT-5/o-시리즈는 (1) max_tokens를 거부하고(max_completion_tokens 필요)
        (2) temperature도 1.0만 허용 — 이전엔 400 에러로 회의록 생성이 통째로 실패.
        (3) 추론 토큰이 출력 예산을 잠식해 응답이 빈 문자열이 되므로,
        정형 문서 작업엔 reasoning_effort=low + 예산 헤드룸이 필요하다 (E-41).
        """
        model = (self.model or "").lower()
        if model.startswith("gpt-5"):
            return {
                "max_completion_tokens": max_tokens + 4096,  # 추론 토큰 헤드룸
                "reasoning_effort": "low",
            }
        if model.startswith(("o1", "o3", "o4")):
            return {"max_completion_tokens": max_tokens + 4096}
        return {"max_tokens": max_tokens, "temperature": temperature}

    async def aclose(self) -> None:
        """리소스 정리. openai AsyncClient를 닫는다."""
        try:
            await self._llm.client.close()
            logger.debug("AsyncLLM httpx 클라이언트 정상 종료")
        except Exception as e:
            logger.warning(f"aclose 중 오류: {e}")

    async def _simple_stream(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str | dict[str, Any]]:
        """tool 없는 경로. NoThinkLLM이 think=False를 이미 주입하므로 extra_body 불필요."""
        try:
            system = self._inner._system
            messages_with_system = (
                [{"role": "system", "content": system}, *messages] if system else messages
            )
            stream = await self._llm.client.chat.completions.create(
                messages=messages_with_system,
                model=self._llm.model,
                stream=True,
                temperature=self._llm.temperature,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"_simple_stream 예외: {e}")
            yield f"Error calling the chat endpoint: {e}"

    def _translate_tool_event(
        self,
        item: dict[str, Any],
    ) -> AgentEvent | None:
        """upstream ToolExecutor가 yield하는 dict를 AgentEvent로 변환.

        매핑:
          item["type"] == "tool_call_status" and item["status"] == "running"
              -> ToolCallStart(tool_id, name, arguments=<파싱 결과>)
          item["type"] == "tool_call_status" and item["status"] in ("completed","error")
              -> ToolCallResult(tool_id, name, ok=(status=="completed"), content=item["content"])
          item["type"] == "final_tool_results"
              -> None
          기타
              -> None + 로그 DEBUG
        """
        item_type = item.get("type")

        if item_type == "tool_call_status":
            status = item.get("status", "")
            tool_id = item.get("tool_id", "")
            tool_name = item.get("tool_name", "")
            content = item.get("content", "")

            if status == "running":
                arguments = self._parse_running_args(content)
                return ToolCallStart(
                    tool_id=tool_id,
                    name=tool_name,
                    arguments=arguments,
                )
            elif status in ("completed", "error"):
                return ToolCallResult(
                    tool_id=tool_id,
                    name=tool_name,
                    ok=(status == "completed"),
                    content=content,
                )
            else:
                logger.debug(f"알 수 없는 tool_call_status: {status}")
                return None

        elif item_type == "final_tool_results":
            return None  # 내부 state, 이벤트 아님

        else:
            logger.debug(f"알 수 없는 upstream dict type: {item_type} — 무시")
            return None

    @staticmethod
    def _parse_running_args(content: str) -> dict[str, Any]:
        """running status_update["content"] 문자열에서 arguments dict 파싱.

        upstream이 "Input: {...}" 형식으로 제공하는 경우 JSON 파싱 시도.
        파싱 실패 시 빈 dict 반환.
        """
        if not content:
            return {}

        # "Input: {...}" 패턴 시도
        prefix = "Input: "
        if content.startswith(prefix):
            json_str = content[len(prefix) :]
        else:
            json_str = content

        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except (json.JSONDecodeError, ValueError):
            logger.debug(f"running args JSON 파싱 실패: {content[:100]}")
            return {}

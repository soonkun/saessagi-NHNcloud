# src/agent/upstream_adapter.py
"""BasicMemoryAgentAdapter — upstream AgentInterface 호환 어댑터."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .gemma_chat_agent import GemmaChatAgent

# RAG를 트리거할 한국어 키워드 패턴.
# 의문사·요청어·질문 종결어미 등 정보 탐색 의도가 명확한 표현만 포함.
_RAG_TRIGGER_RE = re.compile(
    r"뭐야|뭔데|뭐임|뭐지|뭐예요|뭔가요|뭐인지|뭐인가|뭐였어|뭐였나|무엇이|무엇인|뭘|뭐가|뭐를|뭔지|"
    r"어떻게|어떡해|어떠해|어떤|어디야|어디에|어디서|어디|어딨|"
    r"왜냐|왜요|왜지|"
    r"누구야|누군가|누가|누굴|"
    r"언제야|언제부터|언제까지|"
    r"얼마야|얼마나|몇 개|몇개|몇 번|몇번|"
    r"알려줘|알려주세요|알려줘요|"
    r"찾아줘|찾아봐|찾아주세요|"
    r"검색해|검색해줘|검색해봐|검색해주세요|"
    r"가르쳐줘|가르쳐주세요|"
    r"설명해줘|설명해|설명해주세요|"
    r"말해줘|말해주세요|"
    r"알아\?|알고있어|알고 있어|"
    r"있어\?|있나\?|있니\?|있어요\?|있나요\?|있어|있나|있니|있어요|있나요|"
    r"방법|사용법|사용방법|이용방법|양식|규정|절차|기준|서식|"
    r"어떤거|어떤 거|어떤걸|어떤 걸"
)

# Proactive RAG 주입 시 적용할 최소 유사도 임계값.
# RagService의 min_score(0.35)보다 높게 설정해 낮은 관련성 문서 주입을 방지.
_MIN_INJECTION_SCORE = 0.50


def _format_rag_context(hits: list[dict[str, Any]]) -> str:
    """RAG 검색 결과를 LLM 주입용 텍스트로 포맷.

    각 hit에 doc_id를 명시 → LLM이 답변에 [[doc:doc_id]] 마커를 포함하면
    프론트가 정확한 다운로드 칩을 자동 렌더한다.
    """
    lines = [
        "[관련 문서 검색 결과]",
        "아래 내용을 바탕으로 답변하세요. 답변에 인용한 자료는 반드시 답변 안에 "
        "`[[doc:<doc_id>]]` 마커를 한 번 포함해야 합니다 — 사용자에게는 그것이 "
        "다운로드 칩으로 보입니다. 노트(is_note=true)는 `[[note:<slug>]]` 마커를 사용하세요. "
        "마커 자체는 본문에 보이지 않습니다.",
        "",
    ]
    for h in hits:
        text = (h.get("text") or "").strip()
        if not text:
            continue
        doc_id = h.get("doc_id") or ""
        is_note = h.get("is_note", False)
        if is_note and doc_id.startswith("__knowledge__:"):
            slug = doc_id.split(":", 1)[1]
            marker_hint = f"[[note:{slug}]]"
            label = f"노트 marker={marker_hint}"
        elif doc_id:
            marker_hint = f"[[doc:{doc_id}]]"
            label = f"문서 doc_id={doc_id} marker={marker_hint}"
        else:
            label = ""
        block = f"--- {label} ---\n{text}" if label else text
        lines.append(block)
    return "\n\n".join(lines)


def _make_adapter_class() -> type:
    """동적으로 BasicMemoryAgentAdapter 클래스를 생성해 mypy Any 서브클래싱 에러 우회."""
    from open_llm_vtuber.agent.agents.agent_interface import AgentInterface
    from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

    from .events import AgentError, EndOfTurn, TextChunk, ToolCallResult, ToolCallStart

    class _BasicMemoryAgentAdapter(AgentInterface):  # type: ignore[misc]
        """upstream ConversationOrchestrator가 기대하는 AgentInterface를 GemmaChatAgent로 만족시키는 얇은 어댑터.

        Proactive RAG: 질문·탐색 의도가 감지된 메시지에 한해 벡터 검색을 수행하고
        유사도 임계값(_MIN_INJECTION_SCORE) 이상인 결과만 컨텍스트에 주입한다.
        """

        def __init__(self, agent: "GemmaChatAgent", rag_service: Any = None) -> None:
            super().__init__()
            self._agent = agent
            self._rag_service = rag_service  # vector_search.RagService | None
            self._pending_tasks: set[asyncio.Task[None]] = set()

        @staticmethod
        def _should_trigger_rag(text: str) -> bool:
            """질문·검색 의도 키워드가 포함돼 있으면 True 반환.

            '안녕?'처럼 단순 인사나 지시어에는 RAG를 실행하지 않도록
            정보 탐색 의도가 명확한 표현만 검사한다.
            """
            return bool(_RAG_TRIGGER_RE.search(text))

        async def _augment_with_rag(self, input_data: BatchInput) -> BatchInput:
            """사용자 메시지를 RAG 결과로 증강.

            - rag_service가 없거나 트리거 키워드 없으면 즉시 반환(벡터 검색 생략).
            - 유사도 _MIN_INJECTION_SCORE 미만인 hits는 주입하지 않음.
            - 실제 rag.retrieve() 결과만 사용 — 하드코딩 없음.
            """
            if self._rag_service is None:
                return input_data

            # 사용자 메시지 텍스트 추출
            user_text = " ".join(
                t.content
                for t in (input_data.texts or [])
                if t.source == TextSource.INPUT
            ).strip()
            if not user_text:
                return input_data

            # 트리거 키워드 없으면 RAG 생략 (벡터 검색 비용 절감)
            if not self._should_trigger_rag(user_text):
                logger.debug("RAG 스킵: 트리거 키워드 없음 (query=%r)", user_text[:50])
                return input_data

            try:
                # 블로킹 IO를 executor로 분리
                retrieval = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._rag_service.retrieve(user_text, 5)
                )
                if not retrieval.found or not retrieval.hits:
                    logger.debug("RAG 스킵: 관련 문서 없음 (query=%r)", user_text[:50])
                    return input_data

                # _MIN_INJECTION_SCORE 이상인 hits만 주입 (낮은 유사도 문서 배제)
                hits = [
                    {
                        "doc_id": getattr(h, "doc_id", None),
                        "doc_name": getattr(h, "doc_name", None),
                        "page": getattr(h, "page", None),
                        "text": getattr(h, "text", ""),
                        "score": float(getattr(h, "score", 0.0)),
                        "is_note": getattr(h, "category", None) == "__knowledge__",
                    }
                    for h in retrieval.hits
                    if float(getattr(h, "score", 0.0)) >= _MIN_INJECTION_SCORE
                ]
                if not hits:
                    logger.debug(
                        "RAG 스킵: 임계값(%.2f) 이상 hits 없음 (top_score=%.3f)",
                        _MIN_INJECTION_SCORE,
                        float(getattr(retrieval.hits[0], "score", 0.0)) if retrieval.hits else 0.0,
                    )
                    return input_data

                context_text = _format_rag_context(hits)
                logger.info(
                    "Proactive RAG: %d건 주입 (query=%r, top_score=%.3f)",
                    len(hits),
                    user_text[:50],
                    hits[0]["score"],
                )

                # 컨텍스트를 사용자 메시지 앞에 TextSource.INPUT으로 삽입
                context_td = TextData(
                    source=TextSource.INPUT,
                    content=context_text,
                    from_name="문서검색",
                )
                new_texts = [context_td] + list(input_data.texts or [])
                return BatchInput(
                    texts=new_texts,
                    images=input_data.images,
                    metadata=input_data.metadata,
                )

            except Exception as exc:
                logger.warning("Proactive RAG 실패 (무시): %s", exc)
                return input_data

        async def chat(  # type: ignore[override]
            self, input_data: BatchInput
        ) -> AsyncIterator[Any]:
            """GemmaChatAgent.chat를 소비해 upstream SentenceOutput 스트림으로 변환.

            - Proactive RAG: 트리거 키워드 감지 시 자동 검색 후 컨텍스트 주입
            - TextChunk 누적 → 전체 텍스트를 하나의 SentenceOutput으로 yield
            - ToolCallStart/Result → dict yield (upstream이 JSON으로 전송)
            - EndOfTurn → 스트림 종료
            - AgentError → 에러 텍스트를 SentenceOutput으로 yield
            """
            from open_llm_vtuber.agent.output_types import SentenceOutput, DisplayText, Actions

            # Proactive RAG 적용
            input_data = await self._augment_with_rag(input_data)

            text_parts: list[str] = []

            async for event in self._agent.chat(input_data):
                if isinstance(event, TextChunk):
                    if event.text:
                        text_parts.append(event.text)
                elif isinstance(event, ToolCallStart):
                    yield {
                        "type": "tool_call_start",
                        "tool_id": event.tool_id,
                        "name": event.name,
                        "arguments": event.arguments,
                    }
                elif isinstance(event, ToolCallResult):
                    yield {
                        "type": "tool_call_status",
                        "status": "completed" if event.ok else "error",
                        "tool_id": event.tool_id,
                        "tool_name": event.name,
                        "content": event.content,
                    }
                elif isinstance(event, EndOfTurn):
                    break
                elif isinstance(event, AgentError):
                    logger.warning(f"AgentError를 텍스트로 변환: code={event.code}")
                    text_parts.append(f"[오류: {event.message}]")

            full_text = "".join(text_parts)
            if full_text:
                yield SentenceOutput(
                    display_text=DisplayText(text=full_text, name="AI"),
                    tts_text=full_text,
                    actions=Actions(),
                )

        def handle_interrupt(self, heard_response: str) -> None:
            """동기 인터페이스 요구. asyncio 태스크로 스케줄."""
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._agent.handle_interrupt(heard_response))
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
                logger.debug(f"handle_interrupt 태스크 스케줄: {heard_response!r}")
            except RuntimeError:
                logger.warning(
                    "handle_interrupt: 실행 중인 이벤트 루프 없음 — upstream _inner에 직접 위임"
                )
                self._agent._inner.handle_interrupt(heard_response)

        def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
            """동기 인터페이스 요구. upstream BasicMemoryAgent에 직접 위임."""
            self._agent._inner.set_memory_from_history(conf_uid, history_uid)

        async def close(self) -> None:
            """GemmaChatAgent 내부 httpx 클라이언트 종료 (누수 방지)."""
            await self._agent.aclose()

    return _BasicMemoryAgentAdapter


BasicMemoryAgentAdapter: type = _make_adapter_class()

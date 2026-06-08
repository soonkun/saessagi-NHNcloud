# src/agent/upstream_adapter.py
"""BasicMemoryAgentAdapter — upstream AgentInterface 호환 어댑터."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable, Mapping
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
    r"있어\?|있나\?|있니\?|있어요\?|있나요\?|"  # M_16 변경 6: 물음표 없는 평서문 종결어미 제거
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


def _marker_for_hit(h: dict[str, Any]) -> str | None:
    """RAG hit → 프론트가 칩으로 렌더할 `[[doc:...]]` / `[[note:...]]` 마커."""
    doc_id = h.get("doc_id") or ""
    is_note = h.get("is_note", False)
    if is_note and doc_id.startswith("__knowledge__:"):
        slug = doc_id.split(":", 1)[1]
        return f"[[note:{slug}]]"
    if doc_id:
        return f"[[doc:{doc_id}]]"
    return None


# LLM이 직접 만든 (정상이든 깨졌든) doc/note 마커. doc_id에는 공백·괄호·점이
# 들어가므로 LLM이 정확히 복사하지 못해 `[[doc:xxx]`처럼 깨지기 쉽다 →
# 백엔드가 권위 있는 마커를 따로 붙이므로, LLM이 낸 마커는 표시 전에 모두 제거.
_LLM_MARKER_RE = re.compile(r"\[\[(?:doc|note):[^\[\]]*\]{0,2}")


def _strip_llm_markers(text: str) -> str:
    cleaned = _LLM_MARKER_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    return cleaned.strip()


def _make_adapter_class() -> type:
    """동적으로 BasicMemoryAgentAdapter 클래스를 생성해 mypy Any 서브클래싱 에러 우회."""
    from open_llm_vtuber.agent.agents.agent_interface import AgentInterface
    from open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource

    from .events import AgentError, EndOfTurn, TextChunk, ToolCallResult, ToolCallStart

    class _BasicMemoryAgentAdapter(AgentInterface):  # type: ignore[misc]
        """upstream ConversationOrchestrator가 기대하는 AgentInterface를 GemmaChatAgent로 만족시키는 얇은 어댑터.

        Proactive RAG: 질문·탐색 의도가 감지된 메시지에 한해 벡터 검색을 수행하고
        유사도 임계값(_MIN_INJECTION_SCORE) 이상인 결과만 컨텍스트에 주입한다.

        M_16 IntentGate: chat() 진입 직후 LLM 기반 의도 분류(1회)를 수행해
        RAG on/off, 검색 소스, 도구 힌트를 결정론적으로 라우팅한다.
        """

        def __init__(
            self,
            agent: "GemmaChatAgent",
            rag_service: Any = None,
            intent_classifier: Any = None,  # IntentClassifier | None (M_16)
            prompt_provider: Callable[[], Mapping[str, str]] | None = None,  # M_17
        ) -> None:
            super().__init__()
            self._agent = agent
            self._rag_service = rag_service  # vector_search.RagService | None
            self._intent_classifier = intent_classifier  # M_16: IntentClassifier | None
            # M_17: lazy 지침 조회 클로저. None이면 {} 취급 → M_16 기존 동작과 동일
            self._prompt_provider = prompt_provider
            self._pending_tasks: set[asyncio.Task[None]] = set()
            # 직전 턴에서 실제 주입한 RAG 문서의 권위 마커 (chat에서 display_text에 부착)
            self._last_cited_markers: list[str] = []
            # M_16: 직전 분류 결과 캐시 (턴 단위, 동일 턴 내 재분류 금지)
            self._last_routing: Any = None  # RoutingDecision | None

        @staticmethod
        def _should_trigger_rag(text: str) -> bool:
            """질문·검색 의도 키워드가 포함돼 있으면 True 반환.

            '안녕?'처럼 단순 인사나 지시어에는 RAG를 실행하지 않도록
            정보 탐색 의도가 명확한 표현만 검사한다.
            """
            return bool(_RAG_TRIGGER_RE.search(text))

        @staticmethod
        def _extract_attached_doc_ids(text: str) -> list[str]:
            """사용자 메시지 prefix `[첨부 자료: filename (doc_id: xxx); ...]`에서 doc_id 추출."""
            ids: list[str] = []
            import re as _re

            for m in _re.finditer(r"doc_id:\s*([^\s\)\];,]+)", text):
                doc_id = m.group(1).strip().rstrip(")]")
                if doc_id and doc_id not in ids:
                    ids.append(doc_id)
            return ids

        def _fetch_attached_chunks(
            self, doc_ids: list[str], per_doc_limit: int = 5
        ) -> list[dict[str, Any]]:
            """첨부 doc_id들의 청크를 처음 N개씩 가져와 LLM 컨텍스트로 합친다."""
            if not doc_ids:
                return []
            store = getattr(self._rag_service, "_store", None) or getattr(
                self._rag_service, "store", None
            )
            if store is None or not hasattr(store, "get_chunks_by_doc_id"):
                return []
            chunks: list[dict[str, Any]] = []
            for doc_id in doc_ids:
                try:
                    rows = store.get_chunks_by_doc_id(doc_id, limit=per_doc_limit)
                except Exception as exc:
                    logger.warning("첨부 청크 조회 실패 (doc_id=%s): %s", doc_id, exc)
                    continue
                for row in rows:
                    chunks.append(
                        {
                            "doc_id": row.get("doc_id"),
                            "doc_name": row.get("doc_name"),
                            "page": row.get("page"),
                            "text": row.get("text", ""),
                            "score": 1.0,  # 첨부는 직접 지정이라 최고 점수 부여
                            "is_note": row.get("category") == "__knowledge__",
                        }
                    )
            return chunks

        async def _augment_with_rag(self, input_data: BatchInput) -> BatchInput:
            """사용자 메시지를 RAG 결과로 증강.

            - 첨부 doc_id가 있으면 그 청크를 무조건 컨텍스트로 prepend.
            - 추가로 트리거 키워드가 있으면 일반 RAG 검색도 함께 주입.
            """
            # 이번 턴 인용 마커 초기화 (RAG 미주입 시 빈 채로 유지)
            self._last_cited_markers = []
            if self._rag_service is None:
                # RAG 서비스 없어도 tool_hint·answer_guide는 주입해야 한다 (M_17)
                prepend_no_svc: list[Any] = []
                if self._last_routing is not None and self._last_routing.tool_hint:
                    prepend_no_svc.append(
                        TextData(
                            source=TextSource.INPUT,
                            content="[지시] " + self._last_routing.tool_hint,
                            from_name="의도게이트",
                        )
                    )
                if self._last_routing is not None and self._last_routing.answer_guide:
                    prepend_no_svc.append(
                        TextData(
                            source=TextSource.INPUT,
                            content="[작성 지침] " + self._last_routing.answer_guide,
                            from_name="작성지침",
                        )
                    )
                if prepend_no_svc:
                    return BatchInput(
                        texts=prepend_no_svc + list(input_data.texts or []),
                        images=input_data.images,
                        metadata=input_data.metadata,
                    )
                return input_data

            # 사용자 메시지 텍스트 추출
            user_text = " ".join(
                t.content for t in (input_data.texts or []) if t.source == TextSource.INPUT
            ).strip()
            if not user_text:
                return input_data

            # 첨부 doc_id 추출 (트리거 키워드 무관 — 첨부 있으면 무조건 내용 주입)
            attached_doc_ids = self._extract_attached_doc_ids(user_text)
            attached_chunks: list[dict[str, Any]] = []
            if attached_doc_ids:
                attached_chunks = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._fetch_attached_chunks(attached_doc_ids, per_doc_limit=5),
                )
                logger.info(
                    "첨부 청크 자동 주입: doc_ids=%s, chunks=%d",
                    attached_doc_ids,
                    len(attached_chunks),
                )

            # ── M_16 변경 3: decision에 따라 should_search 결정 ─────────────
            if self._last_routing is not None and not self._last_routing.autonomous:
                should_search = self._last_routing.inject_rag  # 게이트 결정 우선
            else:
                should_search = self._should_trigger_rag(user_text)  # 폴백: 레거시 키워드

            if not should_search and not attached_chunks:
                logger.debug("RAG 스킵: 트리거 키워드·첨부 없음 (query=%r)", user_text[:50])
                # ── M_16 변경 5 (RAG 미주입 시): tool_hint 삽입 ────────────
                # ── M_17 (RAG 미주입 시): answer_guide 삽입 ──────────────────
                prepend_no_rag: list[Any] = []
                if self._last_routing is not None and self._last_routing.tool_hint:
                    hint_td = TextData(
                        source=TextSource.INPUT,
                        content="[지시] " + self._last_routing.tool_hint,
                        from_name="의도게이트",
                    )
                    prepend_no_rag.append(hint_td)
                if self._last_routing is not None and self._last_routing.answer_guide:
                    guide_td = TextData(
                        source=TextSource.INPUT,
                        content="[작성 지침] " + self._last_routing.answer_guide,
                        from_name="작성지침",
                    )
                    prepend_no_rag.append(guide_td)
                if prepend_no_rag:
                    new_texts = prepend_no_rag + list(input_data.texts or [])
                    return BatchInput(
                        texts=new_texts,
                        images=input_data.images,
                        metadata=input_data.metadata,
                    )
                return input_data

            try:
                # ── M_16 변경 4: rag_source를 retrieve에 전달 ──────────────
                rag_source = (
                    self._last_routing.rag_source
                    if (self._last_routing is not None and not self._last_routing.autonomous)
                    else "both"
                )

                # 첨부 있어도 검색은 트리거 키워드 있을 때만
                retrieval = None
                if should_search:
                    retrieval = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._rag_service.retrieve(user_text, 5, source=rag_source),
                    )

                # 일반 검색 hits (score 임계값 통과한 것만)
                search_hits: list[dict[str, Any]] = []
                if retrieval is not None and retrieval.found and retrieval.hits:
                    search_hits = [
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

                # 첨부 청크 + 검색 hits 머지 (첨부가 앞 — 우선순위)
                # 중복 제거: 같은 (doc_id, text)는 한 번만
                seen: set[tuple[Any, str]] = set()
                merged: list[dict[str, Any]] = []
                for src in (attached_chunks, search_hits):
                    for h in src:
                        key = (h.get("doc_id"), (h.get("text") or "")[:80])
                        if key in seen:
                            continue
                        seen.add(key)
                        merged.append(h)

                if not merged:
                    logger.debug("RAG 스킵: 첨부·검색 결과 모두 없음 (query=%r)", user_text[:50])
                    return input_data

                context_text = _format_rag_context(merged)

                # 실제 주입한 문서의 권위 마커를 기록 → chat()이 display_text에 부착.
                # LLM이 마커를 빠뜨리거나 깨뜨려도 다운로드 칩이 정확히 렌더된다.
                markers: list[str] = []
                for h in merged:
                    mk = _marker_for_hit(h)
                    if mk and mk not in markers:
                        markers.append(mk)
                self._last_cited_markers = markers

                logger.info(
                    "RAG 컨텍스트 주입: 첨부 청크=%d, 검색 hits=%d, 인용 마커=%d (query=%r)",
                    len(attached_chunks),
                    len(search_hits),
                    len(markers),
                    user_text[:50],
                )

                # ── M_16 변경 5: tool_hint를 RAG 컨텍스트보다 앞에 삽입 ───────
                # ── M_17: answer_guide를 tool_hint 다음, RAG 컨텍스트 앞에 삽입 ─
                # 순서 고정: [tool_hint?] [answer_guide?] [RAG 컨텍스트] [원본 사용자 메시지...]
                prepend_texts: list[Any] = []
                if self._last_routing is not None and self._last_routing.tool_hint:
                    hint_td = TextData(
                        source=TextSource.INPUT,
                        content="[지시] " + self._last_routing.tool_hint,
                        from_name="의도게이트",
                    )
                    prepend_texts.append(hint_td)

                # M_17: answer_guide prepend (tool_hint 다음, RAG 컨텍스트 앞)
                if self._last_routing is not None and self._last_routing.answer_guide:
                    guide_td = TextData(
                        source=TextSource.INPUT,
                        content="[작성 지침] " + self._last_routing.answer_guide,
                        from_name="작성지침",
                    )
                    prepend_texts.append(guide_td)

                # 컨텍스트를 사용자 메시지 앞에 TextSource.INPUT으로 삽입
                context_td = TextData(
                    source=TextSource.INPUT,
                    content=context_text,
                    from_name="문서검색",
                )
                prepend_texts.append(context_td)
                new_texts = prepend_texts + list(input_data.texts or [])
                return BatchInput(
                    texts=new_texts,
                    images=input_data.images,
                    metadata=input_data.metadata,
                )

            except Exception as exc:
                logger.warning("Proactive RAG 실패 (무시): %s", exc)
                return input_data

        async def chat(self, input_data: BatchInput) -> AsyncIterator[Any]:
            """GemmaChatAgent.chat를 소비해 upstream SentenceOutput 스트림으로 변환.

            - M_16 IntentGate: 진입 직후 의도 분류 1회 (캐시, 턴 단위)
            - Proactive RAG: 트리거 키워드 감지 시 자동 검색 후 컨텍스트 주입
            - TextChunk 누적 → 전체 텍스트를 하나의 SentenceOutput으로 yield
            - ToolCallStart/Result → dict yield (upstream이 JSON으로 전송)
            - EndOfTurn → 스트림 종료
            - AgentError → 에러 텍스트를 SentenceOutput으로 yield
            """
            from open_llm_vtuber.agent.output_types import SentenceOutput, DisplayText, Actions

            # ── M_16 변경 2: chat() 진입 직후 1회 분류 ──────────────────────
            user_text_for_classify = " ".join(
                t.content for t in (input_data.texts or []) if t.source == TextSource.INPUT
            ).strip()
            has_attachment = "[첨부 자료:" in user_text_for_classify

            if self._intent_classifier is not None and user_text_for_classify.strip():
                try:
                    from intent_gate import decide_with_confidence

                    _result = await self._intent_classifier.classify(
                        user_text_for_classify, has_attachment=has_attachment
                    )
                    # confidence_threshold는 classifier에서 접근
                    _threshold = getattr(self._intent_classifier, "_confidence_threshold", 0.55)
                    _legacy = self._should_trigger_rag(user_text_for_classify)
                    # M_17: prompt_provider lazy 조회 (None이면 {} = 미주입)
                    _overrides: Mapping[str, str] | None = None
                    if self._prompt_provider is not None:
                        try:
                            _overrides = self._prompt_provider()
                        except Exception as _prov_exc:
                            logger.warning("prompt_provider 조회 실패 (무시): %s", _prov_exc)
                    _decision = decide_with_confidence(
                        _result,
                        confidence_threshold=_threshold,
                        legacy_rag_triggered=_legacy,
                        prompt_overrides=_overrides,
                    )
                    self._last_routing = _decision
                    logger.info(
                        "IntentGate: intent=%s conf=%.2f source=%s "
                        "inject_rag=%s rag_source=%s autonomous=%s",
                        _result.intent,
                        _result.confidence,
                        _result.source,
                        _decision.inject_rag,
                        _decision.rag_source,
                        _decision.autonomous,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:
                    logger.warning("IntentGate 분류 실패 (무시): %s", _exc)
                    self._last_routing = None
            else:
                self._last_routing = None

            # Proactive RAG 적용 (M_16 변경 3~5는 _augment_with_rag 내부에서 처리)
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
                # LLM이 낸 (깨졌을 수 있는) 마커 제거 후, 백엔드가 기록한
                # 권위 마커를 display_text에만 부착 (tts_text는 마커 없이 깨끗하게).
                clean_text = _strip_llm_markers(full_text)
                markers = getattr(self, "_last_cited_markers", []) or []
                if markers:
                    marker_str = "".join(markers)
                    display = f"{clean_text} {marker_str}".strip() if clean_text else marker_str
                else:
                    display = clean_text
                yield SentenceOutput(
                    display_text=DisplayText(text=display, name="AI"),
                    tts_text=clean_text,
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

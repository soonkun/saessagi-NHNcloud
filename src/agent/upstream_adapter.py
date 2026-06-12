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


def _status_event(text: str) -> dict[str, Any]:
    """프론트 진행 상태 말풍선용 이벤트.

    upstream single_conversation은 type=="tool_call_status"인 dict만 WS로 중계하므로
    그 채널을 재사용한다. tool_name="_agent_status"로 실제 도구 이벤트와 구분.
    """
    return {
        "type": "tool_call_status",
        "status": "running",
        "tool_id": "agent-status",
        "tool_name": "_agent_status",
        "content": text,
    }


# 도구 호출 시작 → 진행 상태 문구 (save_knowledge_note는 별도 안내 메시지 경로 유지)
_TOOL_STATUS_TEXT: dict[str, str] = {
    "search_docs": "문서를 검색하고 있어요…",
    "create_meeting_minutes": "회의록을 만들고 있어요…",
    "add_event": "일정을 등록하고 있어요…",
    "get_events": "일정을 확인하고 있어요…",
    "take_screenshot": "화면을 확인하고 있어요…",
}

# 의도분류 직후 즉시 캐릭터 상태 전환 + 안내음 (고신뢰 분류에만, 사용자 요청 2026-06-12).
# (감정 태그, 안내 멘트) — 턴 종료 시 [neutral]로 복귀한다.
_INTENT_ANNOUNCE: dict[str, tuple[str, str]] = {
    "note_save": ("note_writing", "업무 노트를 작성할게요!"),
    "doc_query": ("study", "자료를 찾아볼게요!"),
    "work_query": ("study", "확인해 볼게요!"),
    "calendar_add": ("writing", "일정을 등록할게요!"),
    "calendar_query": ("thinking", "일정을 확인해 볼게요!"),
}


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


def _extract_attached_doc_ids(text: str) -> list[str]:
    """사용자 메시지 prefix `[첨부 자료: filename (doc_id: xxx); ...]`에서 doc_id 추출.

    doc_id는 원본 파일명 기반이라 공백을 포함할 수 있다 — 닫는 괄호(`)`) 전까지
    전부 캡처해야 한다. 공백에서 끊으면 'AI 이삭이 ... .pptx_xxxx'가 'AI'로
    잘려 첨부 청크 주입이 0건이 된다 (E-44).
    """
    ids: list[str] = []
    for m in re.finditer(r"doc_id:\s*([^)\]]+)", text):
        doc_id = m.group(1).strip()
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
    return ids


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
            tts_brief_enabled: bool = True,
            tts_brief_max_chars: int = 80,
            tool_router: Any = None,  # ToolRouter | None — note_save 강제 폴백용 (E-45)
        ) -> None:
            super().__init__()
            self._agent = agent
            self._rag_service = rag_service  # vector_search.RagService | None
            self._intent_classifier = intent_classifier  # M_16: IntentClassifier | None
            # M_17: lazy 지침 조회 클로저. None이면 {} 취급 → M_16 기존 동작과 동일
            self._prompt_provider = prompt_provider
            # 긴 답변은 전체 낭독 대신 완료 멘트만 말한다 (사용자 요청 2026-06-11)
            self._tts_brief_enabled = tts_brief_enabled
            self._tts_brief_max_chars = tts_brief_max_chars
            # E-45: LLM이 도구 호출을 건너뛴 note_save 턴의 강제 저장 경로
            self._tool_router = tool_router
            self._pending_tasks: set[asyncio.Task[None]] = set()
            # 직전 턴에서 실제 주입한 RAG 문서의 권위 마커 (chat에서 display_text에 부착)
            self._last_cited_markers: list[str] = []
            # M_16: 직전 분류 결과 캐시 (턴 단위, 동일 턴 내 재분류 금지)
            self._last_routing: Any = None  # RoutingDecision | None
            # E-45: 직전 분류의 intent 라벨 (RoutingDecision에는 없음)
            self._last_intent: str | None = None

        @staticmethod
        def _should_trigger_rag(text: str) -> bool:
            """질문·검색 의도 키워드가 포함돼 있으면 True 반환.

            '안녕?'처럼 단순 인사나 지시어에는 RAG를 실행하지 않도록
            정보 탐색 의도가 명확한 표현만 검사한다.
            """
            return bool(_RAG_TRIGGER_RE.search(text))

        @staticmethod
        def _extract_attached_doc_ids(text: str) -> list[str]:
            """모듈 레벨 _extract_attached_doc_ids 위임 (테스트 용이성)."""
            return _extract_attached_doc_ids(text)

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
                    lambda: self._fetch_attached_chunks(attached_doc_ids, per_doc_limit=30),
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

            # 진행 상태 1: 의도 분류는 LLM 1회 호출(~1초)이라 먼저 알린다
            yield _status_event("질문을 살펴보고 있어요…")

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
                    self._last_intent = _result.intent
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
                    self._last_intent = None
            else:
                self._last_routing = None
                self._last_intent = None

            # 의도분류 직후 즉시 캐릭터 전환 + 안내음 (고신뢰 분류에만) —
            # 작업이 끝나면 마지막 메시지의 [neutral] 태그로 복귀한다.
            announced = False
            if (
                self._last_intent in _INTENT_ANNOUNCE
                and self._last_routing is not None
                and not self._last_routing.autonomous
            ):
                _emo, _announce_msg = _INTENT_ANNOUNCE[self._last_intent]
                announced = True
                yield SentenceOutput(
                    display_text=DisplayText(text=f"[{_emo}] {_announce_msg}", name="AI"),
                    tts_text=_announce_msg,
                    actions=Actions(),
                )

            # 진행 상태 2: 문서 검색이 일어날 턴이면 알린다 (_augment 내부 조건과 동일 취지)
            will_search = self._rag_service is not None and (
                has_attachment
                or (
                    self._last_routing is not None
                    and not self._last_routing.autonomous
                    and self._last_routing.inject_rag
                )
                or (
                    (self._last_routing is None or self._last_routing.autonomous)
                    and self._should_trigger_rag(user_text_for_classify)
                )
            )
            if will_search:
                yield _status_event("관련 문서를 찾아보고 있어요…")

            # Proactive RAG 적용 (M_16 변경 3~5는 _augment_with_rag 내부에서 처리)
            input_data = await self._augment_with_rag(input_data)

            # 진행 상태 3: LLM 생성 시작 — 가장 긴 구간
            yield _status_event("답변을 작성하고 있어요…")

            text_parts: list[str] = []
            note_call_started = False  # save_knowledge_note 호출 시작 여부
            note_saved = False  # 이번 턴에 업무 노트 저장이 "성공"했는가 (E-46: 결과 기준)

            async for event in self._agent.chat(input_data):
                if isinstance(event, TextChunk):
                    if event.text:
                        text_parts.append(event.text)
                elif isinstance(event, ToolCallStart):
                    # 진행 상태: 도구 실행 단계를 알린다
                    if event.name in _TOOL_STATUS_TEXT:
                        yield _status_event(_TOOL_STATUS_TEXT[event.name])
                    yield {
                        "type": "tool_call_start",
                        "tool_id": event.tool_id,
                        "name": event.name,
                        "arguments": event.arguments,
                    }
                    # 업무 노트 저장은 생성+RAG 임베딩으로 시간이 걸리므로 시작 시
                    # "작성 중" 안내를 먼저 보낸다(완료 메시지는 임베딩 후). display_text의
                    # [note_writing] 태그 → 프론트가 작성 중 캐릭터로 전환(대화 채널로 확실히
                    # 전달; tts에는 태그 미포함). 완료 메시지에 [neutral]로 복귀.
                    if event.name == "save_knowledge_note":
                        note_call_started = True
                        # 의도분류 시점에 이미 안내했으면 중복 안내 생략
                        if not announced:
                            _msg = "📝 업무 노트를 작성하고 있어요. 잠시만요…"
                            yield SentenceOutput(
                                display_text=DisplayText(
                                    text=f"[note_writing] {_msg}", name="AI"
                                ),
                                tts_text=_msg,
                                actions=Actions(),
                            )
                elif isinstance(event, ToolCallResult):
                    # E-46: 저장 "성공"을 결과 기준으로 추적 — 호출했지만 검증 실패한
                    # 턴(예: title 누락 → invalid_arguments)도 폴백 대상이 되도록.
                    if event.name == "save_knowledge_note" and event.ok:
                        note_saved = True
                    yield {
                        "type": "tool_call_status",
                        "status": "completed" if event.ok else "error",
                        "tool_id": event.tool_id,
                        "tool_name": event.name,
                        "content": event.content,
                    }
                    # 도구 완료 → LLM이 결과를 정리하는 구간으로 복귀
                    yield _status_event("답변을 작성하고 있어요…")
                elif isinstance(event, EndOfTurn):
                    break
                elif isinstance(event, AgentError):
                    logger.warning(f"AgentError를 텍스트로 변환: code={event.code}")
                    text_parts.append(f"[오류: {event.message}]")

            full_text = "".join(text_parts)

            # E-45/E-46: 고신뢰 note_save 턴인데 "성공한" 저장이 없으면 강제 저장 예정.
            # (미호출뿐 아니라 호출 후 invalid_arguments 등으로 실패한 경우 포함)
            will_force_save = (
                not note_saved
                and self._tool_router is not None
                and self._last_intent == "note_save"
                and self._last_routing is not None
                and not self._last_routing.autonomous
            )

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
                # 의도 안내·노트 작성으로 캐릭터 상태를 바꿨다면 작업 종료 시 [neutral] 복귀.
                # 단, 직후 강제 저장 폴백이 돌 예정이면 폴백 완료 메시지가 복귀를 담당한다.
                if (announced or note_call_started) and not will_force_save:
                    display = f"[neutral] {display}"

                # 긴 답변은 전체 낭독 대신 완료 멘트만 (짧은 답변·인사는 그대로 읽음).
                # 본문은 채팅창에 그대로 표시되므로 사용자가 직접 읽는다.
                tts_out = clean_text
                if self._tts_brief_enabled and len(clean_text) > self._tts_brief_max_chars:
                    if note_saved:
                        tts_out = "업무 노트 저장이 완료되었어요. 내용을 확인해 주세요."
                    elif markers:
                        tts_out = "자료 확인이 완료되었어요. 내용을 확인해 주세요."
                    else:
                        tts_out = "답변 작성이 완료되었어요. 내용을 확인해 주세요."
                    logger.debug("TTS 요약 모드: 본문 %d자 → 완료 멘트로 대체", len(clean_text))

                yield SentenceOutput(
                    display_text=DisplayText(text=display, name="AI"),
                    tts_text=tts_out,
                    actions=Actions(),
                )

            # ── E-45: note_save 의도인데 LLM이 도구 호출을 건너뛴 턴 — 강제 저장 ──
            # gemma가 "노트로 저장해 두었어요"라고 말만 하고 save_knowledge_note를
            # 호출하지 않는 환각이 발생한다 (가짜 '[생성된 노트 요약]'까지 출력).
            # 게이트가 고신뢰(autonomous=False)로 note_save로 분류한 턴은 백엔드가
            # 저장을 보장한다.
            if will_force_save:
                logger.warning(
                    "note_save 의도였으나 저장 성공 없음(미호출 또는 호출 실패) — "
                    "강제 저장 폴백 (E-45/E-46)"
                )
                yield _status_event("업무 노트를 저장하고 있어요…")
                fallback_out: Any = None
                try:
                    fallback_out = await self._force_save_note(user_text_for_classify, full_text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("노트 강제 저장 폴백 실패 (무시): %s", exc)
                if fallback_out is not None:
                    yield fallback_out

        async def _force_save_note(self, user_text: str, reply_text: str) -> Any:
            """LLM이 도구 호출 없이 끝낸 note_save 턴의 강제 노트 저장 (E-45).

            이번 턴 답변(reply_text)에는 첨부 자료 내용이 이미 반영돼 있으므로,
            그것을 노트 JSON으로 변환해 ToolRouter로 직접 저장한다.
            성공 시 사용자에게 보낼 SentenceOutput, 실패 시 None 반환.
            """
            from open_llm_vtuber.agent.output_types import Actions, DisplayText, SentenceOutput

            schema: dict[str, Any] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "summary", "tags"],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 80},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 7000},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 30},
                        "maxItems": 3,
                    },
                },
            }
            reply_core = _strip_llm_markers(reply_text)[:4000]
            raw = await self._agent.complete_json(
                system_prompt=(
                    "사용자의 업무 보고와 비서가 작성한 정리 답변을 업무 노트 JSON으로 변환합니다. "
                    "summary는 한국어 markdown으로 '## 상황 / ## 절차 / ## 사용 자료 / ## 교훈' "
                    "구조를 따르고, 답변에 있는 수치·날짜·결정 사항을 빠짐없이 보존하세요. "
                    "JSON만 출력하세요."
                ),
                user_prompt=(
                    f"사용자 보고:\n'''\n{user_text[:1500]}\n'''\n\n"
                    f"비서가 작성한 정리 답변:\n'''\n{reply_core}\n'''"
                ),
                json_schema=schema,
                max_tokens=2048,
                temperature=0.2,
                timeout_seconds=120.0,
            )
            args: dict[str, Any] = {
                "title": (str(raw.get("title", "")).strip() or "업무 노트")[:100],
                "summary": str(raw.get("summary", "")).strip()[:8000],
                "tags": [str(t)[:30] for t in (raw.get("tags") or []) if str(t).strip()][:3],
                "related_docs": _extract_attached_doc_ids(user_text),
            }
            if not args["summary"]:
                logger.warning("강제 저장 폴백: summary 생성 실패 — 중단")
                return None
            result = await self._tool_router.dispatch("save_knowledge_note", args)
            if not getattr(result, "ok", False):
                logger.warning("강제 저장 폴백: dispatch 실패 — %s", getattr(result, "error", None))
                return None
            payload = result.payload or {}
            slug = payload.get("slug", "")
            title = payload.get("title", args["title"])
            marker = payload.get("note_marker", f"[[note:{slug}]]" if slug else "")
            logger.info("노트 강제 저장 폴백 성공: slug=%s (E-45)", slug)
            msg = f"업무 노트 '{title}'(으)로 저장해 두었어요."
            return SentenceOutput(
                display_text=DisplayText(text=f"[neutral] {msg} {marker}".strip(), name="AI"),
                tts_text=msg,
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

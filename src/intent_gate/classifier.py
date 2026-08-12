# src/intent_gate/classifier.py
"""M_16 IntentGate 분류기."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from typing import cast

from .prompts import FILTER_GUIDE, INTENT_JSON_SCHEMA, SYSTEM_PROMPT
from .types import (
    ALL_INTENT_LABELS,
    DOCUMENT_TYPES,
    ENTITY_TYPES,
    CompleteJsonFn,
    IntentLabel,
    IntentResult,
    RetrievalFilter,
)

logger = logging.getLogger(__name__)

# 분류 응답 토큰 예산.
# 출력 JSON 자체는 60토큰이면 충분하지만, 요즘 소형 모델(gemma4:e4b 등)은 추론(thinking)
# 토큰을 먼저 쓴다. 예산이 빠듯하면 추론에서 다 소진해 **빈 응답**이 오고
# (finish_reason=length), 분류가 조용히 fallback_error로 떨어져 RAG 라우팅이 사라진다.
# 실측(gemma4:e4b): 64·512토큰 → 빈 응답, 1024토큰 → 정상. 헤드룸을 넉넉히 둔다
# (E-41과 같은 함정). max_tokens는 상한일 뿐이라 모델이 일찍 끝나면 비용이 늘지 않는다.
_CLASSIFY_MAX_TOKENS = 1024


class IntentClassifier:
    """LLM 기반 의도 분류기.

    complete_json을 DI로 주입받아 사용하므로 메인 대화 모델과 다른
    분류기 전용 모델/클라이언트를 연결할 수 있다.

    classify()는 항상 IntentResult를 반환하며 CancelledError 외의
    예외를 외부로 전파하지 않는다.
    """

    def __init__(
        self,
        complete_json: CompleteJsonFn,
        *,
        model_label: str,
        confidence_threshold: float = 0.55,
        timeout_seconds: float = 8.0,
        max_input_chars: int = 4000,
        system_prompt_override: str | None = None,
    ) -> None:
        self._complete_json = complete_json
        self._model_label = model_label
        self._confidence_threshold = confidence_threshold
        self._timeout_seconds = timeout_seconds
        self._max_input_chars = max_input_chars
        # M_17: 커스텀 SYSTEM_PROMPT (None이면 기본 SYSTEM_PROMPT 사용)
        # INTENT_JSON_SCHEMA·few-shot은 변경 불가 — 코드가 항상 강제 결합
        self._system_prompt_override = system_prompt_override
        logger.info(
            "IntentClassifier 초기화: model=%s, threshold=%.2f, timeout=%.1fs, custom_prompt=%s",
            model_label,
            confidence_threshold,
            timeout_seconds,
            "yes" if system_prompt_override else "no",
        )

    async def classify(
        self,
        user_text: str,
        *,
        has_attachment: bool = False,
        prev_context: str | None = None,
    ) -> IntentResult:
        """사용자 발화를 분류해 IntentResult를 반환한다.

        항상 IntentResult 반환. CancelledError 제외한 예외는 fallback_error로 처리.

        Args:
            user_text: 분류할 사용자 입력 텍스트.
            has_attachment: 메시지에 [첨부 자료: ...] 메타 존재 여부.
            prev_context: 직전 대화 요약(사용자 질문 + 답변 앞부분). followup 판정에 쓴다.
                없으면 모델에게 "직전 대화 없음"을 알려 followup을 고르지 않게 한다.

        Returns:
            IntentResult. source는 "llm", "fallback_error", "fallback_lowconf" 중 하나.
        """
        # 입력 길이 제한 — max_input_chars 초과 시 앞부분만 사용
        truncated = user_text[: self._max_input_chars]

        # has_attachment 힌트 추가
        attachment_hint = ""
        if has_attachment:
            attachment_hint = (
                "\n[참고: 이 메시지에는 첨부 자료가 포함되어 있습니다 — note_save 가능성 고려]"
            )

        # 직전 대화를 함께 준다 — 이게 없으면 모델은 "짧게 정리해줘"가 무엇을 가리키는지
        # 알 수 없어 followup 판정을 할 수 없다 (CR-51).
        if prev_context:
            context_block = f"[직전 대화]\n{prev_context[: self._max_input_chars]}\n\n[현재 발화]\n"
        else:
            context_block = "[직전 대화 없음 — followup 라벨을 고르지 마세요]\n\n[현재 발화]\n"

        user_prompt = f"{context_block}{truncated}{attachment_hint}"

        # M_17: system_prompt_override가 있으면 그것을 사용, 없으면 기본값
        # INTENT_JSON_SCHEMA(6 enum)는 항상 코드가 강제로 전달 (편집 불가)
        active_system_prompt = (
            self._system_prompt_override if self._system_prompt_override else SYSTEM_PROMPT
        )
        # CR-72: 검색 제약 지침은 **커스텀 지침에도 항상 붙인다.** 사용자가 지침 화면에서
        # 이미 저장해 둔 프롬프트에는 이 문구가 없어, 안 붙이면 필터가 영영 안 나온다.
        if "retrieval_filter" not in active_system_prompt:
            active_system_prompt = active_system_prompt + "\n" + FILTER_GUIDE

        try:
            async with asyncio.timeout(self._timeout_seconds + 1.0):
                raw: dict[str, Any] = await self._complete_json(
                    active_system_prompt,
                    user_prompt,
                    INTENT_JSON_SCHEMA,
                    max_tokens=_CLASSIFY_MAX_TOKENS,
                    temperature=0.0,
                    timeout_seconds=self._timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning(
                "IntentClassifier.classify 타임아웃 (fallback_error): model=%s, timeout=%.1fs",
                self._model_label,
                self._timeout_seconds,
            )
            return IntentResult(
                intent="chat",
                confidence=0.0,
                reason=f"타임아웃: {type(exc).__name__}",
                source="fallback_error",
            )
        except Exception as exc:
            logger.warning(
                "IntentClassifier.classify 실패 (fallback_error): model=%s, error=%s",
                self._model_label,
                exc,
            )
            return IntentResult(
                intent="chat",
                confidence=0.0,
                reason=f"분류 실패: {type(exc).__name__}",
                source="fallback_error",
            )

        return self._parse_result(raw)

    def _parse_result(self, raw: dict[str, Any]) -> IntentResult:
        """LLM 응답 dict를 IntentResult로 변환.

        파싱 규칙 (스펙 §structured output):
        - intent가 6개 라벨 외 → chat 강등, source 유지 ("llm")
        - confidence가 숫자 아니거나 범위 밖 → 0.0으로 clamp
        - 저신뢰 (비-RAG 라벨) → source="fallback_lowconf"
        """
        # ── intent 파싱 ──────────────────────────────────────────────────────
        raw_intent = raw.get("intent", "")
        if raw_intent in ALL_INTENT_LABELS:
            intent: IntentLabel = cast(IntentLabel, raw_intent)
        else:
            logger.warning("IntentClassifier: intent 라벨 외 값 '%s' → chat 강등", raw_intent)
            intent = "chat"

        # ── confidence 파싱 ──────────────────────────────────────────────────
        raw_confidence = raw.get("confidence", 0.0)
        try:
            confidence = float(raw_confidence)
            # 범위 clamp
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            logger.warning("IntentClassifier: confidence 파싱 실패 ('%s') → 0.0", raw_confidence)
            confidence = 0.0

        # ── reason 파싱 ──────────────────────────────────────────────────────
        reason = str(raw.get("reason", ""))[:200]

        # ── needs_search 파싱 (CR-52) ────────────────────────────────────────
        # 모델이 빠뜨렸을 때의 기본값은 라벨로 정한다 — 문서 질의 계열은 검색이 기본이고,
        # 나머지는 검색하지 않는 편이 안전하다(엉뚱한 청크 주입 방지).
        raw_needs = raw.get("needs_search")
        if isinstance(raw_needs, bool):
            needs_search = raw_needs
        else:
            needs_search = intent in ("doc_query", "work_query")

        # ── retrieval_filter 파싱 (CR-72) ────────────────────────────────────
        retrieval_filter = _parse_filter(raw.get("retrieval_filter"))

        # ── source 결정 ──────────────────────────────────────────────────────
        # 저신뢰 판정: doc_query/work_query는 별도(소스 폴백, autonomous=False)
        # → 여기서는 source="llm"으로 두고, decide()에서 처리
        # 비-RAG 라벨(calendar_add, calendar_query, note_save, chat)에서 저신뢰이면
        # source="fallback_lowconf"
        if confidence < self._confidence_threshold and intent not in ("doc_query", "work_query"):
            logger.info(
                "IntentClassifier: intent=%s, conf=%.2f < threshold=%.2f → fallback_lowconf",
                intent,
                confidence,
                self._confidence_threshold,
            )
            return IntentResult(
                intent=intent,
                confidence=confidence,
                reason=reason,
                source="fallback_lowconf",
                needs_search=needs_search,
                retrieval_filter=retrieval_filter,
            )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            reason=reason,
            source="llm",
            needs_search=needs_search,
            retrieval_filter=retrieval_filter,
        )


def _parse_filter(raw: Any) -> RetrievalFilter | None:
    """모델이 낸 `retrieval_filter`를 검증해 담는다 (CR-72).

    **이상한 값은 통과시키지 않는다.** 분류기는 경량 모델(`intent_gate.ollama_model`)이라
    필드를 빠뜨리거나 엉뚱한 값을 낼 수 있다. 못 믿을 값은 버리고 `None`(제약 없음)으로
    떨어뜨린다 — 기본이 안전한 쪽이어야 한다. 제약을 잘못 걸면 멀쩡한 자료가 사라진다.

    `recent_years`는 여기서 **오늘 연도 기준으로 환산**한다. 모델에게 날짜 산술을
    시키면 틀린다.
    """
    if not isinstance(raw, dict):
        return None

    def _int(key: str) -> int | None:
        v = raw.get(key)
        if isinstance(v, bool) or not isinstance(v, int):
            return None
        return v

    this_year = datetime.now().year
    year_from = _int("year_from")
    year_to = _int("year_to")

    recent = _int("recent_years")
    if recent is not None and 1 <= recent <= 50 and year_from is None:
        # "최근 5년" = 올해 포함 5개 연도 → 2026이면 2022~
        year_from = this_year - recent + 1

    # 오타·헛값(9999)만 버린다.
    #
    # **미래 연도를 버리면 안 된다.** 처음에 `this_year + 1`로 막았더니 "2030년 이후
    # 자료" 질문에서 제약이 통째로 사라져 전체 검색이 돌았다 — 사용자는 걸러진 줄 알고
    # 답을 읽는다. 없는 범위면 0건이 나오고 "그 범위에 자료가 없다"고 답해야 한다.
    # 그게 이 기능의 핵심이다.
    def _sane(y: int | None) -> int | None:
        return y if y is not None and 1900 <= y <= 2100 else None

    year_from, year_to = _sane(year_from), _sane(year_to)
    if year_from and year_to and year_from > year_to:
        logger.warning("IntentGate: 연도 범위가 뒤집혀 무시한다 (%s~%s)", year_from, year_to)
        year_from = year_to = None

    doc_type = raw.get("document_type")
    if doc_type not in DOCUMENT_TYPES:
        doc_type = None

    types_raw = raw.get("entity_types")
    entity_types: tuple[str, ...] = ()
    if isinstance(types_raw, list):
        entity_types = tuple(
            dict.fromkeys(t for t in types_raw if isinstance(t, str) and t in ENTITY_TYPES)
        )

    filt = RetrievalFilter(
        year_from=year_from,
        year_to=year_to,
        document_type=doc_type,
        entity_types=entity_types,
    )
    return None if filt.is_empty else filt

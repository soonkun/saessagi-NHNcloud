# src/intent_gate/classifier.py
"""M_16 IntentGate 분류기."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from typing import cast

from .prompts import INTENT_JSON_SCHEMA, SYSTEM_PROMPT
from .types import ALL_INTENT_LABELS, CompleteJsonFn, IntentLabel, IntentResult

logger = logging.getLogger(__name__)


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
    ) -> IntentResult:
        """사용자 발화를 분류해 IntentResult를 반환한다.

        항상 IntentResult 반환. CancelledError 제외한 예외는 fallback_error로 처리.

        Args:
            user_text: 분류할 사용자 입력 텍스트.
            has_attachment: 메시지에 [첨부 자료: ...] 메타 존재 여부.

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

        user_prompt = f"{truncated}{attachment_hint}"

        # M_17: system_prompt_override가 있으면 그것을 사용, 없으면 기본값
        # INTENT_JSON_SCHEMA(6 enum)는 항상 코드가 강제로 전달 (편집 불가)
        active_system_prompt = (
            self._system_prompt_override if self._system_prompt_override else SYSTEM_PROMPT
        )

        try:
            async with asyncio.timeout(self._timeout_seconds + 1.0):
                raw: dict[str, Any] = await self._complete_json(
                    active_system_prompt,
                    user_prompt,
                    INTENT_JSON_SCHEMA,
                    max_tokens=64,
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
            )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            reason=reason,
            source="llm",
        )

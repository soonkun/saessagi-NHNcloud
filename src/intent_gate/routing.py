# src/intent_gate/routing.py
"""M_16 IntentGate 라우팅 결정 순수 함수."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .types import IntentResult, RagSource

logger = logging.getLogger(__name__)

# ── 후속 질문(follow-up) 감지 (CR-23) ─────────────────────────────────────────
# 직전 답변을 참조하는 후속 질문은 분류·RAG 재검색 없이 대화 맥락으로 처리해야 한다.
# ("그럼 내용을 요약해줘"를 독립 분류하면 doc_query로 빠져 무관한 청크가 주입되는 문제)

# 직전 내용을 가리키는 지시어 — 담화 표지 단독("그럼")은 새 요청 앞에도 붙으므로 제외
_FOLLOWUP_ANAPHORA = re.compile(
    r"(그\s?내용|그\s?결과|그\s?답변|그거|그것|그건|그중|그\s?중에|위\s?내용|위\s?답변|"
    r"위에서|방금|아까|앞서|앞의|이\s?내용|이거를|이걸|이건|거기서|둘\s?중|셋\s?중)"
)
# 재표현 요청 — 짧은 문장이면 거의 항상 직전 답변 대상
_FOLLOWUP_REPHRASE = re.compile(
    r"(요약|정리)\s?해|짧게|간단히|한\s?문장|한\s?줄|표로\s?(만들|정리|보여)|"
    r"다시\s?(설명|말해)|풀어서|쉽게\s?(설명|말해)|번역해"
)
# 주제어 판정 시 걷어낼 군더더기 — 담화 표지·정도 부사·어투. 이것만 남으면 주제가 없는
# 순수 재표현 요청이다.
_FOLLOWUP_FILLER = re.compile(
    r"(그럼|그러면|그리고|이제|일단|좀|더|조금|자세히|자세하게|계속|또|한번|한\s?번|"
    # "내용·부분·항목"은 재표현 요청 안에서는 주제가 아니라 **직전 답변을 가리키는 말**이다.
    # ("내용을 요약해줘"는 새 질문이 아니다)
    r"내용|부분|항목|결과|답변|"
    # 요청 동사·어미의 잔재 — 주제어로 세면 안 된다
    r"보여|알려|말해|설명|만들어|바꿔|해서|하여|해|"
    r"부탁해|부탁드려|해줘|해주세요|해줄래|줘|주세요|해봐|해다오|please)"
)

# 조사만으로 이루어진 토막 — 주제어로 세면 안 된다
_PARTICLE_ONLY = re.compile(r"[을를이가은는에서의으로도만과와랑까지부터]+")

# 새 검색 대상을 특정하는 단서 — 있으면 후속이 아니라 새 질의로 본다
_FOLLOWUP_NEW_TARGET = re.compile(
    r"(문서|보고서|파일|자료|계획서|RFP|규정|노트)에?\s?(에서|의|를|을)?"
)


def residual_topic(text: str) -> str:
    """재표현 요청에서 어투·군더더기를 걷어내고 **남는 주제어**를 돌려준다.

    "짧게 정리해줘"에는 주제가 없지만 "기후변화 대응방안을 정리해줘"에는 있다.
    이 차이를 못 보면 새 질문까지 후속으로 오인해 RAG를 건너뛴다 (E-79).
    """
    t = " ".join((text or "").split())
    t = _FOLLOWUP_REPHRASE.sub(" ", t)  # 요약/정리/표로 … 요청 표현 제거
    t = _FOLLOWUP_FILLER.sub(" ", t)  # 그럼·좀·다시·해줘 같은 군더더기 제거
    # 조사만 남은 토막("을", "으로")을 버린다 — 이것까지 주제로 세면 순수 재표현 요청이
    # 새 질문으로 오인된다. 주제어에 붙어 있는 조사는 길이 신호에 영향이 없어 그냥 둔다.
    kept = [w for w in t.split() if _PARTICLE_ONLY.fullmatch(w) is None]
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", " ".join(kept))


def looks_like_followup(text: str) -> bool:
    """직전 답변을 참조하는 후속 질문인지 판정 (보수적 휴리스틱).

    True 조건:
    1. 내용 지시어(그 내용/그거/방금/아까 등) 포함, 또는
    2. 60자 이하의 재표현 요청이면서 **자체 주제어가 없을 때**
    단, 새 검색 대상(문서/보고서/파일명 등)을 명시하면 False.

    2번의 "주제어가 없을 때" 조건이 핵심이다. 예전에는 짧고 "정리해"가 들어가기만 하면
    후속으로 봤는데, 그러면 "기후변화 대응방안을 정리해줘" 같은 **새 질문까지** 후속으로
    처리해 RAG를 건너뛰고 모델의 일반 지식으로만 답했다 (E-79).
    """
    t = " ".join((text or "").split())
    if not t:
        return False
    if _FOLLOWUP_NEW_TARGET.search(t):
        return False
    if _FOLLOWUP_ANAPHORA.search(t):
        return True
    if len(t) > 60 or _FOLLOWUP_REPHRASE.search(t) is None:
        return False
    # 주제어가 남으면 새 질문이다. 두 글자짜리 찌꺼기에 휘둘리지 않게 3자 이상을 요구한다.
    return len(residual_topic(t)) < 3


def followup_decision() -> "RoutingDecision":
    """후속 질문용 라우팅 — 재검색·도구 유도 없이 대화 맥락(메모리)만으로 답한다."""
    return RoutingDecision(
        inject_rag=False,
        rag_source="both",
        tool_hint=(
            "직전 대화에 이어지는 후속 요청입니다. 새로 검색하지 말고 "
            "바로 위 대화에서 당신이 답한 내용을 대상으로 요청을 수행하세요."
        ),
        autonomous=False,
        answer_guide=None,
    )


# ── tool_hint 문구 상수 ────────────────────────────────────────────────────────

_HINT_CALENDAR_ADD = (
    "사용자가 일정 등록을 요청했습니다. 반드시 add_event 도구를 호출하세요. "
    "시작 시각은 ISO 8601(+09:00)로 변환."
)
_HINT_CALENDAR_QUERY = (
    "사용자가 일정 조회를 요청했습니다. get_events 도구로 해당 기간을 조회하세요."
)
_HINT_DOC_QUERY = (
    "사용자가 사내 공용 문서·규정에 대해 질문했습니다. "
    "주입된 [관련 문서 검색 결과]를 근거로 답하고, 부족하면 search_docs를 호출하세요."
)
_HINT_NOTE_SAVE = (
    "사용자가 처리한 업무를 보고했습니다. save_knowledge_note 도구로 노트를 저장하세요."
)
_HINT_WORK_QUERY = (
    "사용자가 자신의 업무이력(개인 노트)에 대해 질문했습니다. "
    "주입된 [관련 노트 검색 결과]를 근거로 답하고, 부족하면 search_docs를 호출하세요."
)

_TOOL_HINTS: dict[str, str | None] = {
    "calendar_add": _HINT_CALENDAR_ADD,
    "calendar_query": _HINT_CALENDAR_QUERY,
    "doc_query": _HINT_DOC_QUERY,
    "note_save": _HINT_NOTE_SAVE,
    "work_query": _HINT_WORK_QUERY,
    "chat": None,
}


@dataclass(frozen=True)
class RoutingDecision:
    """라우팅 결정 결과."""

    inject_rag: bool  # _augment_with_rag가 벡터 검색·주입을 수행할지
    rag_source: RagSource  # "docs" | "notes" | "both" — retrieve에 넘길 소스 필터
    tool_hint: str | None  # LLM 시스템 메시지에 1줄로 주입할 도구 유도 지시 (None이면 미주입)
    autonomous: bool  # True면 게이트가 강제하지 않고 LLM 자율 (fallback 경로)
    answer_guide: str | None = field(
        default=None
    )  # M_17: 해당 의도의 답변/작성 지침 본문 (빈/None=미주입)


def decide(result: IntentResult, *, legacy_rag_triggered: bool = False) -> RoutingDecision:
    """IntentResult → RoutingDecision 순수 함수.

    스펙 §라우팅 규칙의 매핑 표를 결정론적으로 구현한다.
    threshold 없이 호출 가능한 공개 API — decide_with_confidence(threshold=0.0)에 위임한다
    (즉, confidence 값이 threshold 미만으로 떨어지지 않아 저신뢰 폴백이 발생하지 않음).

    Args:
        result: 분류기가 반환한 IntentResult.
        legacy_rag_triggered: 자율 모드 폴백 시 레거시 키워드 휴리스틱 결과.
            (autonomous=True일 때만 inject_rag에 반영됨)

    Returns:
        RoutingDecision.
    """
    return decide_with_confidence(
        result,
        confidence_threshold=0.0,
        legacy_rag_triggered=legacy_rag_triggered,
    )


def decide_with_confidence(
    result: IntentResult,
    *,
    confidence_threshold: float = 0.55,
    legacy_rag_triggered: bool = False,
    prompt_overrides: Mapping[str, str] | None = None,
) -> RoutingDecision:
    """confidence_threshold를 반영한 확장 라우팅 결정 함수.

    스펙 §저신뢰 폴백 — doc_query/work_query의 저신뢰 소스 폴백 구현.
    M_17: prompt_overrides로 per-intent 답변 지침을 answer_guide에 주입.

    Args:
        result: 분류기가 반환한 IntentResult.
        confidence_threshold: 저신뢰 판정 임계값.
        legacy_rag_triggered: 자율 모드 폴백 시 레거시 키워드 결과.
        prompt_overrides: M_17 — {intent_key: 지침본문} 매핑.
            None이면 M_16 기존 동작과 100% 동일 (answer_guide=None).

    Returns:
        RoutingDecision.
    """

    # M_17: answer_guide 결정 헬퍼
    def _get_answer_guide(intent: str) -> str | None:
        """의도에 따라 해당 지침을 prompt_overrides에서 조회."""
        if prompt_overrides is None:
            return None
        _key_map: dict[str, str] = {
            "doc_query": "doc_query_answer",
            "work_query": "work_query_answer",
            "note_save": "knowledge_note",
        }
        override_key = _key_map.get(intent)
        if override_key is None:
            return None
        val = prompt_overrides.get(override_key) or None
        # 빈 문자열 → None 정규화 (미주입)
        if val is not None and not val.strip():
            return None
        return val

    # 분류기 실패/비활성/비-RAG 저신뢰 → 전면 자율 폴백
    if result.source != "llm":
        logger.debug(
            "IntentGate decide_with_confidence: source=%s → autonomous 폴백",
            result.source,
        )
        return RoutingDecision(
            inject_rag=legacy_rag_triggered,
            rag_source="both",
            tool_hint=None,
            autonomous=True,
            answer_guide=None,
        )

    intent = result.intent

    # followup — 분류기가 "직전 답변을 다듬어 달라"고 판단한 경우 (CR-51).
    # 재검색 없이 대화 맥락만으로 답한다.
    if intent == "followup":
        return followup_decision()

    # doc_query / work_query
    if intent in ("doc_query", "work_query"):
        if result.confidence < confidence_threshold:
            # 소스 저신뢰 폴백: RAG는 켜되 둘 다 검색
            rag_source: RagSource = "both"
            logger.info(
                "IntentGate: intent=%s, conf=%.2f < threshold=%.2f → rag_source=both (소스 폴백)",
                intent,
                result.confidence,
                confidence_threshold,
            )
        else:
            rag_source = "docs" if intent == "doc_query" else "notes"

        hint = _TOOL_HINTS[intent]
        return RoutingDecision(
            inject_rag=True,
            rag_source=rag_source,
            tool_hint=hint,
            autonomous=False,
            answer_guide=_get_answer_guide(intent),
        )

    # 비-RAG 라벨(calendar_add, calendar_query, note_save, chat)
    # 저신뢰이면 전면 자율 폴백
    if result.confidence < confidence_threshold:
        logger.info(
            "IntentGate: intent=%s, conf=%.2f < threshold=%.2f (비-RAG 라벨) → autonomous 폴백",
            intent,
            result.confidence,
            confidence_threshold,
        )
        return RoutingDecision(
            inject_rag=legacy_rag_triggered,
            rag_source="both",
            tool_hint=None,
            autonomous=True,
            answer_guide=None,
        )

    # 고신뢰 라벨 (note_save 포함)
    return RoutingDecision(
        inject_rag=False,
        rag_source="both",
        tool_hint=_TOOL_HINTS[intent],
        autonomous=False,
        answer_guide=_get_answer_guide(intent),
    )

# src/kg/validate.py
"""M_23 추출 결과 검증 (지침서 11장, 스펙 §3 (4)).

**LLM 출력은 반드시 애플리케이션 코드에서 검증한다.** 이 파일이 "근거 없는 노드"를 막는
마지막 방벽이고, 지침서가 최우선 지표로 꼽은 Entity Precision·Evidence Accuracy가 여기서
결정된다.

거르는 것:
- 허용되지 않은 유형·상태·관련성
- 0~1을 벗어난 신뢰도, 임계값 미달
- **원문에 없는 근거** (지침서 11.3 — 가장 중요)
- 빈 이름, 문장처럼 긴 이름, 일반명사, 숫자·연도
- 청크 안 중복, 개수 상한 초과
- 현재 과제와 무관(NONE)한데 저장 대상으로 분류된 것
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .config import (
    NON_ATTRIBUTABLE_STATUSES,
    PROJECT_RELEVANCES,
    STATEMENT_STATUSES,
)
from .prompts import GENERIC_TERMS

logger = logging.getLogger(__name__)

# 거절 사유 (지침서 10장 excluded_candidates.reason과 같은 어휘)
REASON_BAD_TYPE = "BAD_TYPE"
REASON_BAD_STATUS = "BAD_STATUS"
REASON_BAD_RELEVANCE = "BAD_RELEVANCE"
REASON_LOW_CONFIDENCE = "LOW_CONFIDENCE"
REASON_NO_EVIDENCE = "NO_EVIDENCE"
REASON_EVIDENCE_NOT_IN_SOURCE = "EVIDENCE_NOT_IN_SOURCE"
REASON_EMPTY_NAME = "EMPTY_NAME"
REASON_NAME_TOO_LONG = "NAME_TOO_LONG"
REASON_GENERAL_TERM = "GENERAL_TERM"
REASON_NUMERIC_ONLY = "NUMERIC_ONLY"
REASON_DUPLICATE_IN_CHUNK = "DUPLICATE_IN_CHUNK"
REASON_NOT_CURRENT_PROJECT = "NOT_CURRENT_PROJECT"
REASON_OVER_LIMIT = "OVER_LIMIT"

_MAX_NAME_CHARS = 60
_MIN_NAME_CHARS = 2
_NUMERIC_ONLY = re.compile(r"^[\d\s.,%~\-/년월일건개]+$")
_WS = re.compile(r"\s+")


@dataclass
class ValidatedEntity:
    """검증을 통과한 엔티티. 근거는 원문 문장으로 교정돼 있을 수 있다."""

    temp_id: str
    entity_type: str
    name: str
    canonical_name_candidate: str
    description: str
    statement_status: str
    project_relevance: str
    target_terms: list[str]
    specificity: str
    evidence: str
    confidence: float


@dataclass
class RejectedEntity:
    """거절된 후보 — 왜 버렸는지 남긴다. 평가·튜닝의 근거가 된다."""

    name: str
    reason: str
    detail: str = ""


@dataclass
class ValidationResult:
    accepted: list[ValidatedEntity]
    rejected: list[RejectedEntity]

    @property
    def counts(self) -> dict[str, int]:
        by_reason: dict[str, int] = {}
        for r in self.rejected:
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        return {"accepted": len(self.accepted), "rejected": len(self.rejected), **by_reason}


def _norm(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip().casefold()


def _split_sentences(text: str) -> list[str]:
    """근거 대조용 문장 분리. 마침표가 드문 보고서 문체를 고려해 줄바꿈도 경계로 본다."""
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def _coverage(evidence_norm: str, source_norm: str) -> float:
    """근거 문자열 중 원문에 실제로 등장하는 비율.

    문장 전체 유사도로 재면 **원문의 일부만 인용한 정상 근거가 탈락한다** — 긴 문장에서
    한 조각만 따오면 대칭 유사도가 낮게 나오기 때문이다(실측 0.76). LLM은 근거를 문장
    단위로 정확히 끊어 주지 않으므로 이쪽이 훨씬 흔한 경우다.

    그래서 "근거가 원문에 얼마나 담겨 있는가"를 잰다. 환각 문장은 원문과 겹치는 조각이
    조사·어미뿐이라 여전히 낮게 나온다.

    autojunk=False가 필요하다 — 기본값은 긴 문자열에서 자주 나오는 문자를 매칭에서
    빼 버려 한국어 원문에서 결과가 왜곡된다.
    """
    if not evidence_norm:
        return 0.0
    matcher = SequenceMatcher(None, evidence_norm, source_norm, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(evidence_norm)


def verify_evidence(evidence: str, source_text: str, threshold: float) -> tuple[bool, str, float]:
    """근거 문장이 청크 원문에 실제로 있는지 확인한다 (지침서 11.3).

    LLM은 근거를 요약하거나, 어미를 바꾸거나, 문장 일부만 따온다. 그대로 버리면 멀쩡한
    후보를 대량으로 잃고, 그대로 받으면 원문에 없는 문장이 그래프에 남는다. 그래서:

      1. 정규화 후 부분 문자열이면 통과 (원문 그대로 인용)
      2. 아니면 **근거가 원문에 담긴 비율**을 잰다
      3. 임계값 이상이면 통과시키되, 가장 잘 맞는 **원문 문장으로 교체**해 저장한다 —
         저장되는 근거는 항상 원문이어야 역추적이 성립한다
      4. 미달이면 거절

    반환: (통과 여부, 저장할 근거 문장, 점수)
    """
    ev = _norm(evidence)
    src = _norm(source_text)
    if not ev:
        return False, "", 0.0
    if not src:
        return False, evidence, 0.0
    if ev in src:
        return True, evidence.strip(), 1.0

    score = _coverage(ev, src)
    if score < threshold:
        return False, evidence, score

    # 통과했으면 저장은 원문 문장으로 한다. 근거를 가장 많이 담은 문장을 고른다.
    best_sentence = ""
    best_local = 0.0
    for sentence in _split_sentences(source_text):
        local = _coverage(ev, _norm(sentence))
        if local > best_local:
            best_local, best_sentence = local, sentence
    return True, (best_sentence or source_text).strip(), score


def _is_generic(name: str, generic_terms: frozenset[str]) -> bool:
    """일반명사인지 본다.

    목록에 없는 일반명사도 잡히도록 **구조 규칙을 함께** 쓴다: 수식어 없이 한 낱말이면서
    그 낱말이 목록에 있으면 제외. 목록은 도메인 지식(작물 이름)이 아니라 불용어다.
    """
    n = _norm(name)
    if n in {_norm(t) for t in generic_terms}:
        return True
    tokens = n.split()
    return len(tokens) == 1 and tokens[0] in {_norm(t) for t in generic_terms}


def validate_entities(
    raw_entities: list[dict[str, object]],
    *,
    source_text: str,
    allowed_types: list[str],
    max_entities: int,
    minimum_confidence: float,
    evidence_threshold: float,
    skip_citation_only: bool = True,
    generic_terms: frozenset[str] = GENERIC_TERMS,
) -> ValidationResult:
    """LLM이 돌려준 엔티티 목록을 검증한다.

    받아들인 것과 거절한 것을 모두 돌려준다 — 거절 사유 분포가 프롬프트 튜닝의 근거다.
    """
    accepted: list[ValidatedEntity] = []
    rejected: list[RejectedEntity] = []
    allowed = {t.upper() for t in allowed_types}
    seen: set[tuple[str, str]] = set()

    for idx, raw in enumerate(raw_entities or []):
        name = str(raw.get("name") or "").strip()
        etype = str(raw.get("type") or "").strip().upper()

        if len(accepted) >= max_entities:
            rejected.append(RejectedEntity(name, REASON_OVER_LIMIT, f"상한 {max_entities}"))
            continue
        if not name or len(name) < _MIN_NAME_CHARS:
            rejected.append(RejectedEntity(name, REASON_EMPTY_NAME))
            continue
        if len(name) > _MAX_NAME_CHARS:
            rejected.append(RejectedEntity(name[:40], REASON_NAME_TOO_LONG, f"{len(name)}자"))
            continue
        if etype not in allowed:
            rejected.append(RejectedEntity(name, REASON_BAD_TYPE, etype))
            continue
        if _NUMERIC_ONLY.match(name):
            # 지침서 사례 7: 연구기간 2021~2025를 엔티티로 만들지 않는다.
            rejected.append(RejectedEntity(name, REASON_NUMERIC_ONLY))
            continue
        if _is_generic(name, generic_terms):
            rejected.append(RejectedEntity(name, REASON_GENERAL_TERM))
            continue

        status = str(raw.get("status") or "UNCERTAIN").strip().upper()
        if status not in STATEMENT_STATUSES:
            rejected.append(RejectedEntity(name, REASON_BAD_STATUS, status))
            continue

        relevance = str(raw.get("current_project_relevance") or "UNCERTAIN").strip().upper()
        if relevance not in PROJECT_RELEVANCES:
            rejected.append(RejectedEntity(name, REASON_BAD_RELEVANCE, relevance))
            continue

        # 현재 과제와 무관하거나 인용뿐인 것은 현재 과제 그래프에 넣지 않는다.
        if relevance == "NONE":
            rejected.append(RejectedEntity(name, REASON_NOT_CURRENT_PROJECT, relevance))
            continue
        if skip_citation_only and status in NON_ATTRIBUTABLE_STATUSES:
            rejected.append(RejectedEntity(name, REASON_NOT_CURRENT_PROJECT, status))
            continue

        try:
            raw_conf = raw.get("confidence")
            confidence = float(raw_conf) if isinstance(raw_conf, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        if not (0.0 <= confidence <= 1.0):
            rejected.append(RejectedEntity(name, REASON_LOW_CONFIDENCE, f"범위 밖 {confidence}"))
            continue
        if confidence < minimum_confidence:
            rejected.append(RejectedEntity(name, REASON_LOW_CONFIDENCE, f"{confidence:.2f}"))
            continue

        evidence = str(raw.get("evidence") or "").strip()
        if not evidence:
            rejected.append(RejectedEntity(name, REASON_NO_EVIDENCE))
            continue
        ok, fixed_evidence, ratio = verify_evidence(evidence, source_text, evidence_threshold)
        if not ok:
            rejected.append(
                RejectedEntity(name, REASON_EVIDENCE_NOT_IN_SOURCE, f"유사도 {ratio:.2f}")
            )
            continue

        key = (etype, _norm(name))
        if key in seen:
            rejected.append(RejectedEntity(name, REASON_DUPLICATE_IN_CHUNK))
            continue
        seen.add(key)

        targets = raw.get("target_terms") or []
        target_terms = (
            [str(t).strip() for t in targets if str(t).strip()] if isinstance(targets, list) else []
        )

        accepted.append(
            ValidatedEntity(
                temp_id=str(raw.get("temp_id") or f"E{idx + 1}"),
                entity_type=etype,
                name=name,
                canonical_name_candidate=str(raw.get("canonical_name_candidate") or name).strip(),
                description=str(raw.get("description") or "").strip()[:500],
                statement_status=status,
                project_relevance=relevance,
                target_terms=target_terms,
                specificity=str(raw.get("specificity") or "").strip().upper(),
                evidence=fixed_evidence[:1000],
                confidence=confidence,
            )
        )

    return ValidationResult(accepted=accepted, rejected=rejected)


def target_key(target_terms: list[str]) -> str:
    """대상 키 — 병합 금지 판정(스펙 §6 R2)에 쓰는 정규화 키.

    작물·품종·병해충·지역을 **LLM이 짚어 준 값**으로 만든다. 코드가 작물 목록을 가질
    필요가 없는 이유가 이것이다.
    """
    normed = sorted({_norm(t) for t in target_terms if t and t.strip()})
    return "|".join(normed)

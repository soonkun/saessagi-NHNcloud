# src/kg/merge.py
"""M_23 이름 비교·병합 판정과 문서 단위 통합 (스펙 §3 (6), §6).

**여기에는 작물 목록이 없다.**

지침서 26장의 사례(사과/복숭아, 과수/사과 …)는 *예시*이지 규칙이 아니다. 사례를 사전으로
박으면 목록에 없는 작물—배추/무, 벼/보리, 새로 나온 병해충—에서 조용히 오병합이 난다.
게다가 실패 방향이 나쁘다: 사전 누락은 정확히 False Merge(지침서 21장 최우선 지표)를
키우는 쪽으로 터진다.

그래서 일반 규칙으로 판정한다.

    R1 유형이 다르면 병합 금지            — 구조 비교, 사전 불필요
    R2 대상 키가 다르면 병합 금지          — 추출 단계에서 받은 값, 사전 불필요
    R3 차이 토큰이 실질어면 병합 금지      — "무시 가능한 수식어" 목록만 사용
    R4 접미어 성격이 다르면 병합 금지      — 기술(APPROACH) vs 모형(ARTIFACT)
    R5 포함 관계면 SAME 아님               — 상위/하위 후보로 남긴다
    R6 근거·신뢰도 부족이면 병합 금지

`사과 육종시스템` vs `복숭아 육종시스템`은 **차이 토큰이 사과/복숭아**이고 이것이 무시 가능한
수식어가 아니므로 병합되지 않는다. 사과와 복숭아가 무엇인지 알 필요가 없다. 같은 이유로
`배추 병해진단` vs `무 병해진단`도 자동으로 걸린다.

유일한 사전은 **무시 가능한 수식어·표기 변형**인데, 이것은 도메인에 묶이지 않고 작으며,
무엇보다 **틀렸을 때 안전한 쪽으로 실패한다** — 항목을 빠뜨리면 병합이 안 될 뿐(노드 2개)
오병합이 생기지 않는다.

상위/하위 개념(과수 ⊃ 사과)만은 의미 지식이 필요하다. 이건 사전이 아니라 LLM 판정에
맡기고(§7 normalize), 여기서는 **SAME이 아니라는 것만** 확정한다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# ── 판정값 (지침서 14.3) ─────────────────────────────────────────────────────

SAME = "SAME"
RELATED = "RELATED"
BROADER = "BROADER"
NARROWER = "NARROWER"
DIFFERENT = "DIFFERENT"
UNCERTAIN = "UNCERTAIN"

VERDICTS: frozenset[str] = frozenset({SAME, RELATED, BROADER, NARROWER, DIFFERENT, UNCERTAIN})


# ── 표기 변형 (같은 것을 다르게 쓴 경우) ──────────────────────────────────────
# 사전이라기보다 표기 통일 규칙이다. 대상(작물·병해충)에 대한 지식이 아니다.
DEFAULT_EQUIVALENCES: dict[str, str] = {
    "plus": "+",
    "모델": "모형",
    "메소드": "방법",
    "메서드": "방법",
    "시스템즈": "시스템",
    "데이터베이스": "db",
    "ver": "버전",
    "version": "버전",
}

# 있으나 없으나 같은 대상을 가리키는 수식어. 이것만 다르면 같은 것으로 본다.
# (지침서 사례 3: SWAT+, SWAT Plus, SWAT+ 모형 → 하나로)
DEFAULT_IGNORABLE_QUALIFIERS: frozenset[str] = frozenset(
    {
        "개발",
        "구축",
        "관련",
        "기반",
        "활용",
        "이용",
        "적용",
        "관한",
        "위한",
        "등",
        "및",
        "the",
        "a",
        "an",
        "of",
        "for",
    }
)

# 접미어 성격 — **같은 알맹이라도 성격이 다르면 다른 것이다.**
# 지침서 사례 4(유전체 선발 기술 vs 유전체 선발모형)와
# "병해충 영상진단 기술 vs 병해충 영상진단 서비스"를 일반화한 규칙이다.
DEFAULT_SUFFIX_CLASSES: dict[str, str] = {
    # 산출물·구현체
    "모형": "ARTIFACT",
    "시스템": "ARTIFACT",
    "장치": "ARTIFACT",
    "도구": "ARTIFACT",
    "플랫폼": "ARTIFACT",
    "db": "ARTIFACT",
    "지도": "ARTIFACT",
    "품종": "ARTIFACT",
    "매뉴얼": "ARTIFACT",
    # 접근·수단
    "기술": "APPROACH",
    "방법": "APPROACH",
    "기법": "APPROACH",
    "공법": "APPROACH",
    "전략": "APPROACH",
    "방안": "APPROACH",
    # 제공 형태
    "서비스": "SERVICE",
    "사업": "SERVICE",
    # 활동
    "평가": "ACTIVITY",
    "분석": "ACTIVITY",
    "진단": "ACTIVITY",
    "조사": "ACTIVITY",
}

_TOKEN_SPLIT = re.compile(r"[\s/,()\[\]{}·‧・:;~\-–—_]+")
_PUNCT_TRIM = re.compile(r"^[^\w+]+|[^\w+]+$")


@dataclass(frozen=True)
class MergeRules:
    """판정 규칙 묶음 — 설정에서 갈아끼울 수 있다."""

    equivalences: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EQUIVALENCES))
    ignorable_qualifiers: frozenset[str] = DEFAULT_IGNORABLE_QUALIFIERS
    suffix_classes: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SUFFIX_CLASSES))
    string_similarity_threshold: float = 0.90
    prevent_cross_type_merge: bool = True
    prevent_different_target_merge: bool = True


@dataclass(frozen=True)
class NameAnalysis:
    """이름 하나를 뜯어본 결과."""

    raw: str
    normalized: str
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]
    core: str
    suffix_class: str


@dataclass(frozen=True)
class MergeInput:
    """병합 판정 입력 — 이름만으로 판단하지 않는다."""

    name: str
    entity_type: str
    target_key: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class MergeDecision:
    """판정 결과. `auto_merge`가 True일 때만 같은 노드로 합친다."""

    verdict: str
    auto_merge: bool
    reason: str
    similarity: float = 0.0


def normalize_name(name: str, rules: MergeRules | None = None) -> str:
    """문자열 정규화 (지침서 14.1) — 유니코드·대소문자·공백·표기 변형."""
    r = rules or MergeRules()
    text = unicodedata.normalize("NFKC", name or "").strip().casefold()
    text = re.sub(r"\s+", " ", text)
    # 표기 변형 통일. 긴 것부터 바꿔야 "데이터베이스"가 "db"로 온전히 간다.
    for src in sorted(r.equivalences, key=len, reverse=True):
        text = text.replace(src, r.equivalences[src])
    # 치환이 공백을 남기는 경우를 정리한다. "swat plus" → "swat +" → "swat+".
    # 이걸 빼먹으면 SWAT+ 와 SWAT Plus 가 다른 토큰이 되어 표기 변형 병합이 통째로
    # 실패한다 (테스트 test_case3_notation_variants_merge가 잡았다).
    text = re.sub(r"\s+\+", "+", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_tokens(normalized: str) -> tuple[str, ...]:
    parts = [_PUNCT_TRIM.sub("", p) for p in _TOKEN_SPLIT.split(normalized)]
    return tuple(p for p in parts if p)


def _strip_suffix(token: str, rules: MergeRules) -> tuple[str, str]:
    """토큰 끝의 성격 접미어를 떼어 (알맹이, 성격)을 돌려준다.

    한국어는 띄어쓰기 없이 붙는 경우가 많아(`사과육종시스템`) 토큰 비교만으로는 부족하다.
    긴 접미어부터 본다 — `데이터베이스`가 `db`로 통일된 뒤에도 `지도`보다 먼저 잡히도록.
    """
    for suffix in sorted(rules.suffix_classes, key=len, reverse=True):
        if len(token) > len(suffix) and token.endswith(suffix):
            return token[: -len(suffix)].strip(), rules.suffix_classes[suffix]
    return token, ""


def analyze_name(name: str, rules: MergeRules | None = None) -> NameAnalysis:
    """이름을 정규화하고 알맹이(core)와 접미어 성격을 뽑는다."""
    r = rules or MergeRules()
    normalized = normalize_name(name, r)
    tokens = _split_tokens(normalized)

    suffix_class = ""
    core_tokens: list[str] = []
    for i, tok in enumerate(tokens):
        if tok in r.ignorable_qualifiers:
            continue
        if tok in r.suffix_classes:
            # 독립 토큰으로 떨어진 접미어 ("swat+ 모형")
            suffix_class = suffix_class or r.suffix_classes[tok]
            continue
        stem, cls = _strip_suffix(tok, r)
        # 마지막 토큰에 붙은 접미어만 성격으로 인정한다 — 중간 토큰의 우연한 일치를 피한다.
        if cls and i == len(tokens) - 1:
            suffix_class = suffix_class or cls
            if stem:
                core_tokens.append(stem)
                continue
        core_tokens.append(tok)

    core = " ".join(core_tokens)
    return NameAnalysis(
        raw=name,
        normalized=normalized,
        tokens=tokens,
        core_tokens=tuple(core_tokens),
        core=core,
        suffix_class=suffix_class,
    )


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def compare_names(left: str, right: str, rules: MergeRules | None = None) -> tuple[str, str, float]:
    """이름만 비교한다. 반환 (판정, 사유, 유사도).

    유형·대상 키는 여기서 보지 않는다 — `merge_decision`이 하드 규칙으로 먼저 막는다.
    """
    r = rules or MergeRules()
    a = analyze_name(left, r)
    b = analyze_name(right, r)
    sim = _similarity(a.core or a.normalized, b.core or b.normalized)

    # 알맹이가 같은데 접미어 성격이 다르면 다른 것이다 (기술 vs 모형 vs 서비스).
    if a.core == b.core:
        if a.suffix_class and b.suffix_class and a.suffix_class != b.suffix_class:
            return (
                RELATED,
                f"알맹이는 같으나 성격이 다름({a.suffix_class} vs {b.suffix_class})",
                sim,
            )
        return SAME, "정규화 후 동일", sim

    set_a, set_b = set(a.core_tokens), set(b.core_tokens)
    # 한쪽이 다른 쪽을 포함하면 상위/하위 후보다 — 절대 SAME으로 자동 처리하지 않는다.
    if set_a and set_b and set_a < set_b:
        return NARROWER, "다른 쪽이 수식어를 더 갖고 있음(상위/하위 후보)", sim
    if set_a and set_b and set_b < set_a:
        return BROADER, "이쪽이 수식어를 더 갖고 있음(상위/하위 후보)", sim

    # 차이 토큰이 전부 무시 가능한 수식어면 같은 것으로 본다.
    diff = (Counter(a.core_tokens) - Counter(b.core_tokens)) + (
        Counter(b.core_tokens) - Counter(a.core_tokens)
    )
    if diff and all(tok in r.ignorable_qualifiers for tok in diff):
        return SAME, "차이가 무시 가능한 수식어뿐", sim

    # 여기까지 왔으면 실질어가 다르다. **이 지점이 사과/복숭아를 막는 곳이다.**
    if sim >= r.string_similarity_threshold:
        return (
            UNCERTAIN,
            f"문자열은 비슷하나 실질어가 다름({', '.join(sorted(diff))})",
            sim,
        )
    return DIFFERENT, f"실질어가 다름({', '.join(sorted(diff))})", sim


def merge_decision(
    left: MergeInput,
    right: MergeInput,
    rules: MergeRules | None = None,
    min_confidence: float = 0.0,
) -> MergeDecision:
    """두 후보를 합쳐도 되는지 판정한다. 하드 규칙이 LLM 판정보다 우선한다(스펙 §6)."""
    r = rules or MergeRules()

    # R1 유형 불일치
    if r.prevent_cross_type_merge and left.entity_type != right.entity_type:
        return MergeDecision(
            DIFFERENT, False, f"유형 불일치({left.entity_type} vs {right.entity_type})"
        )

    # R2 대상 키 불일치 — 추출 단계에서 받은 작물·품종·병해충·지역 값
    if r.prevent_different_target_merge:
        ta, tb = (left.target_key or "").strip(), (right.target_key or "").strip()
        if ta and tb and normalize_name(ta, r) != normalize_name(tb, r):
            return MergeDecision(DIFFERENT, False, f"대상이 다름({ta} vs {tb})")

    # R6 신뢰도
    if min(left.confidence, right.confidence) < min_confidence:
        return MergeDecision(UNCERTAIN, False, "신뢰도 부족")

    verdict, reason, sim = compare_names(left.name, right.name, r)
    return MergeDecision(verdict, verdict == SAME, reason, sim)


# ── 문서 단위 통합 (스펙 §3 (6)) ─────────────────────────────────────────────


@dataclass
class MergedGroup:
    """같은 문서 안에서 하나로 묶인 후보들."""

    entity_type: str
    canonical_name: str
    target_key: str
    aliases: list[str] = field(default_factory=list)
    member_ids: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    max_confidence: float = 0.0
    review_required: bool = False


def consolidate(
    items: list[tuple[str, MergeInput, str]],
    rules: MergeRules | None = None,
    min_confidence: float = 0.0,
) -> list[MergedGroup]:
    """문서 하나에서 나온 후보들을 표기 변형 단위로 묶는다.

    입력: (후보 id, 판정 입력, 진술 상태) 목록.
    전역 정규화 **이전** 단계다 — 같은 문서 안에서만 묶는다(지침서 13장). 문서를 넘는
    병합은 근거가 더 필요하므로 다음 단계에서 LLM 판정까지 거친다.

    대표 이름은 **가장 많이 나온 표기**로 정한다(동률이면 짧은 것). 원문 표기는 전부
    별칭으로 남겨 근거 역추적이 끊기지 않게 한다.
    """
    r = rules or MergeRules()
    groups: list[MergedGroup] = []
    surface_counts: list[Counter[str]] = []

    for cand_id, item, status in items:
        placed = False
        for gi, g in enumerate(groups):
            probe = MergeInput(
                name=g.canonical_name,
                entity_type=g.entity_type,
                target_key=g.target_key,
                confidence=1.0,
            )
            decision = merge_decision(probe, item, r, min_confidence)
            if decision.auto_merge:
                g.member_ids.append(cand_id)
                if item.name not in g.aliases:
                    g.aliases.append(item.name)
                if status and status not in g.statuses:
                    g.statuses.append(status)
                g.max_confidence = max(g.max_confidence, item.confidence)
                if not g.target_key and item.target_key:
                    g.target_key = item.target_key
                surface_counts[gi][item.name] += 1
                placed = True
                break
            if decision.verdict == UNCERTAIN:
                # 애매한 것은 합치지 않되 검토 대상으로 남긴다 (정밀도 우선, 지침서 4.1)
                g.review_required = True
        if not placed:
            groups.append(
                MergedGroup(
                    entity_type=item.entity_type,
                    canonical_name=item.name,
                    target_key=item.target_key,
                    aliases=[item.name],
                    member_ids=[cand_id],
                    statuses=[status] if status else [],
                    max_confidence=item.confidence,
                )
            )
            surface_counts.append(Counter({item.name: 1}))

    # 대표 이름 재선정 — 최빈 표기, 동률이면 짧은 쪽
    for g, counts in zip(groups, surface_counts, strict=True):
        best = min(counts.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]
        g.canonical_name = best
    return groups

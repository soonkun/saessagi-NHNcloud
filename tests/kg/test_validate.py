# tests/kg/test_validate.py
"""M_23 추출 검증 테스트 (지침서 11장).

이 파일이 지키는 것은 하나다. **원문에 없는 것은 그래프에 들어가지 않는다.**

지침서가 최우선 지표로 꼽은 Entity Precision·Evidence Accuracy·Current-Project
Attribution Accuracy가 전부 이 단계에서 결정된다. 그래서 통과시키는 경우보다
**거절하는 경우**를 더 촘촘히 본다.
"""

from __future__ import annotations

import pytest

from kg.validate import (
    REASON_BAD_STATUS,
    REASON_BAD_TYPE,
    REASON_DUPLICATE_IN_CHUNK,
    REASON_EVIDENCE_NOT_IN_SOURCE,
    REASON_GENERAL_TERM,
    REASON_LOW_CONFIDENCE,
    REASON_NAME_TOO_LONG,
    REASON_NO_EVIDENCE,
    REASON_NOT_CURRENT_PROJECT,
    REASON_NUMERIC_ONLY,
    REASON_OVER_LIMIT,
    target_key,
    validate_entities,
    verify_evidence,
)

SOURCE = (
    "3. 연구수행 내용 및 결과\n"
    "본 연구에서는 SWAT+ 모형을 개선하여 유역 단위 수질 예측 정확도를 향상시켰다. "
    "사과 과수원 3개소에서 현장 실증을 수행하였으며, 예측 오차는 12% 감소하였다.\n"
    "향후 복숭아 등 타 작목으로 확대할 계획이다."
)

TYPES = [
    "RESEARCH_PROBLEM",
    "OBJECTIVE",
    "RESEARCH_TARGET",
    "TECHNOLOGY",
    "METHOD",
    "DATASET",
    "OUTPUT",
]


def _ent(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "temp_id": "E1",
        "type": "TECHNOLOGY",
        "name": "SWAT+ 모형",
        "status": "ACTUAL",
        "current_project_relevance": "DIRECT",
        "evidence": "본 연구에서는 SWAT+ 모형을 개선하여 유역 단위 수질 예측 정확도를 향상시켰다.",
        "confidence": 0.92,
        "target_terms": ["사과"],
    }
    base.update(kw)
    return base


def run(entities: list[dict[str, object]], **kw: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "source_text": SOURCE,
        "allowed_types": TYPES,
        "max_entities": 12,
        "minimum_confidence": 0.70,
        "evidence_threshold": 0.80,
    }
    params.update(kw)
    return validate_entities(entities, **params)  # type: ignore[arg-type]


class TestEvidenceVerification:
    def test_exact_quote_passes(self) -> None:
        ok, ev, ratio = verify_evidence(
            "사과 과수원 3개소에서 현장 실증을 수행하였으며", SOURCE, 0.80
        )
        assert ok and ratio == 1.0
        assert "사과 과수원" in ev

    def test_paraphrase_is_replaced_with_source_sentence(self) -> None:
        """LLM이 어미를 바꿔 오면 원문 문장으로 되돌려 저장한다 — 근거는 항상 원문이어야 한다."""
        ok, ev, ratio = verify_evidence(
            "본 연구에서는 SWAT+ 모형을 개선하여 유역 단위 수질 예측 정확도를 향상시킴",
            SOURCE,
            0.80,
        )
        assert ok and ratio >= 0.80
        assert ev in SOURCE  # 저장되는 것은 원문 그대로

    def test_partial_quote_passes(self) -> None:
        """LLM은 문장 일부만 따오는 일이 잦다 — 원문에 담겨 있으면 정상 근거다.

        문장 전체 유사도로 재던 시절 이 케이스가 0.76으로 탈락했다. 멀쩡한 후보를
        대량으로 잃는 방향이라 근거 판정을 '원문에 담긴 비율'로 바꿨다.
        """
        ok, ev, score = verify_evidence(
            "SWAT+ 모형을 개선하여 수질 예측 정확도를 향상", SOURCE, 0.80
        )
        assert ok and score >= 0.80
        assert ev in SOURCE

    @pytest.mark.parametrize(
        "fake",
        [
            "딥러닝 기반 병해충 판별모델을 개발하여 정확도 95%를 달성하였다.",
            "토마토 온실 환경제어 알고리즘을 최적화하였다.",
            "본 과제는 국가 예산 100억 원을 투입하여 수행되었다.",
        ],
    )
    def test_hallucinated_evidence_rejected(self, fake: str) -> None:
        """원문에 없는 문장은 통과하면 안 된다 — 이게 뚫리면 그래프 전체를 못 믿는다."""
        ok, _, score = verify_evidence(fake, SOURCE, 0.80)
        assert not ok and score < 0.80

    def test_empty_inputs(self) -> None:
        assert verify_evidence("", SOURCE, 0.8)[0] is False
        assert verify_evidence("무언가", "", 0.8)[0] is False


class TestAcceptance:
    def test_valid_entity_passes(self) -> None:
        res = run([_ent()])
        assert len(res.accepted) == 1
        e = res.accepted[0]
        assert e.name == "SWAT+ 모형" and e.entity_type == "TECHNOLOGY"
        assert e.target_terms == ["사과"]

    def test_evidence_stored_from_source(self) -> None:
        res = run([_ent(evidence="SWAT+ 모형을 개선하여 수질 예측 정확도를 향상")])
        assert res.accepted and res.accepted[0].evidence in SOURCE


class TestRejection:
    @pytest.mark.parametrize(
        ("kw", "reason"),
        [
            ({"type": "PERSON"}, REASON_BAD_TYPE),
            ({"status": "MAYBE"}, REASON_BAD_STATUS),
            ({"confidence": 0.4}, REASON_LOW_CONFIDENCE),
            ({"confidence": 1.7}, REASON_LOW_CONFIDENCE),
            ({"evidence": ""}, REASON_NO_EVIDENCE),
            ({"evidence": "존재하지 않는 근거 문장이다"}, REASON_EVIDENCE_NOT_IN_SOURCE),
            ({"name": "연구"}, REASON_GENERAL_TERM),
            ({"name": "2021~2025"}, REASON_NUMERIC_ONLY),
            ({"name": "가" * 80}, REASON_NAME_TOO_LONG),
        ],
    )
    def test_rejects(self, kw: dict[str, object], reason: str) -> None:
        res = run([_ent(**kw)])
        assert res.accepted == []
        assert res.rejected[0].reason == reason

    def test_prior_research_not_attributed_to_current_project(self) -> None:
        """지침서 사례 5 — 선행연구를 현재 과제 기술로 귀속하지 않는다."""
        res = run([_ent(status="PRIOR_RESEARCH")])
        assert res.accepted == []
        assert res.rejected[0].reason == REASON_NOT_CURRENT_PROJECT

    def test_citation_only_excluded(self) -> None:
        res = run([_ent(status="CITATION_ONLY")])
        assert res.rejected[0].reason == REASON_NOT_CURRENT_PROJECT

    def test_relevance_none_excluded(self) -> None:
        res = run([_ent(current_project_relevance="NONE")])
        assert res.rejected[0].reason == REASON_NOT_CURRENT_PROJECT

    def test_prior_research_can_be_kept_by_config(self) -> None:
        """선행연구 그래프를 따로 만들고 싶을 때를 위해 설정으로 열어 둔다."""
        res = run([_ent(status="PRIOR_RESEARCH")], skip_citation_only=False)
        assert len(res.accepted) == 1

    def test_duplicate_within_chunk(self) -> None:
        res = run([_ent(), _ent(temp_id="E2")])
        assert len(res.accepted) == 1
        assert res.rejected[0].reason == REASON_DUPLICATE_IN_CHUNK

    def test_same_name_different_type_is_not_duplicate(self) -> None:
        res = run([_ent(), _ent(temp_id="E2", type="OUTPUT")])
        assert len(res.accepted) == 2

    def test_max_entities_enforced(self) -> None:
        items = [_ent(temp_id=f"E{i}", name=f"SWAT+ 모형{i}") for i in range(6)]
        res = run(items, max_entities=3)
        assert len(res.accepted) == 3
        assert all(r.reason == REASON_OVER_LIMIT for r in res.rejected)

    def test_planned_status_is_kept_but_marked(self) -> None:
        """지침서 사례 6 — 계획은 버리지 않되 실적과 구분해 남긴다."""
        res = run([_ent(status="PLANNED", evidence="향후 복숭아 등 타 작목으로 확대할 계획이다.")])
        assert len(res.accepted) == 1
        assert res.accepted[0].statement_status == "PLANNED"

    def test_empty_list(self) -> None:
        res = run([])
        assert res.accepted == [] and res.rejected == []

    def test_counts_summarise_reasons(self) -> None:
        res = run([_ent(), _ent(temp_id="E2", type="PERSON"), _ent(temp_id="E3", confidence=0.1)])
        counts = res.counts
        assert counts["accepted"] == 1 and counts["rejected"] == 2
        assert counts[REASON_BAD_TYPE] == 1


class TestTargetKey:
    def test_order_and_case_insensitive(self) -> None:
        assert target_key(["사과", "탄저병"]) == target_key(["탄저병", " 사과 "])

    def test_different_targets_produce_different_keys(self) -> None:
        assert target_key(["사과"]) != target_key(["복숭아"])

    def test_empty(self) -> None:
        assert target_key([]) == ""
        assert target_key(["", "  "]) == ""

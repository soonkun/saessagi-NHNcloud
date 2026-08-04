# tests/kg/test_merge.py
"""M_23 병합 판정 테스트 (스펙 §6).

지침서 26장의 사례 7개는 **회귀 픽스처**로만 쓴다. 규칙이 아니다.

그래서 이 파일은 두 축으로 검증한다.
1. 지침서 사례가 통과하는가 (TestGuidelineCases)
2. **어떤 목록에도 없는 대상 쌍**이 같은 메커니즘으로 걸리는가 (TestGeneralisesBeyondExamples)

2번이 핵심이다. 사과/복숭아만 통과하고 배추/무에서 뚫리면, 사례를 사전으로 박은 것과
다를 바가 없고 목록에 없는 작물에서 조용히 오병합이 난다.
"""

from __future__ import annotations

import pytest

from kg.merge import (
    BROADER,
    DIFFERENT,
    NARROWER,
    RELATED,
    SAME,
    UNCERTAIN,
    MergeInput,
    MergeRules,
    analyze_name,
    compare_names,
    consolidate,
    merge_decision,
    normalize_name,
)

RULES = MergeRules()


def decide(a: str, b: str, *, ta: str = "TECHNOLOGY", tb: str = "TECHNOLOGY", **kw: str) -> str:
    return merge_decision(
        MergeInput(name=a, entity_type=ta, target_key=kw.get("ka", "")),
        MergeInput(name=b, entity_type=tb, target_key=kw.get("kb", "")),
        RULES,
    ).verdict


class TestNormalization:
    def test_case_and_space_and_notation(self) -> None:
        assert normalize_name("  SWAT  Plus  ") == "swat+"
        assert normalize_name("SWAT+") == "swat+"

    def test_model_synonym(self) -> None:
        assert normalize_name("예측모델") == normalize_name("예측모형")

    def test_unicode_width(self) -> None:
        assert normalize_name("ＳＷＡＴ") == "swat"

    def test_core_and_suffix_class(self) -> None:
        a = analyze_name("유전체 선발 기술")
        assert a.core == "유전체 선발"
        assert a.suffix_class == "APPROACH"

    def test_core_without_space(self) -> None:
        """한국어는 붙여 쓰는 경우가 많다 — 토큰 비교만으로는 부족하다."""
        a = analyze_name("사과육종시스템")
        assert a.core == "사과육종"
        assert a.suffix_class == "ARTIFACT"


class TestGuidelineCases:
    """지침서 26장 사례 — 회귀 픽스처."""

    def test_case1_same_tech_different_crop(self) -> None:
        """사례 1: 같은 기술, 다른 작물 → 병합 금지."""
        assert decide("사과 유전체 육종시스템", "복숭아 유전체 육종시스템") == DIFFERENT

    def test_case2_broader_narrower(self) -> None:
        """사례 2: 과수 ⊃ 사과 → SAME 아님. 상위/하위 후보로 남긴다."""
        verdict = decide("과수 육종시스템", "사과 육종시스템")
        assert verdict != SAME

    def test_case3_notation_variants_merge(self) -> None:
        """사례 3: SWAT+ / SWAT Plus / SWAT+ 모형 → 하나로."""
        assert decide("SWAT+", "SWAT Plus") == SAME
        assert decide("SWAT+ 모형", "SWAT Plus 모델") == SAME
        assert decide("SWAT+", "SWAT+ 모형") == SAME

    def test_case4_technology_vs_output(self) -> None:
        """사례 4: 유전체 선발 기술 vs 유전체 선발모형 → 자동 SAME 금지."""
        # 유형이 다르게 잡힌 경우
        assert (
            decide("유전체 선발 기술", "유전체 선발모형", ta="TECHNOLOGY", tb="OUTPUT") == DIFFERENT
        )
        # 유형이 같게 잡혀도 접미어 성격이 달라 SAME이 아니어야 한다
        assert decide("유전체 선발 기술", "유전체 선발모형") == RELATED

    def test_service_vs_technology(self) -> None:
        """지침서 13장 통합 금지 사례: 병해충 영상진단 기술 vs 서비스."""
        assert decide("병해충 영상진단 기술", "병해충 영상진단 서비스") == RELATED


class TestGeneralisesBeyondExamples:
    """**목록 어디에도 없는 대상**이 같은 메커니즘으로 걸리는지 — 이게 핵심이다."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("배추 병해진단 기술", "무 병해진단 기술"),
            ("벼 재배모형", "보리 재배모형"),
            ("딸기 스마트팜 시스템", "토마토 스마트팜 시스템"),
            ("한우 사양관리 기술", "젖소 사양관리 기술"),
            ("경기도 토양조사", "전라남도 토양조사"),
            ("탄저병 방제기술", "역병 방제기술"),
        ],
    )
    def test_different_targets_never_merge(self, a: str, b: str) -> None:
        """작물·가축·지역·병해충 — 코드 어디에도 이 단어들의 목록이 없다."""
        assert decide(a, b) == DIFFERENT

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("벼 수량예측모형", "벼 수량예측모델"),
            ("스마트팜 데이터 플랫폼 개발", "스마트팜 데이터 플랫폼"),
            ("PLS 기반 잔류농약 분석법", "pls 기반 잔류농약 분석법"),
        ],
    )
    def test_notation_variants_still_merge(self, a: str, b: str) -> None:
        """대상이 같고 표기만 다르면 합쳐져야 한다 — 지나치게 보수적이어도 안 된다."""
        assert decide(a, b) == SAME

    def test_target_key_blocks_even_when_names_identical(self) -> None:
        """이름이 같아도 추출 단계가 대상을 다르게 짚었으면 합치지 않는다."""
        assert decide("육종시스템", "육종시스템", ka="사과", kb="복숭아") == DIFFERENT

    def test_target_key_absent_falls_back_to_name(self) -> None:
        assert decide("사과 육종시스템", "복숭아 육종시스템", ka="", kb="") == DIFFERENT


class TestHardRules:
    def test_type_mismatch_blocks(self) -> None:
        assert (
            decide("스마트팜 플랫폼", "스마트팜 플랫폼", ta="TECHNOLOGY", tb="OUTPUT") == DIFFERENT
        )

    def test_type_mismatch_can_be_disabled(self) -> None:
        rules = MergeRules(prevent_cross_type_merge=False)
        d = merge_decision(
            MergeInput("스마트팜 플랫폼", "TECHNOLOGY"),
            MergeInput("스마트팜 플랫폼", "OUTPUT"),
            rules,
        )
        assert d.verdict == SAME

    def test_low_confidence_is_uncertain(self) -> None:
        d = merge_decision(
            MergeInput("SWAT+", "TECHNOLOGY", confidence=0.4),
            MergeInput("SWAT Plus", "TECHNOLOGY", confidence=0.9),
            RULES,
            min_confidence=0.7,
        )
        assert d.verdict == UNCERTAIN and d.auto_merge is False

    def test_containment_direction(self) -> None:
        assert compare_names("사과 육종시스템", "고품질 사과 육종시스템")[0] == NARROWER
        assert compare_names("고품질 사과 육종시스템", "사과 육종시스템")[0] == BROADER

    def test_only_same_is_auto_merged(self) -> None:
        for a, b in [
            ("과수 육종시스템", "사과 육종시스템"),
            ("유전체 선발 기술", "유전체 선발모형"),
            ("사과 육종시스템", "복숭아 육종시스템"),
        ]:
            assert (
                merge_decision(MergeInput(a, "TECHNOLOGY"), MergeInput(b, "TECHNOLOGY")).auto_merge
                is False
            )


class TestConsolidate:
    def test_groups_notation_variants_and_keeps_aliases(self) -> None:
        items = [
            ("c1", MergeInput("SWAT+ 모형", "TECHNOLOGY", confidence=0.9), "ACTUAL"),
            ("c2", MergeInput("SWAT Plus", "TECHNOLOGY", confidence=0.8), "ACTUAL"),
            ("c3", MergeInput("SWAT+", "TECHNOLOGY", confidence=0.95), "PLANNED"),
        ]
        groups = consolidate(items, RULES)
        assert len(groups) == 1
        g = groups[0]
        assert set(g.member_ids) == {"c1", "c2", "c3"}
        assert set(g.aliases) == {"SWAT+ 모형", "SWAT Plus", "SWAT+"}
        assert set(g.statuses) == {"ACTUAL", "PLANNED"}
        assert g.max_confidence == pytest.approx(0.95)

    def test_keeps_different_targets_apart(self) -> None:
        items = [
            ("c1", MergeInput("사과 육종시스템", "TECHNOLOGY"), "ACTUAL"),
            ("c2", MergeInput("복숭아 육종시스템", "TECHNOLOGY"), "ACTUAL"),
            ("c3", MergeInput("사과 육종 시스템", "TECHNOLOGY"), "PLANNED"),
        ]
        groups = consolidate(items, RULES)
        assert len(groups) == 2
        by_size = sorted(groups, key=lambda g: -len(g.member_ids))
        assert set(by_size[0].member_ids) == {"c1", "c3"}

    def test_canonical_name_is_most_frequent(self) -> None:
        items = [
            ("c1", MergeInput("SWAT+", "TECHNOLOGY"), ""),
            ("c2", MergeInput("SWAT+", "TECHNOLOGY"), ""),
            ("c3", MergeInput("SWAT+ 모형", "TECHNOLOGY"), ""),
        ]
        assert consolidate(items, RULES)[0].canonical_name == "SWAT+"

    def test_empty_input(self) -> None:
        assert consolidate([], RULES) == []

    def test_different_types_never_share_group(self) -> None:
        items = [
            ("c1", MergeInput("스마트팜 플랫폼", "TECHNOLOGY"), ""),
            ("c2", MergeInput("스마트팜 플랫폼", "OUTPUT"), ""),
        ]
        assert len(consolidate(items, RULES)) == 2

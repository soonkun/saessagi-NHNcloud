# tests/agent_prompts/test_registry.py
"""M_17 AgentInstructions — agent_prompts 패키지 단위 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock


from agent_prompts import PROMPT_KEYS, get_default
from agent_prompts.registry import get_label, get_risk


# ── 정상 케이스 ─────────────────────────────────────────────────────────────────


def test_N1_prompt_keys_entries() -> None:
    """N-1: PROMPT_KEYS는 10개의 키를 가진다 (CR-44: 딥 리서치 3모드 추가)."""
    assert len(PROMPT_KEYS) == 10
    expected = {
        "persona",
        "knowledge_note",
        "doc_query_answer",
        "work_query_answer",
        "intent_classify",
        "meeting_minutes",
        "graph_extract",
        "deep_research_duplication",
        "deep_research_discovery",
        "deep_research_proposal",
    }
    assert set(PROMPT_KEYS) == expected


def test_N2_get_default_persona_returns_none() -> None:
    """N-2: persona의 default는 None(코드 상수 없음)."""
    assert get_default("persona") is None


def test_N3_get_default_non_persona_returns_string() -> None:
    """N-3: 나머지 5개 키는 문자열 기본값을 반환한다."""
    for key in PROMPT_KEYS:
        if key == "persona":
            continue
        default = get_default(key)  # type: ignore[arg-type]
        assert isinstance(default, str), f"{key} should return str, got {type(default)}"
        assert len(default) > 0, f"{key} default should not be empty"


def test_N4_risk_values_correct() -> None:
    """N-4: 키별 risk 값이 스펙 표와 일치한다."""
    assert get_risk("persona") == "medium"
    assert get_risk("knowledge_note") == "low"
    assert get_risk("doc_query_answer") == "low"
    assert get_risk("work_query_answer") == "low"
    assert get_risk("intent_classify") == "high"
    assert get_risk("meeting_minutes") == "low"


def test_N5_labels_are_non_empty_strings() -> None:
    """N-5: 모든 키의 레이블이 비어있지 않은 문자열이다."""
    for key in PROMPT_KEYS:
        label = get_label(key)  # type: ignore[arg-type]
        assert isinstance(label, str)
        assert len(label) > 0


def test_N6_effective_prompt_custom_overrides_default() -> None:
    """N-6: agent_prompts에 커스텀이 있으면 effective_prompt가 커스텀을 반환한다."""
    from agent_prompts.registry import effective_prompt

    mock_app_cfg = MagicMock()
    mock_app_cfg.agent_prompts = MagicMock()
    mock_app_cfg.agent_prompts.knowledge_note = "커스텀 노트 지침"

    result = effective_prompt("knowledge_note", mock_app_cfg, None)
    assert result == "커스텀 노트 지침"


def test_N7_effective_prompt_empty_custom_returns_default() -> None:
    """N-7: 커스텀이 빈 문자열이면 default를 반환한다."""
    from agent_prompts.registry import effective_prompt

    mock_app_cfg = MagicMock()
    mock_app_cfg.agent_prompts = MagicMock()
    mock_app_cfg.agent_prompts.meeting_minutes = ""

    result = effective_prompt("meeting_minutes", mock_app_cfg, None)
    # default(SYSTEM_PROMPT)가 반환되어야 함
    from meeting_minutes.prompts import SYSTEM_PROMPT

    assert result == SYSTEM_PROMPT


def test_N8_effective_prompt_persona_uses_character_config() -> None:
    """N-8: persona는 character_config.persona_prompt에서 읽는다."""
    from agent_prompts.registry import effective_prompt

    mock_app_cfg = MagicMock()
    mock_char_cfg = MagicMock()
    mock_char_cfg.persona_prompt = "나는 친절한 비서야"

    result = effective_prompt("persona", mock_app_cfg, mock_char_cfg)
    assert result == "나는 친절한 비서야"


# ── 엣지 케이스 ─────────────────────────────────────────────────────────────────


def test_E1_effective_prompt_no_agent_prompts_attr() -> None:
    """E-1: app_config에 agent_prompts 속성이 없으면 default를 반환한다."""
    from agent_prompts.registry import effective_prompt

    # agent_prompts 없는 경우 - AttributeError 발생해도 graceful 처리
    class MinimalAppConfig:
        pass

    result = effective_prompt("meeting_minutes", MinimalAppConfig(), None)
    from meeting_minutes.prompts import SYSTEM_PROMPT

    assert result == SYSTEM_PROMPT


def test_E2_effective_prompt_none_app_config_non_persona() -> None:
    """E-2: app_config=None이면 default를 반환한다(persona 제외)."""
    from agent_prompts.registry import effective_prompt

    result = effective_prompt("knowledge_note", None, None)
    from agent_prompts.defaults import KNOWLEDGE_NOTE_GUIDE

    assert result == KNOWLEDGE_NOTE_GUIDE


def test_E3_intent_classify_default_contains_required_tokens() -> None:
    """E-3: intent_classify default에 6라벨과 JSON 토큰이 모두 포함된다."""
    default = get_default("intent_classify")
    assert default is not None
    for label in ("calendar_add", "calendar_query", "doc_query", "note_save", "work_query", "chat"):
        assert label in default, f"라벨 '{label}'이 intent_classify default에 없음"
    for token in ("JSON", "intent", "confidence", "reason"):
        assert token in default, f"토큰 '{token}'이 intent_classify default에 없음"


# ── CR-44: 딥 리서치 3모드 지침 ────────────────────────────────────────────────


def test_D1_deep_research_keys_have_string_defaults() -> None:
    """D-1: 딥 리서치 3키 모두 비어있지 않은 기본 지침 문자열을 반환한다."""
    for key in ("deep_research_duplication", "deep_research_discovery", "deep_research_proposal"):
        default = get_default(key)  # type: ignore[arg-type]
        assert isinstance(default, str) and len(default) > 0, f"{key} default 비어있음"


def test_D2_deep_research_risk_is_medium() -> None:
    """D-2: 결과물 품질에 직접 영향을 주므로 medium — high(재초기화 필요)는 아니다."""
    for key in ("deep_research_duplication", "deep_research_discovery", "deep_research_proposal"):
        assert get_risk(key) == "medium"  # type: ignore[arg-type]


def test_D3_deep_research_defaults_are_distinct() -> None:
    """D-3: 세 모드가 실수로 같은 기본값을 공유하면 안 된다(복붙 실수 방지)."""
    dup = get_default("deep_research_duplication")
    disc = get_default("deep_research_discovery")
    prop = get_default("deep_research_proposal")
    assert len({dup, disc, prop}) == 3


def test_D4_deep_research_effective_prompt_custom_override() -> None:
    """D-4: 다른 키들과 동일하게 커스텀 우선 → 기본값 폴백이 성립한다."""
    from agent_prompts.registry import effective_prompt

    mock_app_cfg = MagicMock()
    mock_app_cfg.agent_prompts = MagicMock()
    mock_app_cfg.agent_prompts.deep_research_proposal = "커스텀 계획서 지침"

    assert effective_prompt("deep_research_proposal", mock_app_cfg, None) == "커스텀 계획서 지침"

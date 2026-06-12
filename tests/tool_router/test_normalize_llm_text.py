# tests/tool_router/test_normalize_llm_text.py
"""E-44 회귀 테스트 — save_knowledge_note summary 이스케이프 정규화."""

from __future__ import annotations

from tool_router.router import _normalize_llm_text


def test_literal_backslash_n_restored() -> None:
    """실제 줄바꿈 없이 literal \\n만 있으면 줄바꿈으로 복원 (E-44)."""
    raw = "## 상황\\n보고를 받았습니다.\\n## 절차\\n자료를 검토했습니다."
    assert _normalize_llm_text(raw) == "## 상황\n보고를 받았습니다.\n## 절차\n자료를 검토했습니다."


def test_real_newlines_kept_intact() -> None:
    """정상적인 줄바꿈이 이미 있으면 literal \\n도 본문 일부로 보존."""
    raw = "첫 줄\n경로는 C:\\new_folder 입니다"
    assert _normalize_llm_text(raw) == raw


def test_escaped_underscore_and_trailing_quote() -> None:
    raw = '파일명은 보고서\\_v2.pptx 입니다.\\"'
    assert _normalize_llm_text(raw) == "파일명은 보고서_v2.pptx 입니다."


def test_clean_text_unchanged() -> None:
    raw = "## 상황\n정상 텍스트"
    assert _normalize_llm_text(raw) == raw


# ── E-46: title 누락 자동 보정 ────────────────────────────────────────────────


async def test_e46_missing_title_derived_from_summary(router) -> None:
    """title 누락 시 summary 첫 줄로 보정되어 스키마 검증을 통과한다 (E-46).

    knowledge 서비스가 없는 픽스처이므로 service_unavailable에 도달하면
    invalid_arguments 단계(검증)를 통과했다는 증거다.
    """
    result = await router.dispatch(
        "save_knowledge_note",
        {
            "summary": "## AI 시스템 구축 회의 결과\n상세 내용...",
            "tags": ["회의"],
        },
    )
    assert result.error_code != "invalid_arguments"


async def test_e46_empty_summary_and_title_still_normalized(router) -> None:
    """summary도 비면 기본 제목 '업무 노트'로 보정 — invalid_arguments는 아니다."""
    result = await router.dispatch(
        "save_knowledge_note",
        {"summary": "내용", "title": "  "},
    )
    assert result.error_code != "invalid_arguments"

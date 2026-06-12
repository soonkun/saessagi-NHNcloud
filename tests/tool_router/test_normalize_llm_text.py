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

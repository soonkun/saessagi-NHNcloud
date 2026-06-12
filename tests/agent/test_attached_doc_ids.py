# tests/agent/test_attached_doc_ids.py
"""E-44 회귀 테스트 — 첨부 doc_id 추출 (공백 포함 파일명)."""

from __future__ import annotations

from agent.upstream_adapter import _extract_attached_doc_ids


def test_doc_id_with_spaces_extracted_fully() -> None:
    """공백 포함 한글 파일명 doc_id가 잘리지 않고 전체 추출돼야 한다 (E-44)."""
    text = (
        "[첨부 자료: AI 이삭이 서비스 고도화 중간보고_260609_v2.0.pptx "
        "(doc_id: AI 이삭이 서비스 고도화 중간보고_260609_v2.0.pptx_d340e9cd)]\n"
        "오늘 업체로부터 중간 보고를 받았어"
    )
    assert _extract_attached_doc_ids(text) == [
        "AI 이삭이 서비스 고도화 중간보고_260609_v2.0.pptx_d340e9cd"
    ]


def test_doc_id_without_spaces() -> None:
    text = (
        "[첨부 자료: 회의결과보고서_fdc67831.hwpx (doc_id: 회의결과보고서_fdc67831.hwpx_7bf0958b)]"
    )
    assert _extract_attached_doc_ids(text) == ["회의결과보고서_fdc67831.hwpx_7bf0958b"]


def test_multiple_attachments_semicolon_separated() -> None:
    text = (
        "[첨부 자료: 보고서 초안 v1.pptx (doc_id: 보고서 초안 v1.pptx_aaaa1111); "
        "예산표.docx (doc_id: 예산표.docx_bbbb2222)]\n정리해줘"
    )
    assert _extract_attached_doc_ids(text) == [
        "보고서 초안 v1.pptx_aaaa1111",
        "예산표.docx_bbbb2222",
    ]


def test_duplicate_doc_ids_deduped() -> None:
    text = "(doc_id: dup.pdf_cccc3333) ... (doc_id: dup.pdf_cccc3333)"
    assert _extract_attached_doc_ids(text) == ["dup.pdf_cccc3333"]


def test_no_attachment_returns_empty() -> None:
    assert _extract_attached_doc_ids("그냥 일반 질문이야") == []

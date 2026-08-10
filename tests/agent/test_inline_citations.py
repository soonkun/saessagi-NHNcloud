# tests/agent/test_inline_citations.py
"""CR-64 / E-103: 인용 마커를 본문 제자리에 살린다.

사용자 지적 두 가지.
1. "대화방을 나갔다가 다시 들어오면 근거 문서 링크가 사라져" — 칩이 런타임 상태라
   히스토리 복원 시 사라졌다. 마커가 **본문에 남으면** 저절로 복원된다.
2. "그냥 <근거> 이렇게만 나오고 누르면 문서를 열어주던지" — 긴 파일명이 문장 사이에
   박혀 본문을 읽을 수 없었다.

실측: 저장 이력의 이중괄호 16건 중 11건이 `doc:` 접두사 없는 `[[TRKO….pdf_abc]]`였고,
제거 정규식이 접두사를 요구해 **하나도 안 걸러진 채** 화면에 날것으로 떴다.
"""

from __future__ import annotations

from agent.upstream_adapter import (
    _normalize_doc_marker,
    _resolve_inline_markers,
    _strip_llm_markers,
)

_DOC = "TRKO202500017741_농업분야기후변화실태조사.pdf_afbc4f63"
_MARKERS = [f"[[doc:{_DOC}]]", "[[note:my-note]]"]


def test_bare_marker_is_revived_not_dropped() -> None:
    """접두사를 빼먹어도 doc_id가 맞으면 살린다 — 지우면 근거 링크가 사라진다."""
    text = f"기후변화 대응이 필요합니다. [[{_DOC}]]"
    out, kept = _resolve_inline_markers(text, [f"[[doc:{_DOC}]]"])
    assert kept == 1
    assert f"[[doc:{_DOC}]]" in out


def test_marker_stays_in_place() -> None:
    """마커는 **쓰인 자리**에 남아야 한다 — 끝에 몰면 어느 문장 근거인지 알 수 없다."""
    text = f"첫 문단입니다. [[doc:{_DOC}]]\n\n둘째 문단입니다."
    out, kept = _resolve_inline_markers(text, [f"[[doc:{_DOC}]]"])
    assert kept == 1
    assert out.index("[[doc:") < out.index("둘째 문단")


def test_hallucinated_document_is_removed() -> None:
    """검색 결과에 없는 문서명은 지운다 — 없는 자료로 링크를 만들면 안 된다."""
    text = "그럴듯한 주장입니다. [[doc:존재하지않는보고서.pdf_deadbeef]]"
    out, _ = _resolve_inline_markers(text, [f"[[doc:{_DOC}]]"])
    assert "존재하지않는" not in out, "없는 문서로 링크를 만들면 안 된다"
    # 본문 인용은 사라졌지만 실제 검색된 자료는 끝에 보태져 접근 경로가 남는다
    assert f"[[doc:{_DOC}]]" in out


def test_corrupted_filename_is_repaired() -> None:
    """LLM이 파일명을 조금 깨뜨려도 후보가 하나면 교정한다.

    실측 사례: `신기후변화대응체계구축사업최종·종보고서.pdf` (가운뎃점 삽입).
    """
    broken = _DOC.replace("실태조사", "실태·조사")
    out, kept = _resolve_inline_markers(f"본문. [[doc:{broken}]]", [f"[[doc:{_DOC}]]"])
    assert kept == 1, "정규화 비교로 교정했어야 한다"
    assert f"[[doc:{_DOC}]]" in out


def test_note_marker_survives() -> None:
    out, kept = _resolve_inline_markers("노트 참고. [[note:my-note]]", ["[[note:my-note]]"])
    assert kept == 1 and "[[note:my-note]]" in out


def test_tts_text_has_no_markers() -> None:
    """음성 낭독본에는 마커가 남으면 안 된다 — 파일명을 읽어 버린다."""
    text = f"본문입니다. [[doc:{_DOC}]] 그리고 [[{_DOC}]] 또 [[note:my-note]]"
    clean = _strip_llm_markers(text)
    assert "[[" not in clean and ".pdf" not in clean


def test_normalize_adds_prefix() -> None:
    assert "[[doc:" in _normalize_doc_marker(f"[[{_DOC}]]")


def test_repeats_are_kept_for_each_section() -> None:
    """같은 자료가 여러 섹션의 근거면 **그대로 둔다** (CR-64 재조정).

    메시지 전체 단위로 중복을 지웠더니 칩이 확 줄었다는 지적을 받았다 —
    한 자료가 3개 섹션을 뒷받침해도 첫 섹션에만 남았다.
    같은 섹션 안의 중복 정리는 프론트(`groupMarkersByBlock`)가 맡는다.
    """
    text = f"첫째. [[doc:{_DOC}]]\n둘째. [[doc:{_DOC}]]"
    out, _ = _resolve_inline_markers(text, [f"[[doc:{_DOC}]]"])
    assert out.count("[[doc:") == 2


def test_uncited_documents_are_appended() -> None:
    """모델이 빠뜨린 자료는 끝에 보탠다 — 버리면 접근 경로가 사라진다."""
    other = "다른보고서.pdf_beef"
    markers = [f"[[doc:{_DOC}]]", f"[[doc:{other}]]"]
    out, kept = _resolve_inline_markers(f"본문. [[doc:{_DOC}]]", markers)
    assert f"[[doc:{other}]]" in out, "인용 안 된 자료가 사라졌다"
    assert out.index(_DOC) < out.index(other), "본문 인용이 앞, 보탠 것이 뒤여야 한다"
    assert kept == 2


# ── 재진입 (E-106) ────────────────────────────────────────────────────────────

_BRACKET_DOC = "[이암허브]농식품R&BD기획지원사업 최종보고서.pdf_e3072e8b"


def test_bracketed_filename_marker_is_matched() -> None:
    """파일명에 대괄호가 있어도 마커로 인식한다 (E-106).

    `[^\\[\\]]`로 두면 `[이암허브]`에서 매치가 끊겨 마커가 화면에 날것으로 남았다.
    """
    out, kept = _resolve_inline_markers(
        f"본문. [[doc:{_BRACKET_DOC}]]", [f"[[doc:{_BRACKET_DOC}]]"]
    )
    assert kept == 1
    assert "이암허브" in out and out.count("[[doc:") == 1


def test_bracketed_marker_stripped_from_tts() -> None:
    """음성 낭독본에는 남으면 안 된다 — 파일명을 읽어 버린다."""
    clean = _strip_llm_markers(f"본문. [[doc:{_BRACKET_DOC}]]")
    assert "이암허브" not in clean and "[[" not in clean


def test_emotion_tags_removed_from_memory() -> None:
    """감정 태그는 화면·LLM 메모리 어디에도 남으면 안 된다 (E-106).

    실측: `[study] 자료를 찾아볼게요![neutral] 갈색거저리는…` 이 그대로 저장돼
    재진입 시 사용자 화면에 떴고 LLM 자기 발화에도 들어갔다.
    """
    from agent.upstream_adapter import _clean_for_memory

    out = _clean_for_memory("[study] 자료를 찾아볼게요![neutral] 갈색거저리는 고단백입니다.")
    assert "[study]" not in out and "[neutral]" not in out
    assert "갈색거저리는 고단백입니다." in out


def test_clean_for_memory_keeps_ordinary_brackets() -> None:
    """감정 태그가 아닌 대괄호 표현은 지우지 않는다 — 본문이 훼손된다."""
    from agent.upstream_adapter import _clean_for_memory

    text = "보고서[별지 제19호]에 따르면 수치는 48.26g입니다."
    assert _clean_for_memory(text) == text

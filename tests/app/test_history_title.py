"""CR-53: 대화 목록 제목이 첫 사용자 질문에서 만들어지는가.

예전에는 마지막 메시지(대개 답변)를 썼다. 답변이 "자료를 찾아볼게요!"로 시작하다 보니
목록의 여러 대화가 전부 같은 문구로 보여 구분되지 않았다.
"""

from __future__ import annotations

from app.history_title import clean_title_text, history_title


def test_uses_first_human_message() -> None:
    msgs = [
        {"role": "human", "content": "기후변화 대응방안에 관해 정리해줘"},
        {"role": "ai", "content": "[study] 자료를 찾아볼게요! ## 기후변화 농업 대응..."},
        {"role": "human", "content": "짧게 정리해줘"},
    ]
    assert history_title(msgs) == "기후변화 대응방안에 관해 정리해줘"


def test_ignores_answer_even_if_it_comes_first_in_file() -> None:
    """실제 사고: 답변만 든 히스토리가 "자료를 찾아볼게요!"로 표시됐다."""
    msgs = [
        {"role": "ai", "content": "[study] 자료를 찾아볼게요!"},
        {"role": "human", "content": "가축사양표준 알려줘"},
    ]
    assert history_title(msgs) == "가축사양표준 알려줘"


def test_strips_attachment_meta() -> None:
    """첨부 메타는 우리가 붙인 것이라 제목에 나오면 안 된다."""
    msgs = [{"role": "human", "content": "[첨부 자료: 보고서.pdf (doc_id: x)]\n이거 정리해줘"}]
    assert history_title(msgs) == "이거 정리해줘"


def test_strips_emotion_tags_and_markers() -> None:
    assert clean_title_text("[study] 답변 [[doc:abc.pdf_123]]") == "답변"


def test_falls_back_to_ai_when_no_human_message() -> None:
    """사용자 발화 없이 답변만 남은 대화도 빈칸으로 두지 않는다."""
    msgs = [{"role": "ai", "content": "안녕하세요! 무엇을 도와드릴까요?"}]
    assert history_title(msgs) == "안녕하세요! 무엇을 도와드릴까요?"


def test_empty_history() -> None:
    assert history_title([]) == ""


def test_truncates_long_question() -> None:
    msgs = [{"role": "human", "content": "가" * 200}]
    assert len(history_title(msgs)) == 60

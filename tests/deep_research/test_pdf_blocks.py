# tests/deep_research/test_pdf_blocks.py
"""CR-68 마크다운 → 블록 파싱.

PDF 레이아웃 계층을 마크다운 생성 로직과 분리하기 위한 중간 표현. 여기서 구조를
잘못 읽으면 렌더러가 아무리 정교해도 문서가 무너진다.
"""

from __future__ import annotations

from deep_research.pdf_blocks import (
    Divider,
    Heading,
    ListBlock,
    Paragraph,
    Table,
    parse_blocks,
)


def test_heading_levels() -> None:
    b = parse_blocks("# 하나\n\n## 둘\n\n### 셋\n")
    assert [(x.level, x.text) for x in b if isinstance(x, Heading)] == [
        (1, "하나"),
        (2, "둘"),
        (3, "셋"),
    ]


def test_table_with_alignment_row() -> None:
    md = "| 구분 | 내용 |\n| :--- | ---: |\n| 예측 | 작물 모형 |\n| 위험 | 조기경보 |\n"
    t = next(x for x in parse_blocks(md) if isinstance(x, Table))
    assert t.header == ["구분", "내용"]
    assert t.rows == [["예측", "작물 모형"], ["위험", "조기경보"]]


def test_table_pads_short_rows() -> None:
    """열 수가 모자란 행이 있어도 렌더러가 터지지 않게 맞춘다."""
    md = "| a | b | c |\n|---|---|---|\n| 1 | 2 |\n"
    t = next(x for x in parse_blocks(md) if isinstance(x, Table))
    assert t.rows == [["1", "2", ""]]


def test_pipe_line_without_separator_is_paragraph() -> None:
    """구분선이 없으면 표가 아니다 — 본문의 `|`를 표로 오인하면 안 된다."""
    b = parse_blocks("검토 결과 | 판단 근거는 다음과 같다.\n")
    assert isinstance(b[0], Paragraph)


def test_bullet_and_ordered_lists_are_separate() -> None:
    b = parse_blocks("- 하나\n- 둘\n\n1. 첫째\n2. 둘째\n")
    lists = [x for x in b if isinstance(x, ListBlock)]
    assert len(lists) == 2
    assert lists[0].ordered is False and lists[1].ordered is True
    assert [t for _, t in lists[1].items] == ["첫째", "둘째"]


def test_nested_bullet_depth() -> None:
    b = parse_blocks("- 상위\n  - 하위\n")
    lst = next(x for x in b if isinstance(x, ListBlock))
    assert [d for d, _ in lst.items] == [0, 1]


def test_paragraph_lines_join() -> None:
    """빈 줄 전까지는 한 문단이다 — 줄마다 끊으면 문단 간격이 어긋난다."""
    b = parse_blocks("첫 줄\n이어지는 줄\n\n다음 문단\n")
    paras = [x.text for x in b if isinstance(x, Paragraph)]
    assert paras == ["첫 줄 이어지는 줄", "다음 문단"]


def test_horizontal_rule() -> None:
    assert any(isinstance(x, Divider) for x in parse_blocks("본문\n\n---\n\n다음\n"))


def test_inline_bold_is_preserved_for_renderer() -> None:
    """강조는 여기서 지우지 않는다 — fpdf2가 `markdown=True`로 처리한다."""
    b = parse_blocks("**과제명**: 미래기후\n")
    assert isinstance(b[0], Paragraph) and "**과제명**" in b[0].text

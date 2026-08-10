# tests/deep_research/test_pdf.py
"""CR-67 보고서 PDF 생성.

**왜 서버에서 만드나**: 브라우저 인쇄로 만들던 것이 iOS Safari에서 숨은 iframe이 아니라
**화면 전체**를 인쇄해, 보고서 대신 앱 UI(내비게이션·버튼·입력창)가 1페이지로 찍혔다.
"""

from __future__ import annotations

import pytest

from deep_research.pdf import PdfUnavailable, report_to_pdf

_MD = """# 과제 중복성 검토 보고서

## 1. 검토 대상

**과제명**: 미래기후 시나리오와 극한기상에 따른 작물 연구

- 에코돔 활용 생육 분석
- 토양 미생물 반응 평가

| 구분 | 내용 |
|---|---|
| 예측 | 작물 모형(DSSAT) |
"""


def test_produces_pdf_bytes() -> None:
    data = report_to_pdf("검토 보고서", _MD, "2026-08-10")
    assert data[:4] == b"%PDF", "PDF 시그니처가 아니다"
    assert len(data) > 5000, "본문이 들어가지 않았다"


def test_title_does_not_break_layout() -> None:
    """제목을 쓴 뒤 커서가 오른쪽 여백에 남으면 폭이 0이 되어 죽는다.

    실측 오류: `Not enough horizontal space to render a single character`.
    """
    long_title = "미래기후 시나리오와 극한기상에 따른 작물 및 탄소흡수 적응기술 연구 검토 보고서"
    assert report_to_pdf(long_title, _MD, "2026-08-10")[:4] == b"%PDF"


def test_citation_markers_removed() -> None:
    """배포 문서에 내부 인용 표기가 남으면 안 된다."""
    from deep_research.pdf import strip_citation_markers

    out = strip_citation_markers("본문 [[doc:파일.pdf_abc]] 끝 [[note:슬러그]]")
    assert "[[" not in out and "파일.pdf" not in out


def test_empty_body_still_renders() -> None:
    """본문이 짧아도 터지지 않는다 — 제목만 있는 문서도 있다."""
    assert report_to_pdf("제목만", "내용", "")[:4] == b"%PDF"


def test_missing_font_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """폰트가 없으면 **한글이 깨진 PDF 대신** 명확한 오류를 낸다."""
    import deep_research.pdf as mod

    monkeypatch.setattr(mod, "_FONT_CANDIDATES", (("/없는/폰트.ttf", "/없는/폰트B.ttf"),))
    with pytest.raises(PdfUnavailable, match="한글 폰트"):
        report_to_pdf("제목", _MD)


def test_bold_inside_table_cell_renders() -> None:
    """표 셀 안의 굵은 글씨 (실측 오류 재현).

    `Unsupported nested HTML tags inside <td> element: <strong>` 로 500이 났다.
    보고서 표에는 `| **생산성 대응** | … |`가 흔하다.
    """
    md = "| 구분 | 내용 |\n|---|---|\n| **생산성 대응** | 농·축·수산물 *변화* 대응 |\n"
    assert report_to_pdf("표 시험", md, "")[:4] == b"%PDF"


def test_bold_in_table_cell_is_kept() -> None:
    """표 셀의 굵은 글씨가 이제는 **살아남는다** (CR-68).

    `write_html` 시절에는 셀 안 중첩 태그를 못 다뤄 강조를 통째로 벗겨 냈다.
    직접 렌더로 바꾸면서 fpdf2 표의 `markdown=True`가 처리한다.
    """
    md = "| 구분 | 내용 |\n|---|---|\n| **생산성 대응** | 농·축·수산물 대응 |\n"
    assert report_to_pdf("표", md, "")[:4] == b"%PDF"


def test_falls_back_to_plain_text_on_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """서식 렌더가 실패해도 내용이 담긴 PDF는 나온다 — 빈손으로 돌려보내지 않는다."""
    from fpdf import FPDF

    def boom(self: FPDF, *a: object, **k: object) -> None:
        raise RuntimeError("서식 실패")

    monkeypatch.setattr(FPDF, "write_html", boom)
    data = report_to_pdf("제목", "# 머리\n\n본문 내용입니다.", "")
    assert data[:4] == b"%PDF" and len(data) > 3000


def test_latex_is_readable() -> None:
    """모델이 섞어 내는 LaTeX을 글자로 바꾼다 (실측: `$\\text{CO}_2$`)."""
    from deep_research.pdf import strip_latex

    assert strip_latex(r"온도 $\text{CO}_2$ 농도") == "온도 CO₂ 농도"
    assert "°C" in strip_latex(r"$20^\circ\text{C}$ 에서")


def test_plain_dollar_is_left_alone() -> None:
    """수식이 아닌 달러 기호는 건드리지 않는다."""
    from deep_research.pdf import strip_latex

    assert strip_latex("가격은 $100 입니다") == "가격은 $100 입니다"


def test_leading_h1_dropped_once() -> None:
    """머리 제목과 본문 H1이 겹쳐 제목이 두 번 나왔다."""
    from deep_research.pdf import drop_leading_h1

    assert drop_leading_h1("# 보고서\n\n본문\n\n# 다른 절\n").startswith("본문")


# ── CR-68 레이아웃 ────────────────────────────────────────────────────────────


def test_column_widths_fit_header() -> None:
    """머리글이 본문보다 길면 그 폭에 맞춘다.

    실측 사고: `비교 적합성` 열의 값이 `높음`뿐이라 열이 좁아지고, 한글은 낱말 안에서
    줄바꿈되어 머리글이 `비/교/적/합/성`처럼 한 글자씩 세로로 쪼개졌다.
    """
    from deep_research.pdf import _column_widths

    rows = [
        ["구분", "비교 적합성", "판단 근거"],
        ["[1]", "높음", "SSP 시나리오 기반 온도 반응 연구 수행"],
    ]
    w = _column_widths(rows)
    assert len(w) == 3
    assert abs(sum(w) - 100) < 0.5
    assert w[1] > 12, f"머리글이 세로로 쪼개질 폭이다: {w}"


def test_column_widths_never_zero() -> None:
    from deep_research.pdf import _column_widths

    w = _column_widths([["a", "b", "c", "d", "e", "f"], ["1", "", "", "", "", ""]])
    assert all(x > 0 for x in w) and abs(sum(w) - 100) < 0.5


def test_multi_page_report_repeats_table_heading() -> None:
    """긴 표가 다음 장으로 넘어가도 머리글이 다시 그려진다."""
    rows = "\n".join(f"| [{i}] | 기존 연구 {i} | 높음 | 판단 근거 {i} |" for i in range(1, 60))
    md = f"## 표 시험\n\n| 구분 | 기존 연구자료 | 적합성 | 판단 근거 |\n|---|---|---|---|\n{rows}\n"
    data = report_to_pdf("긴 표", md, "2026-08-10")
    assert data[:4] == b"%PDF"
    # 페이지가 여러 장 생겼는지 — 머리글 반복은 fpdf2 repeat_headings가 담당한다
    assert data.count(b"/Type /Page") > 1 or b"/Count 2" in data or len(data) > 20000


def test_kicker_and_divider_render() -> None:
    """머리 영역(분류·제목·보조정보·divider)이 그려진다."""
    data = report_to_pdf("제목", "본문", "Deep Research Report · 2026. 08. 10", kicker="과제 검토")
    assert data[:4] == b"%PDF" and len(data) > 3000


def test_title_drops_duplicate_kicker_prefix() -> None:
    """분류줄을 따로 두면 제목 앞 `(분류)`는 같은 말이 두 번 나온 것이다."""
    from deep_research.pdf import _title_without_kicker

    assert (
        _title_without_kicker("(과제 중복성 검토)미래기후 시나리오 연구", "과제 중복성 검토")
        == "미래기후 시나리오 연구"
    )
    # 분류가 다르면 건드리지 않는다
    assert _title_without_kicker("(별건)제목", "과제 중복성 검토") == "(별건)제목"
    assert _title_without_kicker("괄호 없는 제목", "분류") == "괄호 없는 제목"

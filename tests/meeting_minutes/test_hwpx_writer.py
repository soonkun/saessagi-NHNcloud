# tests/meeting_minutes/test_hwpx_writer.py
"""M_13 MeetingMinutes HwpxWriter 테스트 (N-3, A-4)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from meeting_minutes.errors import HwpxTemplateError
from meeting_minutes.hwpx_writer import HwpxWriter
from meeting_minutes.types import (
    DetailItem,
    MeetingDraft,
    NextStepItem,
    SubItem,
    SummaryItem,
)


@pytest.fixture
def sample_draft() -> MeetingDraft:
    """테스트용 MeetingDraft 픽스처."""
    return MeetingDraft(
        title="2026년 4월 농업정책 주간 회의 결과",
        date="2026.04.23.",
        department="농업정책과",
        place="3층 회의실",
        attendees=("홍길동", "김철수", "이영희"),
        datetime_place="2026.04.23.(목) 14:00~15:00, 3층 회의실",
        attendees_str="홍길동 과장 외 2명",
        summary_items=(
            SummaryItem(
                text="스마트팜 보급 확대 추진 계획 논의",
                subs=(
                    SubItem(text="총 예산 50억 원 규모로 20개 시군 대상"),
                    SubItem(
                        text="4월 말 공모, 5월 대상지 선정",
                        detail="5.15. 신청 마감, 6월 초 현장 조사",
                    ),
                ),
            ),
            SummaryItem(text="농업인 교육 프로그램 개편 방향 검토", subs=()),
        ),
        detail_items=(
            DetailItem(
                text="스마트팜 보급 세부 일정 확정",
                subs=(SubItem(text="4.25. 공모 공고, 5.15. 신청 마감"),),
            ),
        ),
        next_steps=(
            NextStepItem(text="스마트팜 공모 공고문 배포", date="4.25."),
            NextStepItem(text="교육 개편안 1차 초안 제출", date="5.31."),
        ),
        pages=1,
    )


# ── 정상 케이스 ──────────────────────────────────────────────


def test_hwpx_writer_init(template_path: Path) -> None:
    """N: 유효한 템플릿으로 HwpxWriter 초기화 성공."""
    writer = HwpxWriter(template_path)
    assert writer is not None


def test_n3_no_placeholder_remaining(
    template_path: Path,
    sample_draft: MeetingDraft,
    tmp_path: Path,
) -> None:
    """N-3: 생성된 HWPX ZIP의 section0.xml에 {{ 가 잔존하지 않음."""
    writer = HwpxWriter(template_path)
    out_path = tmp_path / "output.hwpx"
    writer.write(sample_draft, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 1000

    with zipfile.ZipFile(out_path, "r") as zf:
        section_xml = zf.read("Contents/section0.xml")

    assert b"{{" not in section_xml, "placeholder가 아직 잔존합니다"


def test_write_contains_title_text(
    template_path: Path,
    sample_draft: MeetingDraft,
    tmp_path: Path,
) -> None:
    """N: 생성된 HWPX section0.xml에 title 텍스트가 포함된다."""
    writer = HwpxWriter(template_path)
    out_path = tmp_path / "output.hwpx"
    writer.write(sample_draft, out_path)

    with zipfile.ZipFile(out_path, "r") as zf:
        section_xml = zf.read("Contents/section0.xml").decode("utf-8")

    assert "2026년 4월 농업정책 주간 회의 결과" in section_xml


def test_write_contains_summary_text(
    template_path: Path,
    sample_draft: MeetingDraft,
    tmp_path: Path,
) -> None:
    """N: 생성된 HWPX section0.xml에 summary_items 텍스트가 포함된다."""
    writer = HwpxWriter(template_path)
    out_path = tmp_path / "output.hwpx"
    writer.write(sample_draft, out_path)

    with zipfile.ZipFile(out_path, "r") as zf:
        section_xml = zf.read("Contents/section0.xml").decode("utf-8")

    assert "스마트팜 보급 확대 추진 계획 논의" in section_xml
    assert "농업인 교육 프로그램 개편 방향 검토" in section_xml


def test_write_mimetype_is_first_and_stored(
    template_path: Path,
    sample_draft: MeetingDraft,
    tmp_path: Path,
) -> None:
    """N: mimetype 엔트리가 첫 번째이고 ZIP_STORED 방식."""
    writer = HwpxWriter(template_path)
    out_path = tmp_path / "output.hwpx"
    writer.write(sample_draft, out_path)

    with zipfile.ZipFile(out_path, "r") as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED


def test_write_empty_next_steps(
    template_path: Path,
    tmp_path: Path,
) -> None:
    """E-4: next_steps가 빈 경우 HWPX 생성 성공, {{ 잔존 없음."""
    draft = MeetingDraft(
        title="빈 향후계획 테스트",
        date="2026.04.23.",
        department="테스트과",
        place="회의실",
        attendees=("홍길동",),
        datetime_place="2026.04.23.(목) 14:00~15:00, 회의실",
        attendees_str="홍길동",
        summary_items=(SummaryItem(text="주요 항목", subs=()),),
        detail_items=(),
        next_steps=(),
        pages=1,
    )
    writer = HwpxWriter(template_path)
    out_path = tmp_path / "empty_next.hwpx"
    writer.write(draft, out_path)

    with zipfile.ZipFile(out_path, "r") as zf:
        section_xml = zf.read("Contents/section0.xml")

    assert b"{{" not in section_xml


# ── 적대적 케이스 ──────────────────────────────────────────────


def test_a4_nonexistent_template() -> None:
    """A-4: 존재하지 않는 템플릿 파일 → HwpxTemplateError."""
    with pytest.raises(HwpxTemplateError):
        HwpxWriter(Path("not_existing_file.hwpx"))


def test_a4_not_a_zip(tmp_path: Path) -> None:
    """A-4: ZIP이 아닌 파일 → HwpxTemplateError."""
    fake_file = tmp_path / "not_a_zip.txt"
    fake_file.write_bytes(b"This is not a zip file at all")

    with pytest.raises(HwpxTemplateError):
        HwpxWriter(fake_file)


def test_write_to_nonexistent_parent(
    template_path: Path,
    sample_draft: MeetingDraft,
) -> None:
    """A: 존재하지 않는 부모 디렉토리 → FileNotFoundError."""
    writer = HwpxWriter(template_path)
    out_path = Path("/nonexistent/path/output.hwpx")

    with pytest.raises(FileNotFoundError):
        writer.write(sample_draft, out_path)


def test_multiple_writes_independent(
    template_path: Path,
    sample_draft: MeetingDraft,
    tmp_path: Path,
) -> None:
    """N: 동일 writer로 여러 번 write() 호출해도 결과가 독립적."""
    writer = HwpxWriter(template_path)
    out1 = tmp_path / "out1.hwpx"
    out2 = tmp_path / "out2.hwpx"

    writer.write(sample_draft, out1)
    writer.write(sample_draft, out2)

    with zipfile.ZipFile(out1, "r") as z1, zipfile.ZipFile(out2, "r") as z2:
        xml1 = z1.read("Contents/section0.xml")
        xml2 = z2.read("Contents/section0.xml")

    # 두 파일의 section0.xml이 동일해야 함
    assert xml1 == xml2
    assert b"{{" not in xml1

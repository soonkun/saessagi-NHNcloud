# tests/kg/test_projects.py
"""M_23 과제 동일성 테스트 (스펙 §5.2, §4.1-D).

이 모듈의 테스트는 **한계를 고정하는 것**이 절반이다. 현재 데이터로는 계획서와
완결보고서를 잇지 못한다(RFP 1,984건이 project_no·rfp_no 둘 다 비어 있다). 그 사실을
테스트로 박아 두는 이유는, 나중에 누군가 "제목이 비슷하니 묶자"는 유혹에 빠지지 않게
하기 위해서다 — 그건 `merge.py`가 이름 유사도 병합을 거부하는 바로 그 이유로 실패한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kg.candidates import CandidateStore, DocumentMeta
from kg.projects import SOURCE_DOC_SURROGATE, SOURCE_PROJECT_NO, project_id_for, resolve_projects


@pytest.fixture
def store(tmp_path: Path) -> CandidateStore:
    return CandidateStore(tmp_path / "kg.db")


def _meta(doc_id: str, **kw: object) -> DocumentMeta:
    base: dict[str, object] = {
        "doc_id": doc_id,
        "doc_name": f"{doc_id}.pdf",
        "document_type": "FINAL_REPORT",
        "year": 2024,
        "title": f"{doc_id} 과제",
        "extract_state": "COMPLETED",
    }
    base.update(kw)
    return DocumentMeta(**base)  # type: ignore[arg-type]


def test_project_no_becomes_project_id() -> None:
    pid, source = project_id_for(_meta("D1", project_no="PJ014913"))
    assert pid == "pj:PJ014913"
    assert source == SOURCE_PROJECT_NO


def test_missing_number_falls_back_to_document() -> None:
    """번호가 없으면 문서 자신이 과제 대신이다 — 억지로 다른 문서에 붙이지 않는다."""
    pid, source = project_id_for(_meta("D1"))
    assert pid == "doc:D1"
    assert source == SOURCE_DOC_SURROGATE


def test_same_project_no_groups_documents(store: CandidateStore) -> None:
    """번호가 같으면 계획서와 완결보고서가 한 과제로 묶인다."""
    store.upsert_document(_meta("PLAN", document_type="RFP", project_no="PJ001", year=2022))
    store.upsert_document(_meta("REPORT", project_no="PJ001", year=2024))
    projects = resolve_projects(store)
    assert len(projects) == 1
    assert projects[0].document_count == 2
    # 시작연도는 가장 이른 문서(계획서) 것
    assert projects[0].year == 2022


def test_final_report_title_wins(store: CandidateStore) -> None:
    """RFP 제목은 공고문 제목이라 과제명과 다를 수 있다 — 완결보고서 것을 대표로."""
    store.upsert_document(
        _meta("PLAN", document_type="RFP", project_no="PJ002", title="2022년 공모 과제 공고")
    )
    store.upsert_document(_meta("REPORT", project_no="PJ002", title="벼 도열병 저항성 품종 개발"))
    projects = resolve_projects(store)
    assert projects[0].title == "벼 도열병 저항성 품종 개발"


def test_rfp_without_numbers_stays_separate(store: CandidateStore) -> None:
    """**한계 고정.** 번호 없는 RFP는 완결보고서와 묶이지 않는다 (스펙 §4.1-D).

    제목이 비슷해도 묶지 않는다. 이 동작이 바뀐다면 그것은 근거를 갖춘 별도 설계여야 한다.
    """
    store.upsert_document(_meta("RFP1", document_type="RFP", title="벼 품종 개발 연구"))
    store.upsert_document(_meta("REP1", title="벼 품종 개발 연구"))
    projects = resolve_projects(store)
    assert len(projects) == 2, "번호 없는 문서가 제목만으로 병합됐다"


def test_persist_writes_back_to_documents(store: CandidateStore) -> None:
    store.upsert_document(_meta("D1", project_no="PJ003"))
    resolve_projects(store, persist=True)
    assert store.get_document("D1").project_id == "pj:PJ003"  # type: ignore[union-attr]
    assert store.get_document("D1").project_id_source == SOURCE_PROJECT_NO  # type: ignore[union-attr]


def test_resolve_is_deterministic(store: CandidateStore) -> None:
    for i in range(5):
        store.upsert_document(_meta(f"D{i}", project_no=f"PJ00{i}" if i % 2 else ""))
    first = [p.project_id for p in resolve_projects(store)]
    second = [p.project_id for p in resolve_projects(store)]
    assert first == second


class TestPlaceholderTitles:
    """E-93 회귀 — 서식 안내문이 과제명으로 새어 나오면 안 된다.

    `과제명: (해당 시 작성)` 처럼 **채우라고 비워 둔 칸**을 제목으로 잡아 왔다.
    실측 663건이 `(해당 시 작성)`, 서식 문구 제목 합계 686/11,276(6.1%).
    그래프 화면에 `(해당 시 작성)` 노드가 여럿 떠서 사용자가 물었다.
    """

    def test_detects_form_placeholders(self) -> None:
        from kg.identity import is_placeholder_title

        for bad in ("(해당 시 작성)", "해당 시 작성", "(내역사업명)", "단위사업명", "과제명", ""):
            assert is_placeholder_title(bad), f"{bad!r}를 서식 문구로 못 잡았다"

    def test_keeps_real_titles(self) -> None:
        """진짜 과제명을 서식으로 오판하면 제목이 통째로 사라진다 — 더 나쁘다."""
        from kg.identity import is_placeholder_title

        for good in (
            "벼 도열병 저항성 품종 개발",
            "메타버스 기술을 이용한 가상농장 개발",
            "차세대 농작물 신육종기술 개발 사업",
        ):
            assert not is_placeholder_title(good), f"{good!r}를 서식으로 오판했다"

    def test_title_from_doc_name_strips_collector_id(self) -> None:
        from kg.projects import title_from_doc_name

        assert (
            title_from_doc_name("TRKO202100010370_한우이유시기와단백질수준에따른연구.pdf")
            == "한우이유시기와단백질수준에따른연구"
        )

    def test_project_title_falls_back_to_filename(self, store: CandidateStore) -> None:
        """**핵심** — 이미 저장된 서식 제목이 읽기 시점에 고쳐진다 (재추출 없이)."""
        store.upsert_document(
            _meta(
                "D1",
                title="(해당 시 작성)",
                doc_name="TRKO202100010370_메타버스기술을이용한가상농장개발.pdf",
            )
        )
        projects = resolve_projects(store, persist=False)
        assert projects[0].title == "메타버스기술을이용한가상농장개발"

    def test_real_title_wins_over_filename(self, store: CandidateStore) -> None:
        """제목이 멀쩡하면 파일명으로 덮어쓰지 않는다."""
        store.upsert_document(
            _meta("D1", title="벼 도열병 저항성 품종 개발", doc_name="TRKO1_아무거나.pdf")
        )
        projects = resolve_projects(store, persist=False)
        assert projects[0].title == "벼 도열병 저항성 품종 개발"


# ── 라벨 단독 접두 (E-99) ─────────────────────────────────────────────────────


def test_strips_bare_label_prefix() -> None:
    """안내문 없이 라벨만 앞에 붙은 것도 뗀다 (E-99).

    E-93은 `(해당 시 작성)`이 앞에 있는 형태만 봤는데, 실측하니 라벨 단독이
    훨씬 많았다 — 주관과제명 2,595건 등 제목의 23.6%.
    """
    from kg.identity import strip_placeholder_prefix

    assert strip_placeholder_prefix("주관과제명 무잔량 곡물건조기 개발") == "무잔량 곡물건조기 개발"
    assert strip_placeholder_prefix("단위사업명 차세대바이오그린21") == "차세대바이오그린21"
    assert strip_placeholder_prefix("사업명: 스마트팜 확산") == "스마트팜 확산"


def test_does_not_strip_content_words() -> None:
    """내용어와 문장 중간의 라벨은 건드리지 않는다 — 과도한 제거가 더 나쁘다."""
    from kg.identity import strip_placeholder_prefix

    assert strip_placeholder_prefix("농업과학기반기술연구") == "농업과학기반기술연구"
    assert strip_placeholder_prefix("벼 재배 사업명 개선") == "벼 재배 사업명 개선"


def test_label_only_title_is_not_emptied() -> None:
    """제목이 라벨뿐이면 빈 문자열로 만들지 않는다 — 판정은 is_placeholder_title 몫."""
    from kg.identity import is_placeholder_title, strip_placeholder_prefix

    assert strip_placeholder_prefix("과제명") == "과제명"
    assert is_placeholder_title("과제명") is True

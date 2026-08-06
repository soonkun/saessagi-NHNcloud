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

# tests/kg/test_documents.py
"""M_23 문서 메타 유도·청크 선별 테스트 (스펙 §3).

문서유형과 연도를 폴더에서 읽어내는 것이 이 설계의 전제다(현황조사 §2.2). 이게 틀리면
계획서와 완결보고서를 구분하지 못하고, 지침서가 요구한 "계획 대비 실적" 비교가 통째로
무너진다.

청크 선별은 전 청크 처리(900시간)를 피하기 위한 장치다. 여기서 잘못 고르면 LLM이 아무리
좋아도 건질 것이 없으므로, **과제명이 있는 첫머리를 반드시 포함하는지**를 특히 본다.
"""

from __future__ import annotations

from kg.documents import (
    EXCLUDED,
    DOC_TYPE_ANNUAL,
    DOC_TYPE_FINAL_REPORT,
    DOC_TYPE_PLAN,
    DOC_TYPE_RFP,
    DOC_TYPE_UNKNOWN,
    build_folder_index,
    classify_folder,
    score_chunk,
    select_chunks,
)


class TestFolderClassification:
    def test_final_report_folder(self) -> None:
        info = classify_folder("f1", "2025완결보고서")
        assert info.document_type == DOC_TYPE_FINAL_REPORT
        assert info.year == 2025

    def test_rfp_folder_with_year_range(self) -> None:
        info = classify_folder("f2", "RFP(2020-2025)")
        assert info.document_type == DOC_TYPE_RFP
        assert (info.year, info.year_end) == (2020, 2025)

    def test_plan_and_annual(self) -> None:
        assert classify_folder("f3", "2024연구개발계획서").document_type == DOC_TYPE_PLAN
        assert classify_folder("f4", "2023연차보고서").document_type == DOC_TYPE_ANNUAL

    def test_unknown_folder_is_not_guessed(self) -> None:
        """모르면 UNKNOWN이어야 한다 — 잘못 찍으면 계획/실적 구분이 오염된다."""
        info = classify_folder("f5", "기타자료")
        assert info.document_type == DOC_TYPE_UNKNOWN
        assert info.year is None

    def test_real_folder_list(self) -> None:
        """실제 data/rag_folders.json 형태 그대로."""
        folders = [
            {"folder_id": "f39fc26147a6", "name": "2020완결보고서"},
            {"folder_id": "aa5f65c0f609", "name": "2025완결보고서"},
            {"folder_id": "5d43cd959fd9", "name": "RFP(2020-2025)"},
            {"folder_id": "4614f46910e1", "name": "RFP(2008-2009)"},
        ]
        idx = build_folder_index(folders)
        assert idx["aa5f65c0f609"].document_type == DOC_TYPE_FINAL_REPORT
        assert idx["aa5f65c0f609"].year == 2025
        assert idx["4614f46910e1"].document_type == DOC_TYPE_RFP
        assert idx["4614f46910e1"].year == 2008

    def test_ignores_entries_without_id(self) -> None:
        assert build_folder_index([{"name": "이름만"}]) == {}


class TestChunkScoring:
    def test_research_goal_scores_high(self) -> None:
        score, hint = score_chunk("3. 연구개발의 목표\n본 과제는 벼 품종 개발을 목표로 한다." * 6)
        assert score > 2.0
        assert "연구개발의 목표" in hint

    def test_references_are_excluded(self) -> None:
        """참고문헌은 선행연구 인용 덩어리다 — 현재 과제 성과로 오인될 위험이 가장 크다."""
        score, _ = score_chunk("참고문헌\n1. Kim et al. (2020) ...\n2. Lee (2019) ...")
        assert score == EXCLUDED

    def test_short_chunk_is_low_but_not_excluded(self) -> None:
        """짧다고 하드 제외하면 과제번호가 적힌 표지가 버려진다 (실제로 그랬다)."""
        score, _ = score_chunk("과제번호 PJ013094\n연구기간 2021~2025")
        assert score > EXCLUDED

    def test_budget_section_excluded(self) -> None:
        score, _ = score_chunk("연구비 집행실적\n인건비 30,000천원, 재료비 12,000천원")
        assert score == EXCLUDED

    def test_short_chunk_penalised(self) -> None:
        short, _ = score_chunk("표 1. 개요")
        long, _ = score_chunk("연구내용: " + "벼 수량예측모형을 개발하였다. " * 40)
        assert long > short

    def test_numeric_result_bonus(self) -> None:
        plain, _ = score_chunk("연구결과 " + "모형을 개선하였다. " * 30)
        numeric, _ = score_chunk("연구결과 " + "수량이 15% 증가하였다. " * 30)
        assert numeric > plain

    def test_empty_text(self) -> None:
        assert score_chunk("")[0] == EXCLUDED


def _rows(n: int, *, page_start: int = 1) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": f"c{i}",
            "doc_id": "D1",
            "page": page_start + i,
            "text": f"일반 본문 {i}. " + "내용이 이어진다. " * 20,
        }
        for i in range(n)
    ]


class TestChunkSelection:
    def test_respects_budget(self) -> None:
        assert len(select_chunks(_rows(40), budget=12)) == 12

    def test_prefers_high_value_sections(self) -> None:
        rows = _rows(20)
        rows[7]["text"] = "연구개발의 목표\n" + "핵심 목표 서술. " * 30
        rows[13]["text"] = "연구개발 결과\n" + "수량 12% 증가. " * 30
        picked = {c.chunk_id for c in select_chunks(rows, budget=5)}
        assert "c7" in picked and "c13" in picked

    def test_always_includes_document_head(self) -> None:
        """과제명·과제번호가 첫 장에 있다. 놓치면 계획서–완결보고서 연결이 끊긴다."""
        rows = _rows(30)
        # 첫 장은 평범한 표지라 점수가 낮지만 반드시 뽑혀야 한다
        rows[0]["text"] = "과제번호 PJ013094\n연구기간 2021~2025"
        rows[0]["page"] = 1
        for i in range(1, 30):
            rows[i]["page"] = 10 + i
            rows[i]["text"] = "연구개발 결과\n" + "성과 서술. " * 30
        picked = [c.chunk_id for c in select_chunks(rows, budget=6)]
        assert "c0" in picked

    def test_excluded_chunks_not_selected_when_alternatives_exist(self) -> None:
        rows = _rows(10)
        rows[3]["text"] = "참고문헌\n" + "Kim et al. (2020). " * 20
        picked = {c.chunk_id for c in select_chunks(rows, budget=5)}
        assert "c3" not in picked

    def test_returns_page_order(self) -> None:
        rows = _rows(10)
        picked = select_chunks(rows, budget=5)
        pages = [c.page for c in picked]
        assert pages == sorted(pages)

    def test_zero_budget_and_empty_rows(self) -> None:
        assert select_chunks(_rows(5), budget=0) == []
        assert select_chunks([], budget=10) == []

    def test_fewer_rows_than_budget(self) -> None:
        assert len(select_chunks(_rows(3), budget=12)) == 3

    def test_section_hint_captured(self) -> None:
        """Section 계층을 못 만드는 대신 소제목 힌트를 남겨 사후 보강 경로를 연다."""
        rows = _rows(5)
        rows[2]["text"] = "제3장 연구수행 내용\n" + "세부 서술. " * 30
        picked = {c.chunk_id: c for c in select_chunks(rows, budget=5)}
        assert "연구수행 내용" in picked["c2"].section_hint

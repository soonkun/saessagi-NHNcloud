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


class TestLowValueVetoDoesNotStarveDocuments:
    """E-92 회귀 — 하드 제외가 문서를 통째로 비우면 안 된다.

    2008~2014년 RFP는 한 쪽짜리라 머리글에 `총 연구비 : 130백만원`이 있고 **그 아래에
    연구목표·연구내용이 이어진다.** 예전 규칙은 앞 200자의 저가치 패턴만 보고 통째로
    버려서, 알맹이가 가득한 문서가 "COMPLETED · 후보 0건"으로 조용히 끝났다.
    실측 RFP(2008-2009) 84% · RFP(2010-2014) 28%가 이렇게 비었다.
    """

    def test_budget_header_does_not_veto_research_content(self) -> None:
        """예산 한 줄이 머리글에 있어도 연구 내용이 있으면 살려야 한다."""
        text = (
            "제안요청서(RFP)\n소과제명 감귤 등 겔 이용 의료용 신소재 개발\n"
            "예산구분 총 연구비 : 130백만원\n"
            "1. 연구목표\n○ 과실 발효물 gel이용 생체공학용 신소재 개발\n"
            "2. 세부과제 연구내용\n○ 신균주 개발 및 효소 선발\n"
        )
        score, _hint = score_chunk(text)
        assert score > EXCLUDED, "연구목표가 있는데 예산 머리글 때문에 제외됐다"
        assert score > 0

    def test_pure_budget_chunk_is_still_excluded(self) -> None:
        """반대로 정말 예산표뿐이면 여전히 버려야 한다 — 규칙을 무력화한 게 아니다."""
        text = "소요예산 내역\n항목 금액\n인건비 100\n재료비 50\n연구비 집행실적 정산\n"
        score, _hint = score_chunk(text)
        assert score == EXCLUDED

    def test_single_chunk_document_is_never_starved(self) -> None:
        """청크가 전부 제외돼도 최소 1개는 보낸다 — 안전망."""
        rows = [
            {
                "chunk_id": "only",
                "doc_id": "D1",
                "page": 1,
                "text": "참고문헌\n" + "Kim et al. (2020). " * 30,
            }
        ]
        picked = select_chunks(rows, budget=8)
        assert len(picked) == 1, "제외 규칙이 문서를 통째로 비웠다"
        assert picked[0].chunk_id == "only"

    def test_safety_net_prefers_longest_chunk(self) -> None:
        """안전망이 고를 때는 본문일 확률이 높은 긴 청크를 택한다."""
        rows = [
            {"chunk_id": "short", "doc_id": "D1", "page": 1, "text": "목차\n1. 개요"},
            {"chunk_id": "long", "doc_id": "D1", "page": 2, "text": "붙임\n" + "가나다라. " * 80},
        ]
        picked = select_chunks(rows, budget=8)
        assert len(picked) == 1
        assert picked[0].chunk_id == "long"

    def test_empty_document_yields_nothing(self) -> None:
        """내용이 없으면 안전망도 발동하지 않는다 — 빈 걸 LLM에 보낼 이유는 없다."""
        rows = [{"chunk_id": "c0", "doc_id": "D1", "page": 1, "text": "   \n  "}]
        assert select_chunks(rows, budget=8) == []

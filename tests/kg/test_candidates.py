# tests/kg/test_candidates.py
"""M_23 후보 저장소 테스트 (스펙 §5.1).

여기서 지키려는 성질은 두 가지다.

1. **재실행 안전성** — 같은 문서를 다시 처리해도 후보가 중복으로 쌓이면 안 된다.
   수십 시간짜리 배치라 중단·재개가 일상이고, 중복이 쌓이면 그대로 그래프 노드 폭증으로
   이어진다(지침서 4.6 / 28장 완료조건).
2. **부분 재처리** — 한 청크만 다시 돌렸을 때 같은 문서의 다른 청크 결과가 날아가면 안 된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kg.candidates import (
    CandidateStore,
    CanonicalEntity,
    DocEntity,
    DocumentMeta,
    EntityCandidate,
    RelationCandidate,
)


@pytest.fixture
def store(tmp_path: Path) -> CandidateStore:
    return CandidateStore(tmp_path / "kg.db")


def _cand(cid: str, doc: str = "D1", chunk: str = "C1", **kw: object) -> EntityCandidate:
    base = dict(
        candidate_id=cid,
        doc_id=doc,
        chunk_id=chunk,
        entity_type="TECHNOLOGY",
        surface_form="SWAT+ 모형",
        evidence="본 연구에서는 SWAT+ 모형을 개선하였다.",
        confidence=0.9,
    )
    base.update(kw)
    return EntityCandidate(**base)  # type: ignore[arg-type]


class TestDocuments:
    def test_upsert_is_idempotent(self, store: CandidateStore) -> None:
        meta = DocumentMeta(doc_id="D1", doc_name="완결보고서.pdf", document_type="FINAL_REPORT")
        store.upsert_document(meta)
        store.upsert_document(meta)
        assert store.stats()["documents"] == 1

    def test_upsert_keeps_previously_extracted_project_no(self, store: CandidateStore) -> None:
        """폴더에서 유도한 메타를 다시 넣을 때 이미 추출한 과제번호를 지우면 안 된다."""
        store.upsert_document(DocumentMeta(doc_id="D1", project_no="PJ013094", title="벼 육종"))
        # 폴더 스캔이 다시 돌면서 과제번호를 모르는 채로 덮어쓰는 상황
        store.upsert_document(DocumentMeta(doc_id="D1", document_type="FINAL_REPORT"))
        got = store.get_document("D1")
        assert got is not None
        assert got.project_no == "PJ013094"
        assert got.title == "벼 육종"
        assert got.document_type == "FINAL_REPORT"

    def test_state_transitions_and_query(self, store: CandidateStore) -> None:
        store.upsert_document(DocumentMeta(doc_id="D1"))
        store.upsert_document(DocumentMeta(doc_id="D2"))
        store.set_document_state("D1", "COMPLETED")
        store.set_document_state("D2", "FAILED", "타임아웃")
        assert [d.doc_id for d in store.documents_by_state("COMPLETED")] == ["D1"]
        failed = store.documents_by_state("FAILED")
        assert failed[0].error_message == "타임아웃"


class TestEntityCandidates:
    def test_rerun_same_chunk_does_not_duplicate(self, store: CandidateStore) -> None:
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1"), _cand("c2")])
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1"), _cand("c2")])
        assert len(store.candidates_for_document("D1")) == 2

    def test_rerun_one_chunk_keeps_other_chunks(self, store: CandidateStore) -> None:
        """부분 재처리 — C1만 다시 돌려도 C2 결과는 살아 있어야 한다."""
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1", chunk="C1")])
        store.replace_candidates_for_chunk("D1", "C2", [_cand("c2", chunk="C2")])
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1b", chunk="C1")])
        got = {c.candidate_id for c in store.candidates_for_document("D1")}
        assert got == {"c1b", "c2"}

    def test_replacing_with_empty_clears_chunk(self, store: CandidateStore) -> None:
        """재추출 결과가 0건이면 예전 후보가 남아 있으면 안 된다 (유령 노드 방지)."""
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1")])
        store.replace_candidates_for_chunk("D1", "C1", [])
        assert store.candidates_for_document("D1") == []

    def test_filter_by_state(self, store: CandidateStore) -> None:
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1"), _cand("c2", state="REJECTED")])
        assert len(store.candidates_for_document("D1", states=["PENDING"])) == 1
        assert len(store.candidates_for_document("D1", states=["REJECTED"])) == 1

    def test_set_states(self, store: CandidateStore) -> None:
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1"), _cand("c2")])
        store.set_candidate_states(["c1"], "REJECTED", "근거 불일치")
        rejected = store.candidates_for_document("D1", states=["REJECTED"])
        assert rejected[0].reject_reason == "근거 불일치"

    def test_counts_by_state(self, store: CandidateStore) -> None:
        store.replace_candidates_for_chunk(
            "D1", "C1", [_cand("c1"), _cand("c2"), _cand("c3", state="REJECTED")]
        )
        assert store.count_candidates("D1") == {"PENDING": 2, "REJECTED": 1}


class TestDocEntities:
    def test_links_back_to_source_candidates(self, store: CandidateStore) -> None:
        """통합 결과는 어떤 후보에서 왔는지 되짚을 수 있어야 한다 — 근거 역추적의 뿌리."""
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1"), _cand("c2")])
        store.replace_doc_entities(
            "D1",
            [
                DocEntity(
                    doc_entity_id="de1",
                    doc_id="D1",
                    entity_type="TECHNOLOGY",
                    canonical_name_candidate="SWAT+",
                    aliases=["SWAT+ 모형", "SWAT Plus"],
                    source_candidate_ids=["c1", "c2"],
                    mention_count=2,
                )
            ],
        )
        cands = store.candidates_for_document("D1")
        assert all(c.doc_entity_id == "de1" for c in cands)
        de = store.doc_entities("D1")[0]
        assert de.aliases == ["SWAT+ 모형", "SWAT Plus"]

    def test_rerun_replaces_not_appends(self, store: CandidateStore) -> None:
        for _ in range(2):
            store.replace_doc_entities(
                "D1",
                [
                    DocEntity(
                        doc_entity_id="de1",
                        doc_id="D1",
                        entity_type="TECHNOLOGY",
                        canonical_name_candidate="SWAT+",
                    )
                ],
            )
        assert len(store.doc_entities("D1")) == 1

    def test_link_to_canonical_propagates(self, store: CandidateStore) -> None:
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1")])
        store.replace_doc_entities(
            "D1",
            [
                DocEntity(
                    doc_entity_id="de1",
                    doc_id="D1",
                    entity_type="TECHNOLOGY",
                    canonical_name_candidate="SWAT+",
                    source_candidate_ids=["c1"],
                )
            ],
        )
        store.link_doc_entity("de1", "technology:swat+", "MATCHED")
        assert store.doc_entities("D1")[0].canonical_id == "technology:swat+"
        assert store.candidates_for_document("D1")[0].canonical_id == "technology:swat+"


class TestCanonicalEntities:
    def test_lookup_is_type_scoped(self, store: CandidateStore) -> None:
        """유형이 다르면 같은 이름이어도 다른 노드다 (기술 ≠ 산출물). 스펙 §6."""
        store.upsert_canonical(
            CanonicalEntity(
                canonical_id="technology:유전체 선발",
                entity_type="TECHNOLOGY",
                canonical_name="유전체 선발 기술",
                normalized_name="유전체 선발",
            )
        )
        store.upsert_canonical(
            CanonicalEntity(
                canonical_id="output:유전체 선발",
                entity_type="OUTPUT",
                canonical_name="유전체 선발모형",
                normalized_name="유전체 선발",
            )
        )
        assert store.find_canonical("TECHNOLOGY", "유전체 선발") is not None
        assert store.find_canonical("OUTPUT", "유전체 선발") is not None
        assert store.find_canonical("METHOD", "유전체 선발") is None
        assert len(store.all_canonicals()) == 2

    def test_upsert_updates_aliases(self, store: CandidateStore) -> None:
        e = CanonicalEntity(
            canonical_id="technology:swat+",
            entity_type="TECHNOLOGY",
            canonical_name="SWAT+",
            normalized_name="swat+",
            aliases=["SWAT Plus"],
        )
        store.upsert_canonical(e)
        e.aliases = ["SWAT Plus", "SWAT+ 모형"]
        store.upsert_canonical(e)
        got = store.find_canonical("TECHNOLOGY", "swat+")
        assert got is not None and got.aliases == ["SWAT Plus", "SWAT+ 모형"]
        assert len(store.canonicals_by_type("TECHNOLOGY")) == 1


class TestRelations:
    def test_rerun_replaces(self, store: CandidateStore) -> None:
        rel = RelationCandidate(
            relation_candidate_id="r1",
            doc_id="D1",
            chunk_id="C1",
            source_canonical_id="project:PJ1",
            relation_type="USES_TECHNOLOGY",
            target_canonical_id="technology:swat+",
            statement_status="ACTUAL",
            evidence="SWAT+ 모형을 적용하였다.",
            confidence=0.9,
        )
        store.replace_relations_for_chunk("D1", "C1", [rel])
        store.replace_relations_for_chunk("D1", "C1", [rel])
        assert len(store.relations_for_document("D1")) == 1


class TestJobsAndReset:
    def test_job_lifecycle(self, store: CandidateStore) -> None:
        store.start_job("j1", "extract", scope="doc:D1")
        store.finish_job("j1", "COMPLETED", {"docs": 1, "candidates": 12})
        job = store.jobs()[0]
        assert job["state"] == "COMPLETED"
        assert job["counts"]["candidates"] == 12

    def test_reset_clears_everything(self, store: CandidateStore) -> None:
        store.upsert_document(DocumentMeta(doc_id="D1"))
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1")])
        store.reset()
        s = store.stats()
        assert s["documents"] == 0 and s["entity_candidates"] == 0

    def test_reset_keep_documents_resets_state_only(self, store: CandidateStore) -> None:
        """문서 메타(폴더·과제번호)는 비싸게 얻은 것이라 재추출만 다시 하고 싶을 때가 있다."""
        store.upsert_document(DocumentMeta(doc_id="D1", project_no="PJ1"))
        store.set_document_state("D1", "COMPLETED")
        store.replace_candidates_for_chunk("D1", "C1", [_cand("c1")])
        store.reset(keep_documents=True)
        doc = store.get_document("D1")
        assert doc is not None
        assert doc.project_no == "PJ1"
        assert doc.extract_state == "PENDING"
        assert store.stats()["entity_candidates"] == 0

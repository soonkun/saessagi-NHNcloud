# tests/kg/test_derive.py
"""M_23 8단계 테스트 — 관계 유도와 연결성 (스펙 §3 (8), §4.1-B·C).

이 단계는 LLM 없이 관계를 만든다. 그래서 검증할 것이 두 종류다.

1. **사상이 정확한가** — `entity_type` → 관계 유형이 스펙 §5.2와 일치하는가.
2. **연결성이 실제로 생기는가** — `target_key` 승격과 `SHARES_ENTITY`가 문서를 잇는가.
   이게 없으면 CR-34의 "별들의 숲"이 재현된다. 그 실패를 테스트로 못 박아 둔다.

그리고 절대 어겨서는 안 되는 것 하나: **선행연구·인용문을 현재 과제 성과로 귀속시키지
않는다.** 현재 데이터에는 해당 후보가 0건이라 무동작이지만, 재추출하면 살아나는 방어선이라
테스트로 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kg.candidates import CandidateStore, DocumentMeta, EntityCandidate
from kg.config import ENTITY_TYPE_TO_RELATION, KnowledgeGraphConfig
from kg.derive import (
    RELATION_APPLIED_TO,
    RELATION_SHARES_ENTITY,
    SOURCE_KIND_DOCUMENT,
    SOURCE_KIND_PROJECT,
    STATUS_DERIVED,
    STATUS_EXTRACTED,
    derive_all,
    resolve_status,
)
from kg.normalize import consolidate_documents, normalize_global


@pytest.fixture
def store(tmp_path: Path) -> CandidateStore:
    return CandidateStore(tmp_path / "kg.db")


@pytest.fixture
def config() -> KnowledgeGraphConfig:
    return KnowledgeGraphConfig()


def _doc(
    store: CandidateStore, doc_id: str, doc_type: str = "FINAL_REPORT", project_no: str = ""
) -> None:
    store.upsert_document(
        DocumentMeta(
            doc_id=doc_id,
            doc_name=f"{doc_id}.pdf",
            document_type=doc_type,
            year=2024,
            title=f"{doc_id} 과제",
            project_no=project_no,
            extract_state="COMPLETED",
        )
    )


def _cand(
    cid: str,
    doc: str,
    name: str,
    etype: str = "TECHNOLOGY",
    target_key: str = "",
    status: str = "UNCERTAIN",
    chunk: str = "C1",
) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=cid,
        doc_id=doc,
        chunk_id=chunk,
        entity_type=etype,
        surface_form=name,
        canonical_name_candidate=name,
        target_key=target_key,
        statement_status=status,
        evidence=f"본문에 {name}",
        confidence=0.9,
        state="PENDING",
    )


def _build(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    consolidate_documents(store, config)
    normalize_global(store, config)


def _relations(store: CandidateStore, rel_type: str) -> list[dict[str, object]]:
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT * FROM relation_candidates WHERE relation_type=?", (rel_type,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── 유형 → 관계 사상 ──────────────────────────────────────────────────────────


def test_entity_type_maps_to_spec_relations() -> None:
    """사상 표가 스펙 §5.2의 Project 관계 7종과 정확히 맞아야 한다."""
    assert set(ENTITY_TYPE_TO_RELATION.values()) == {
        "HAS_PROBLEM",
        "HAS_OBJECTIVE",
        "TARGETS",
        "USES_TECHNOLOGY",
        "USES_METHOD",
        "USES_DATASET",
        "PRODUCES",
    }


def test_derives_project_relation_per_entity_type(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    _doc(store, "D1")
    cands = [
        _cand(f"e{i}", "D1", f"항목{i}", etype=etype, chunk=f"C{i}")
        for i, etype in enumerate(ENTITY_TYPE_TO_RELATION)
    ]
    for c in cands:
        store.replace_candidates_for_chunk("D1", c.chunk_id, [c])
    _build(store, config)
    derive_all(store, config)

    got = {
        (r["relation_type"])
        for r in store._conn.execute(  # noqa: SLF001
            "SELECT relation_type FROM relation_candidates WHERE source_kind=?",
            (SOURCE_KIND_PROJECT,),
        ).fetchall()
    }
    assert got == set(ENTITY_TYPE_TO_RELATION.values())


# ── derived_status ────────────────────────────────────────────────────────────


def test_resolve_status_prefers_extracted() -> None:
    """모델이 실제로 채운 값이 문서유형 유도보다 우선한다."""
    assert resolve_status(["PLANNED"], "FINAL_REPORT") == ("PLANNED", STATUS_EXTRACTED)


def test_resolve_status_derives_from_doc_type() -> None:
    """비어 있으면 문서유형의 사전확률로 채우고 출처를 남긴다."""
    assert resolve_status(["UNCERTAIN"], "FINAL_REPORT") == ("ACTUAL", STATUS_DERIVED)
    assert resolve_status([], "RFP") == ("REQUIREMENT", STATUS_DERIVED)
    assert resolve_status([], "UNKNOWN")[0] == "UNCERTAIN"


def test_derived_status_marks_its_source(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """유도값임이 데이터에 드러나야 나중에 재추출값으로 갈아끼울 수 있다."""
    _doc(store, "R1", doc_type="RFP")
    store.replace_candidates_for_chunk("R1", "C1", [_cand("e1", "R1", "무인 방제 기술")])
    _build(store, config)
    derive_all(store, config)

    rel = _relations(store, "USES_TECHNOLOGY")[0]
    assert rel["statement_status"] == "REQUIREMENT"
    assert rel["status_source"] == STATUS_DERIVED


def test_original_statement_status_is_preserved(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """원본 후보의 statement_status를 덮어쓰지 않는다 (스펙 §5.3)."""
    _doc(store, "D1")
    store.replace_candidates_for_chunk("D1", "C1", [_cand("e1", "D1", "센서 기술")])
    _build(store, config)
    derive_all(store, config)
    assert store.candidates_for_document("D1")[0].statement_status == "UNCERTAIN"


def test_prior_research_is_not_attributed(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """선행연구를 현재 과제 성과로 붙이지 않는다 — 이 시스템 최악의 오류."""
    _doc(store, "D1")
    store.replace_candidates_for_chunk(
        "D1", "C1", [_cand("e1", "D1", "선행 성과", etype="OUTPUT", status="PRIOR_RESEARCH")]
    )
    _build(store, config)
    stats = derive_all(store, config)
    assert stats.non_attributable_skipped == 1
    assert _relations(store, "PRODUCES") == []


# ── 연결성 ────────────────────────────────────────────────────────────────────


def test_target_key_becomes_shared_hub(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """**CR-34 회귀 테스트.** 서로 다른 문서의 고유 엔티티가 target_key 허브로 이어진다.

    두 문서는 이름이 겹치는 엔티티가 하나도 없다. 그래도 둘 다 '벼'를 대상으로 하므로
    APPLIED_TO를 통해 연결되어야 한다. 이게 안 되면 그래프는 별들의 숲이다.
    """
    _doc(store, "D1")
    _doc(store, "D2")
    store.replace_candidates_for_chunk(
        "D1", "C1", [_cand("e1", "D1", "벼 도열병 저항성 계통 선발 기술", target_key="벼")]
    )
    store.replace_candidates_for_chunk(
        "D2", "C1", [_cand("e2", "D2", "벼 재배 물관리 자동화 모형", target_key="벼")]
    )
    _build(store, config)
    stats = derive_all(store, config)

    assert stats.target_key_entities >= 1
    applied = _relations(store, RELATION_APPLIED_TO)
    assert len(applied) == 2, "두 문서의 엔티티가 모두 대상 허브에 걸려야 한다"
    # 둘이 같은 허브를 가리켜야 실제로 이어진다
    assert len({r["target_canonical_id"] for r in applied}) == 1


def test_document_frequency_is_computed(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    for d in ("D1", "D2", "D3"):
        _doc(store, d)
        store.replace_candidates_for_chunk(d, "C1", [_cand(f"e-{d}", d, "정밀 방제 기술")])
    _build(store, config)
    derive_all(store, config)

    row = store._conn.execute(  # noqa: SLF001
        "SELECT document_frequency FROM canonical_entities WHERE entity_type='TECHNOLOGY'"
    ).fetchone()
    assert row["document_frequency"] == 3


def test_boilerplate_is_flagged_not_deleted(store: CandidateStore) -> None:
    """상용구는 표시만 한다 — 지우지 않는다 (코퍼스가 늘면 판정이 달라져야 한다)."""
    config = KnowledgeGraphConfig()
    config.graph.boilerplate_document_frequency = 3
    for i in range(4):
        d = f"D{i}"
        _doc(store, d)
        store.replace_candidates_for_chunk(
            d, "C1", [_cand(f"e{i}", d, "산업재산권 출원", etype="OUTPUT")]
        )
    _build(store, config)
    derive_all(store, config)

    row = store._conn.execute(  # noqa: SLF001
        "SELECT canonical_name, is_boilerplate FROM canonical_entities WHERE entity_type='OUTPUT'"
    ).fetchone()
    assert row is not None, "상용구가 삭제됐다 — 표시만 해야 한다"
    assert row["is_boilerplate"] == 1


def test_shares_entity_skips_high_fanout(store: CandidateStore) -> None:
    """상용구 허브는 문서-문서 유사도를 만들지 않는다 (팬아웃 상한)."""
    config = KnowledgeGraphConfig()
    config.graph.shares_entity_max_fanout = 3
    config.graph.shares_entity_min_weight = 0.1
    # 5개 문서가 같은 엔티티를 공유 → 팬아웃 5 > 3 이므로 엣지 생성 안 됨
    for i in range(5):
        d = f"D{i}"
        _doc(store, d)
        store.replace_candidates_for_chunk(d, "C1", [_cand(f"e{i}", d, "학술발표", etype="OUTPUT")])
    _build(store, config)
    derive_all(store, config)
    assert _relations(store, RELATION_SHARES_ENTITY) == []


def test_shares_entity_links_rare_overlap(store: CandidateStore) -> None:
    """희소한 엔티티를 공유하는 두 문서는 이어진다 — 중복성 분석의 본체."""
    config = KnowledgeGraphConfig()
    config.graph.shares_entity_min_weight = 0.1
    for d in ("D1", "D2"):
        _doc(store, d)
        store.replace_candidates_for_chunk(
            d, "C1", [_cand(f"e-{d}", d, "감자 더뎅이병 저항성 검정법", etype="METHOD")]
        )
    _build(store, config)
    derive_all(store, config)

    shares = _relations(store, RELATION_SHARES_ENTITY)
    assert len(shares) == 1
    assert shares[0]["source_kind"] == SOURCE_KIND_DOCUMENT
    assert {shares[0]["source_canonical_id"], shares[0]["target_canonical_id"]} == {"D1", "D2"}


def test_derive_is_rerunnable(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    _doc(store, "D1")
    store.replace_candidates_for_chunk(
        "D1", "C1", [_cand("e1", "D1", "관수 자동화", target_key="딸기")]
    )
    _build(store, config)
    derive_all(store, config)
    first = store.stats()["relation_candidates"]
    derive_all(store, config)
    assert store.stats()["relation_candidates"] == first

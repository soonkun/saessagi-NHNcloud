# tests/kg/test_normalize.py
"""M_23 6·7단계 테스트 — 문서 단위 통합과 전역 정규화 (스펙 §3 (6)(7), §6).

여기서 지키려는 성질은 하나로 요약된다: **잘못 붙지 않는 것.**

이 프로젝트는 병합이 과하게 붙어서 두 번 실패했다.
- CR-25: 범용 엔티티가 문서 126건에 2,212개로 폭증
- CR-36: union-find 연쇄가 3만 용어를 208개 blob으로 붕괴 (AI·3D프린팅·CRISPR가 한 노드)

그래서 테스트의 무게중심이 "제대로 합쳐지는가"가 아니라 **"안 합쳐져야 할 것이 안
합쳐지는가"** 에 있다. 특히 `test_no_transitive_chaining`은 CR-36의 회귀 테스트다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kg.candidates import CandidateStore, DocumentMeta, EntityCandidate
from kg.config import KnowledgeGraphConfig
from kg.normalize import consolidate_documents, normalize_global, review_queue


@pytest.fixture
def store(tmp_path: Path) -> CandidateStore:
    return CandidateStore(tmp_path / "kg.db")


@pytest.fixture
def config() -> KnowledgeGraphConfig:
    return KnowledgeGraphConfig()


def _doc(store: CandidateStore, doc_id: str, doc_type: str = "FINAL_REPORT") -> None:
    store.upsert_document(
        DocumentMeta(
            doc_id=doc_id,
            doc_name=f"{doc_id}.pdf",
            document_type=doc_type,
            year=2024,
            title=f"{doc_id} 과제",
            extract_state="COMPLETED",
        )
    )


def _cand(
    cid: str,
    doc: str,
    name: str,
    etype: str = "TECHNOLOGY",
    target_key: str = "",
    chunk: str = "C1",
    status: str = "UNCERTAIN",
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
        evidence=f"본문에 {name} 가 있다.",
        confidence=0.95,
        state="PENDING",
    )


def _put(store: CandidateStore, doc: str, cands: list[EntityCandidate]) -> None:
    by_chunk: dict[str, list[EntityCandidate]] = {}
    for c in cands:
        by_chunk.setdefault(c.chunk_id, []).append(c)
    for chunk, items in by_chunk.items():
        store.replace_candidates_for_chunk(doc, chunk, items)


# ── 6단계 ─────────────────────────────────────────────────────────────────────


def test_consolidate_groups_notation_variants(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """같은 문서 안의 표기 변형은 하나로 묶인다 (지침서 사례 3)."""
    _doc(store, "D1")
    _put(
        store,
        "D1",
        [
            _cand("e1", "D1", "SWAT+"),
            _cand("e2", "D1", "SWAT Plus"),
            _cand("e3", "D1", "SWAT+ 모형"),
        ],
    )
    consolidate_documents(store, config)
    ents = store.doc_entities("D1")
    assert len(ents) == 1, f"표기 변형이 갈렸다: {[e.canonical_name_candidate for e in ents]}"
    assert ents[0].mention_count == 3


def test_consolidate_keeps_different_targets_apart(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """대상이 다르면 이름이 비슷해도 갈라진다 (스펙 §6 R2)."""
    _doc(store, "D1")
    _put(
        store,
        "D1",
        [
            _cand("e1", "D1", "사과 육종시스템", target_key="사과"),
            _cand("e2", "D1", "복숭아 육종시스템", target_key="복숭아"),
        ],
    )
    consolidate_documents(store, config)
    assert len(store.doc_entities("D1")) == 2


def test_consolidate_skips_rejected(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """검증에서 떨어진 후보는 그래프로 올라가지 않는다 (스펙 §2 원칙 2)."""
    _doc(store, "D1")
    good = _cand("e1", "D1", "유전체 선발 기술")
    bad = _cand("e2", "D1", "환각 엔티티")
    bad.state = "REJECTED"
    bad.reject_reason = "EVIDENCE_NOT_IN_SOURCE"
    _put(store, "D1", [good, bad])
    consolidate_documents(store, config)
    ents = store.doc_entities("D1")
    assert len(ents) == 1
    assert ents[0].canonical_name_candidate == "유전체 선발 기술"


def test_consolidate_is_rerunnable(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """두 번 돌려도 행이 늘지 않는다 (스펙 §2 원칙 6)."""
    _doc(store, "D1")
    _put(store, "D1", [_cand("e1", "D1", "정밀농업 기술"), _cand("e2", "D1", "관수 제어")])
    consolidate_documents(store, config)
    first = {e.doc_entity_id for e in store.doc_entities("D1")}
    consolidate_documents(store, config)
    second = {e.doc_entity_id for e in store.doc_entities("D1")}
    assert first == second


# ── 7단계 ─────────────────────────────────────────────────────────────────────


def test_normalize_merges_across_documents(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """문서가 달라도 같은 이름이면 하나의 정규 엔티티가 된다 — M_19가 못 하던 것."""
    for d in ("D1", "D2", "D3"):
        _doc(store, d)
        _put(store, d, [_cand(f"e-{d}", d, "유전체 선발 기술")])
    consolidate_documents(store, config)
    normalize_global(store, config)

    canon = store.all_canonicals()
    tech = [c for c in canon if c.entity_type == "TECHNOLOGY"]
    assert len(tech) == 1, f"문서를 넘는 병합 실패: {[c.canonical_name for c in tech]}"
    assert all(e.canonical_id == tech[0].canonical_id for e in store.doc_entities())


def test_no_transitive_chaining(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """**CR-36 회귀 테스트.** A~B, B~C 라도 A와 C가 다르면 하나로 붕괴하지 않는다.

    union-find를 쓰면 세 개가 한 덩어리가 된다. 대표자 비교 방식은 그렇게 되지 않는다.
    """
    names = ["병해충 영상진단 기술", "병해충 영상진단 서비스", "병해충 영상진단 모형"]
    for i, name in enumerate(names):
        d = f"D{i}"
        _doc(store, d)
        _put(store, d, [_cand(f"e{i}", d, name)])
    consolidate_documents(store, config)
    normalize_global(store, config)

    canon = store.all_canonicals()
    # 기술 / 서비스 / 모형은 접미어 성격이 달라 서로 다른 것이다 (merge.py R4).
    assert len(canon) == 3, f"연쇄 병합으로 붕괴했다: {[c.canonical_name for c in canon]}"


def test_target_key_blocks_global_merge(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """이름이 완전히 같아도 대상이 다르면 별도 노드다 (스펙 §6)."""
    _doc(store, "D1")
    _doc(store, "D2")
    _put(store, "D1", [_cand("e1", "D1", "병해 진단", target_key="배추")])
    _put(store, "D2", [_cand("e2", "D2", "병해 진단", target_key="무")])
    consolidate_documents(store, config)
    normalize_global(store, config)

    canon = store.all_canonicals()
    assert len(canon) == 2, (
        f"작물이 다른데 병합됐다: {[(c.canonical_name, c.target_key) for c in canon]}"
    )
    assert {c.target_key for c in canon} == {"배추", "무"}


def test_cross_type_never_merges(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """유형이 다르면 이름이 같아도 병합 금지 (스펙 §6 R1)."""
    _doc(store, "D1")
    _put(
        store,
        "D1",
        [
            _cand("e1", "D1", "품종 판별", etype="TECHNOLOGY"),
            _cand("e2", "D1", "품종 판별", etype="OUTPUT", chunk="C2"),
        ],
    )
    consolidate_documents(store, config)
    normalize_global(store, config)
    canon = store.all_canonicals()
    assert {c.entity_type for c in canon} == {"TECHNOLOGY", "OUTPUT"}


def test_blob_cap_sends_to_review(store: CandidateStore) -> None:
    """한 엔티티가 표기를 너무 많이 흡수하면 멈추고 검토 큐로 간다 (CR-36 방어)."""
    config = KnowledgeGraphConfig()
    config.normalization.max_members_per_canonical = 3

    # 같은 알맹이에 무시 가능한 수식어만 다른 이름을 여러 개 만든다 → 전부 SAME 판정.
    variants = ["관수 제어", "관수 제어 개발", "관수 제어 구축", "관수 제어 활용", "관수 제어 기반"]
    for i, name in enumerate(variants):
        d = f"D{i}"
        _doc(store, d)
        _put(store, d, [_cand(f"e{i}", d, name)])
    consolidate_documents(store, config)
    stats = normalize_global(store, config)

    assert stats.blob_capped > 0, "상한을 넘겼는데 블롭 감시가 걸리지 않았다"
    assert review_queue(store), "블롭이 검토 큐에 올라가지 않았다"


def test_normalize_is_rerunnable(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """재실행해도 canonical_id가 같아야 Neo4j에 고아 노드가 안 생긴다."""
    for d in ("D1", "D2"):
        _doc(store, d)
        _put(store, d, [_cand(f"e-{d}", d, "노지 스마트팜 기술", target_key="배추")])
    consolidate_documents(store, config)
    normalize_global(store, config)
    first = sorted(c.canonical_id for c in store.all_canonicals())

    store.clear_derived()
    consolidate_documents(store, config)
    normalize_global(store, config)
    second = sorted(c.canonical_id for c in store.all_canonicals())
    assert first == second


def test_clear_derived_preserves_candidates(
    store: CandidateStore, config: KnowledgeGraphConfig
) -> None:
    """파생물 초기화가 26시간짜리 추출 결과를 지우면 안 된다."""
    _doc(store, "D1")
    _put(store, "D1", [_cand("e1", "D1", "정밀 방제 기술")])
    consolidate_documents(store, config)
    normalize_global(store, config)

    store.clear_derived()
    assert store.stats()["entity_candidates"] == 1
    assert store.stats()["doc_entities"] == 0
    assert store.stats()["canonical_entities"] == 0
    # 후보 상태도 PENDING으로 되돌아야 다시 돌릴 수 있다
    assert store.candidates_for_document("D1")[0].state == "PENDING"


# ── 진행률 보고 (E-97) ────────────────────────────────────────────────────────


def test_progress_reports_final_tick(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """총건수가 보고 주기의 배수가 아니어도 마지막 눈금을 찍는다 (E-97).

    실제 사고: 363,235건인데 2만 배수에서만 보고해 화면이 `360,000/363,235`에
    영원히 머물렀다. 사용자가 "왜 멈춰 있냐"고 물었다.
    """
    _doc(store, "D1")
    # 7건 — 어떤 보고 주기의 배수도 아니다
    _put(store, "D1", [_cand(f"c{i}", "D1", f"기술{i}") for i in range(7)])
    consolidate_documents(store, config)

    seen: list[tuple[str, int, int]] = []
    normalize_global(store, config, progress=lambda s, d, t: seen.append((s, d, t)))

    assert seen, "진행률 보고가 하나도 없다"
    # 마지막 보고는 반드시 완료(done == total)여야 한다 — 그래야 화면이 안 얼어붙는다
    last = seen[-1]
    assert last[1] == last[2], f"마지막 눈금이 완료가 아니다: {last}"


def test_fuzzy_pass_reports_progress(store: CandidateStore, config: KnowledgeGraphConfig) -> None:
    """가장 오래 걸리는 퍼지 구간도 진행률을 낸다 (E-97).

    여기가 조용하면 8분간 죽은 것처럼 보인다 — 실제로 그렇게 보였다.
    """
    config.normalization.fuzzy_enabled = True
    _doc(store, "D1")
    _put(store, "D1", [_cand(f"c{i}", "D1", f"서로다른기술{i}") for i in range(5)])
    consolidate_documents(store, config)

    stages: list[str] = []
    normalize_global(store, config, progress=lambda s, d, t: stages.append(s))

    assert "normalize:fuzzy" in stages, f"퍼지 진행률이 없다: {set(stages)}"

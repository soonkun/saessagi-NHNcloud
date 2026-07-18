# tests/graph_rag/test_neo4j_store.py
"""M_19 Neo4jGraphStore 통합 테스트 — 로컬 Neo4j(bolt://127.0.0.1:7687) 필요.

접속 불가 시 전체 skip (스펙 §6 테스트 정책). 비밀번호는 SAESSAGI_NEO4J_PASSWORD
환경변수 (기본 'saessagi-graph' — install.md 기본 설정).
"""

from __future__ import annotations

import os
import uuid

import pytest

from graph_rag.neo4j_store import Neo4jGraphStore
from graph_rag.types import ChunkLink, Entity, Relation

_PASSWORD = os.environ.get("SAESSAGI_NEO4J_PASSWORD", "saessagi-graph")


@pytest.fixture(scope="module")
def store() -> Neo4jGraphStore:
    s = Neo4jGraphStore(password=_PASSWORD)
    if not s.ping():
        pytest.skip("로컬 Neo4j 미접속 — 통합 테스트 skip")
    s.ensure_schema()
    return s


@pytest.fixture()
def doc_id(store: Neo4jGraphStore) -> str:
    """테스트별 고유 문서 — 종료 시 연쇄 삭제로 정리."""
    did = f"__test__{uuid.uuid4().hex[:8]}"
    yield did
    store.delete_by_doc_id(did)


def _seed(store: Neo4jGraphStore, doc_id: str, suffix: str) -> tuple[str, str]:
    e1 = Entity(id=f"테스트기관{suffix}:조직", name=f"테스트기관{suffix}", type="조직")
    e2 = Entity(id=f"테스트사업{suffix}:사업", name=f"테스트사업{suffix}", type="사업")
    store.upsert_document(doc_id, "테스트.pdf", "테스트폴더")
    store.upsert_entities([e1, e2])
    store.upsert_relations([Relation(source_id=e1.id, target_id=e2.id, type="주관")])
    store.link_chunks(
        [ChunkLink(e1.id, f"chunk-{suffix}-1"), ChunkLink(e2.id, f"chunk-{suffix}-1")],
        parent_id=doc_id,
        parent_kind="document",
    )
    return e1.id, e2.id


def test_roundtrip_and_neighbors(store: Neo4jGraphStore, doc_id: str) -> None:
    """정상: upsert → find → neighbors → chunks_for_entities 왕복."""
    sfx = uuid.uuid4().hex[:6]
    e1, e2 = _seed(store, doc_id, sfx)

    found = store.find_entities([f"테스트기관{sfx}"])
    assert any(e.id == e1 for e in found)

    nbrs = store.neighbors([e1], hops=1)
    assert any(e.id == e2 for e in nbrs)

    chunks = store.chunks_for_entities([e1, e2])
    assert chunks and chunks[0][1] == 2  # 두 엔티티가 같은 청크에


def test_relation_weight_accumulates(store: Neo4jGraphStore, doc_id: str) -> None:
    """정상: 같은 관계 재-upsert 시 weight 누적."""
    sfx = uuid.uuid4().hex[:6]
    e1, e2 = _seed(store, doc_id, sfx)
    store.upsert_relations([Relation(source_id=e1, target_id=e2, type="주관")])
    snap = store.subgraph([e1, e2], [])
    rel_edges = [e for e in snap.edges if e.kind == "rel"]
    assert rel_edges and rel_edges[0].weight >= 2.0


def test_cypher_special_chars_safe(store: Neo4jGraphStore, doc_id: str) -> None:
    """적대: 이름에 Cypher 특수문자 — 파라미터라이즈로 안전."""
    evil_name = "해킹' } ]) DETACH DELETE (n) //"
    evil = Entity(id=f"{evil_name.casefold()}:기타", name=evil_name, type="기타")
    store.upsert_document(doc_id, "x.pdf")
    store.upsert_entities([evil])
    store.link_chunks([ChunkLink(evil.id, f"chunk-evil-{doc_id}")], doc_id, "document")
    found = store.find_entities(["해킹"])
    assert any(e.name == evil_name for e in found)


def test_delete_cascades_and_orphan_cleanup(store: Neo4jGraphStore, doc_id: str) -> None:
    """정상: 문서 삭제 → 청크·고아 엔티티 정리."""
    sfx = uuid.uuid4().hex[:6]
    e1, _ = _seed(store, doc_id, sfx)
    store.delete_by_doc_id(doc_id)
    # 공유 DB(실사용 그래프)와 함께 돌 수 있으므로 이 테스트가 심은 엔티티만 검사
    remaining = [e for e in store.find_entities([f"테스트기관{sfx}"]) if sfx in e.name]
    assert not remaining
    assert store.chunks_for_entities([e1]) == []

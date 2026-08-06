# tests/kg/test_graph_store.py
"""M_23 Neo4j 스토어 테스트 — **삭제 안전성이 주제다** (CR-61).

이 파일이 지키려는 것은 단 하나: **엔티티가 실수로 날아가지 않는 것.**

그래프 탭에는 "그래프 초기화" 버튼이 있고, M_19의 `clear_all`은 `Document`·`Chunk`를
지운다. 그 둘은 M_23도 쓴다 — 그대로 두면 버튼 한 번에 Mention 216,509개가 고아가 된다
(노드는 남고 연결만 끊겨서 "지워지지도 않았는데 아무것도 안 보이는" 최악의 상태).

Neo4j 없이 돌려야 하므로 `_run`을 가로채 실행된 Cypher를 기록하고, **무엇을 지우려
했는지**를 검사한다. 실제 삭제를 확인하는 게 아니라 삭제 대상 라벨이 옳은지 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from kg.graph_store import KgGraphStore, KgGraphStoreError


class RecordingStore(KgGraphStore):
    """`_run`을 가로채는 스토어. 응답은 시나리오별로 주입한다."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None) -> None:
        super().__init__(password="x")
        self.queries: list[str] = []
        self._responses = responses or {}

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:  # type: ignore[override]
        self.queries.append(query)
        for needle, resp in self._responses.items():
            if needle in query:
                return resp
        # 삭제 루프가 무한히 돌지 않도록 0건을 돌려준다.
        if "DETACH DELETE" in query:
            return [{"deleted": 0}]
        if "count(" in query:
            return [{"c": 0}]
        return []


def _counts(**kw: int) -> list[dict[str, Any]]:
    return [dict(kw)]


# ── 삭제 안전장치 ─────────────────────────────────────────────────────────────


def test_purge_refuses_when_m23_graph_is_empty() -> None:
    """새 그래프가 없는데 옛 그래프를 지우면 검색이 통째로 죽는다."""
    store = RecordingStore({"MATCH (n:CanonicalEntity) RETURN count(n)": _counts(c=0)})
    result = store.purge_legacy_keyword_graph()
    assert result["purged"] is False
    assert "구축" in result["reason"]
    assert not any("DETACH DELETE" in q for q in store.queries), "거부했는데 삭제를 시도했다"


def test_purge_refuses_when_labels_overlap() -> None:
    """M_23 노드가 legacy 라벨을 겸하고 있으면 삭제가 곧 데이터 손실이다."""
    store = RecordingStore(
        {
            "MATCH (n:CanonicalEntity) RETURN count(n)": _counts(c=100),
            "MATCH (n:Mention) RETURN count(n)": _counts(c=100),
            "WHERE n:Keyword OR n:Entity": _counts(c=7),
        }
    )
    result = store.purge_legacy_keyword_graph()
    assert result["purged"] is False
    assert "겸하고" in result["reason"]
    assert not any("DETACH DELETE" in q for q in store.queries)


def test_purge_never_targets_m23_labels() -> None:
    """삭제 대상 라벨에 M_23 라벨이 절대 들어가면 안 된다."""
    m23_labels = {"CanonicalEntity", "Mention", "Project", "Chunk", "Document"}
    assert set(KgGraphStore._LEGACY_LABELS).isdisjoint(m23_labels)
    # Document는 두 그래프가 공유하므로 특히 중요하다.
    assert "Document" not in KgGraphStore._LEGACY_LABELS


def test_purge_deletes_only_legacy_labels_when_safe() -> None:
    store = RecordingStore(
        {
            "MATCH (n:CanonicalEntity) RETURN count(n)": _counts(c=100),
            "MATCH (n:Mention) RETURN count(n)": _counts(c=100),
            "WHERE n:Keyword OR n:Entity": _counts(c=0),
        }
    )
    result = store.purge_legacy_keyword_graph()
    assert result["purged"] is True
    deletes = [q for q in store.queries if "DETACH DELETE" in q]
    assert deletes, "안전한데 아무것도 지우지 않았다"
    for q in deletes:
        assert "(n:Document)" not in q
        assert "(n:CanonicalEntity)" not in q
        assert "(n:Mention)" not in q


def test_preflight_does_not_delete_anything() -> None:
    """미리보기는 이름 그대로 아무것도 지우면 안 된다."""
    store = RecordingStore()
    store.purge_preflight()
    assert not any("DELETE" in q for q in store.queries)


def test_clear_all_covers_m23_and_legacy() -> None:
    """초기화는 M_23까지 함께 지워야 반쪽 그래프가 남지 않는다."""
    store = RecordingStore()
    store.clear_all()
    deletes = " ".join(q for q in store.queries if "DETACH DELETE" in q)
    for label in ("Mention", "Chunk", "CanonicalEntity", "Project", "Document", "Keyword"):
        assert f"(n:{label})" in deletes, f"{label}을 지우지 않는다 — 고아 노드가 남는다"


# ── Cypher 조립 안전 ──────────────────────────────────────────────────────────


def test_rejects_unknown_label() -> None:
    """라벨은 파라미터가 안 되므로 화이트리스트가 유일한 방벽이다."""
    store = RecordingStore()
    with pytest.raises(KgGraphStoreError):
        store.delete_stale("b1", ["Person; DROP DATABASE neo4j"])


def test_rejects_unknown_relation_type() -> None:
    store = RecordingStore()
    with pytest.raises(KgGraphStoreError):
        store.upsert_project_relations("EVIL_REL", [{"project_id": "p", "canonical_id": "c"}])


def test_accepts_spec_relation_types() -> None:
    """스펙 §5.2의 관계 7종은 통과해야 한다."""
    store = RecordingStore()
    for rel in (
        "HAS_PROBLEM",
        "HAS_OBJECTIVE",
        "TARGETS",
        "USES_TECHNOLOGY",
        "USES_METHOD",
        "USES_DATASET",
        "PRODUCES",
    ):
        store.upsert_project_relations(rel, [{"project_id": "p", "canonical_id": "c"}])
    assert len(store.queries) == 7


# ── 시각화 ────────────────────────────────────────────────────────────────────


def test_snapshot_balances_across_entity_types() -> None:
    """유형별로 고르게 뽑아야 한다.

    df 순으로만 자르면 상위가 전부 작물(RESEARCH_TARGET)이라 기술·방법이 화면에서
    사라진다 — 실측으로 200개 중 38개가 작물, 기술은 1개뿐이었다.
    """
    store = RecordingStore()
    store.snapshot(limit=70)
    first = store.queries[0]
    assert "UNWIND $types AS t" in first, "유형별로 나눠 뽑지 않는다"
    assert "collect(c)[0.." in first, "유형별 상한이 없다"


def test_snapshot_caps_projects_per_entity() -> None:
    """엔티티 하나가 화면을 문서로 가득 채우지 못하게 한다 ('벼'는 과제 422개)."""
    store = RecordingStore(
        {
            "UNWIND $types AS t": [
                {"cid": "c1", "cname": "벼", "ctype": "RESEARCH_TARGET", "df": 343}
            ]
        }
    )
    store.snapshot(limit=70)
    proj_q = [q for q in store.queries if "<-[r]-(p:Project)" in q]
    assert proj_q, "과제 엣지를 안 가져온다"
    assert "collect({pid:" in proj_q[0] and "[0..$cap]" in proj_q[0], "엔티티별 과제 상한이 없다"

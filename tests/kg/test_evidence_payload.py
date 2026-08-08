# tests/kg/test_evidence_payload.py
"""E-101 근거 그래프 노드 규약.

**증상**: 채팅 답변 아래 "근거 그래프"를 눌러도 아무 일이 안 일어났다.
**원인**: 근거 payload가 문서 노드 id에 raw `doc_id`를 넣는데 개요 그래프는
`Project.project_id`를 쓴다. 프론트가 근거 노드를 개요에서 찾지 못했다 —
실측 근거 노드 94건 중 개요에 존재하는 것 **0건**.
"""

from __future__ import annotations

from typing import Any

from kg.retrieve import EntityMatch, evidence_payload


def _match(cid: str, name: str, docs: list[str]) -> EntityMatch:
    return EntityMatch(
        canonical_id=cid,
        canonical_name=name,
        entity_type="TECHNOLOGY",
        document_frequency=3,
        is_boilerplate=False,
        doc_ids=docs,
    )


class _FakeGraph:
    """`evidence_snapshot`이 개요와 같은 규약으로 돌려주는 상황."""

    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        self._nodes, self._edges = nodes, edges
        self.asked: list[str] = []

    def evidence_snapshot(self, ids: Any, projects_per_entity: int = 8) -> dict[str, Any]:
        self.asked = list(ids)
        return {"nodes": self._nodes, "edges": self._edges}


def test_uses_graph_node_convention_when_available() -> None:
    """그래프가 있으면 문서 노드 id가 project_id, doc_id는 별도 필드여야 한다."""
    graph = _FakeGraph(
        nodes=[
            {
                "id": "ce_1",
                "label": "스마트팜",
                "kind": "entity",
                "type": "TECHNOLOGY",
                "doc_id": "",
            },
            {
                "id": "pj:PJ012345",
                "label": "스마트팜 실증",
                "kind": "document",
                "doc_id": "a.pdf_1",
            },
        ],
        edges=[{"source": "pj:PJ012345", "target": "ce_1", "kind": "uses", "weight": 1.0}],
    )
    out = evidence_payload("질의", [_match("ce_1", "스마트팜", ["a.pdf_1"])], ["c1"], graph=graph)
    docs = [n for n in out["nodes"] if n["kind"] == "document"]
    assert docs[0]["id"] == "pj:PJ012345", "개요와 같은 project_id를 써야 한다"
    assert docs[0]["doc_id"] == "a.pdf_1", "문서를 열 값이 없으면 열기 버튼이 죽는다"
    assert docs[0]["label"] != docs[0]["doc_id"], "라벨이 파일명이면 그래프에 파일명이 그려진다"
    assert graph.asked == ["ce_1"]


def test_falls_back_when_no_graph() -> None:
    """그래프가 없어도 근거가 아예 안 뜨는 것보다는 낫다 — 예전 형태로 반환."""
    out = evidence_payload("질의", [_match("ce_1", "스마트팜", ["a.pdf_1"])], ["c1"])
    ids = {n["id"] for n in out["nodes"]}
    assert ids == {"ce_1", "a.pdf_1"}
    doc = next(n for n in out["nodes"] if n["kind"] == "document")
    assert doc["doc_id"] == "a.pdf_1", "폴백에서도 doc_id는 채워야 한다"


def test_falls_back_when_snapshot_empty() -> None:
    """스냅샷이 비면 폴백 — 빈 그래프를 그려 '고장'처럼 보이게 하지 않는다."""
    out = evidence_payload(
        "질의", [_match("ce_1", "스마트팜", ["a.pdf_1"])], ["c1"], graph=_FakeGraph([], [])
    )
    assert out["nodes"], "빈 결과를 그대로 내보내면 화면이 백지가 된다"


def test_edges_reference_existing_nodes() -> None:
    """엣지 양 끝이 노드 목록에 있어야 한다 — 프론트가 그런 엣지를 버린다."""
    out = evidence_payload(
        "질의",
        [_match("ce_1", "A", ["d1"]), _match("ce_2", "B", ["d1", "d2"])],
        ["c1"],
    )
    ids = {n["id"] for n in out["nodes"]}
    for e in out["edges"]:
        assert e["source"] in ids and e["target"] in ids, f"떠 있는 엣지: {e}"


def test_chunk_ids_preserved() -> None:
    """chunk_ids는 근거 하이라이트에 쓰이므로 그대로 넘어가야 한다."""
    out = evidence_payload("질의", [_match("ce_1", "A", ["d1"])], ["c1", "c2", "c3"])
    assert out["chunk_ids"] == ["c1", "c2", "c3"]

# tests/graph_rag/fakes.py
"""M_19 테스트 대역: 인메모리 GraphStore + 가짜 벡터스토어/추출 LLM."""

from __future__ import annotations

from typing import Any

from graph_rag.store import GraphStore
from graph_rag.types import (
    ChunkLink,
    Entity,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    KeywordMention,
    ProjectInfo,
    Relation,
)


class FakeGraphStore(GraphStore):
    """dict 기반 인메모리 GraphStore — Neo4jGraphStore와 동일 계약."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.entities: dict[str, Entity] = {}
        self.relations: dict[tuple[str, str, str], Relation] = {}
        self.chunk_links: set[tuple[str, str]] = set()  # (entity_id, chunk_id)
        self.chunk_parent: dict[str, tuple[str, str]] = {}  # chunk_id → (parent_id, kind)
        self.documents: dict[str, dict[str, str]] = {}
        self.notes: dict[str, str] = {}
        # CR-30
        self.projects: dict[str, ProjectInfo] = {}
        self.keywords: dict[str, KeywordMention] = {}
        self.closed = False

    # ── 계약 구현 ─────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        return self.alive

    def ensure_schema(self) -> None:
        pass

    def upsert_document(self, doc_id: str, name: str, category: str = "") -> None:
        self.documents[doc_id] = {"name": name, "category": category}

    def upsert_note(self, slug: str, title: str) -> None:
        self.notes[slug] = title

    def upsert_entities(self, entities: list[Entity]) -> None:
        for e in entities:
            existing = self.entities.get(e.id)
            if existing is None:
                self.entities[e.id] = e
            elif not existing.description and e.description:
                self.entities[e.id] = Entity(
                    id=e.id, name=existing.name, type=existing.type, description=e.description
                )

    def upsert_relations(self, relations: list[Relation]) -> None:
        for r in relations:
            key = (r.source_id, r.target_id, r.type)
            existing = self.relations.get(key)
            if existing is None:
                self.relations[key] = r
            else:
                self.relations[key] = Relation(
                    source_id=r.source_id,
                    target_id=r.target_id,
                    type=r.type,
                    description=existing.description,
                    weight=existing.weight + r.weight,
                )

    def link_chunks(self, links: list[ChunkLink], parent_id: str, parent_kind: str) -> None:
        for ln in links:
            self.chunk_links.add((ln.entity_id, ln.chunk_id))
            self.chunk_parent[ln.chunk_id] = (parent_id, parent_kind)

    def find_entities(self, terms: list[str], limit: int = 20) -> list[Entity]:
        out: list[Entity] = []
        terms_norm = [t.casefold() for t in terms if t.strip()]
        for e in self.entities.values():
            norm = e.id.rsplit(":", 1)[0]
            # 양방향 포함 — 역포함은 이름 3자 이상만 (Neo4jGraphStore.find_entities와 동일 계약)
            if any(t in norm or (len(norm) >= 3 and norm in t) for t in terms_norm):
                out.append(e)
            if len(out) >= limit:
                break
        return out

    def neighbors(self, entity_ids: list[str], hops: int = 1, limit: int = 50) -> list[Entity]:
        frontier = set(entity_ids)
        seen = set(entity_ids)
        for _ in range(max(1, hops)):
            nxt: set[str] = set()
            for src, dst, _t in self.relations:
                if src in frontier and dst not in seen:
                    nxt.add(dst)
                if dst in frontier and src not in seen:
                    nxt.add(src)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        result_ids = [i for i in seen if i not in set(entity_ids)]
        return [self.entities[i] for i in result_ids if i in self.entities][:limit]

    def chunks_for_entities(self, entity_ids: list[str], limit: int = 30) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        eids = set(entity_ids)
        for eid, cid in self.chunk_links:
            if eid in eids:
                counts[cid] = counts.get(cid, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: -kv[1])
        return ordered[:limit]

    def subgraph(self, entity_ids: list[str], chunk_ids: list[str]) -> GraphSnapshot:
        nodes = [
            GraphNode(id=e.id, label=e.name, kind="entity", type=e.type)
            for e in self.entities.values()
            if e.id in set(entity_ids)
        ]
        edges = [
            GraphEdge(source=s, target=t, kind="rel", weight=r.weight)
            for (s, t, _ty), r in self.relations.items()
            if s in set(entity_ids) and t in set(entity_ids)
        ]
        for cid in chunk_ids:
            parent = self.chunk_parent.get(cid)
            if parent:
                pid, kind = parent
                if not any(n.id == pid for n in nodes):
                    nodes.append(GraphNode(id=pid, label=pid, kind=kind))
        return GraphSnapshot(nodes=nodes, edges=edges)

    def snapshot(self, limit: int = 500, entity_types: list[str] | None = None) -> GraphSnapshot:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        # CR-30: Project(문서) + 역할 키워드 (Neo4jGraphStore.snapshot와 동일 계약)
        roles = set(entity_types) if entity_types else None
        for k in self.keywords.values():
            if roles is not None and k.role not in roles:
                continue
            did = k.doc_id
            if did not in nodes:
                proj = self.projects.get(did)
                label = (
                    (proj.title if proj else "") or self.documents.get(did, {}).get("name") or did
                )
                nodes[did] = GraphNode(id=did, label=label, kind="document")
            if k.id not in nodes:
                nodes[k.id] = GraphNode(
                    id=k.id, label=k.normalized_term or k.raw_term, kind="keyword", type=k.role
                )
            edges.append(GraphEdge(source=did, target=k.id, kind="has_keyword"))

        # (구) 엔티티 관계 (CR-30 이후 잔존 데이터 호환)
        ents = [
            e for e in self.entities.values() if entity_types is None or e.type in set(entity_types)
        ][:limit]
        for e in ents:
            nodes[e.id] = GraphNode(id=e.id, label=e.name, kind="entity", type=e.type)
        eids = {e.id for e in ents}
        for (s, t, _ty), r in self.relations.items():
            if s in eids and t in eids:
                edges.append(GraphEdge(source=s, target=t, kind="rel", weight=r.weight))

        # CR-34: 공유 키워드(정규화 용어 우선)·동일 역할 기반 문서-문서 연관 엣지
        term_docs: dict[tuple[str, str], set[str]] = {}
        for k in self.keywords.values():
            if roles is not None and k.role not in roles:
                continue
            term = (k.normalized_term or k.raw_term).casefold()
            if len(term) < 2:
                continue
            term_docs.setdefault((term, k.role), set()).add(k.doc_id)
        shared: dict[tuple[str, str], int] = {}
        for docs in term_docs.values():
            if not (2 <= len(docs) <= 15):  # _RELATED_MAX_FANOUT
                continue
            ordered = sorted(docs)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    shared[(a, b)] = shared.get((a, b), 0) + 1
        for (a, b), w in shared.items():
            if a in nodes and b in nodes:
                edges.append(GraphEdge(source=a, target=b, kind="related", weight=float(w)))

        return GraphSnapshot(nodes=list(nodes.values()), edges=edges)

    def doc_focus_snapshot(self, doc_id: str, limit: int = 40) -> GraphSnapshot:
        # CR-37: 중심 문서 + 키워드 + 공유 키워드로 이어진 문서 (Neo4j와 동일 계약)
        proj = self.projects.get(doc_id)
        if proj is None:
            return GraphSnapshot(nodes=[], edges=[])
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        label = proj.title or self.documents.get(doc_id, {}).get("name") or doc_id
        nodes[doc_id] = GraphNode(id=doc_id, label=label, kind="document")
        my_terms: set[tuple[str, str]] = set()
        for k in self.keywords.values():
            if k.doc_id != doc_id:
                continue
            nodes[k.id] = GraphNode(
                id=k.id, label=k.normalized_term or k.raw_term, kind="keyword", type=k.role
            )
            edges.append(GraphEdge(source=doc_id, target=k.id, kind="has_keyword"))
            term = (k.normalized_term or k.raw_term).casefold()
            if len(term) >= 2:
                my_terms.add((term, k.role))
        shared: dict[str, int] = {}
        for k in self.keywords.values():
            if k.doc_id == doc_id:
                continue
            term = (k.normalized_term or k.raw_term).casefold()
            if (term, k.role) in my_terms:
                shared[k.doc_id] = shared.get(k.doc_id, 0) + 1
        for did, w in sorted(shared.items(), key=lambda kv: -kv[1])[:limit]:
            p2 = self.projects.get(did)
            if did not in nodes:
                nodes[did] = GraphNode(
                    id=did, label=(p2.title if p2 else did) or did, kind="document"
                )
            edges.append(GraphEdge(source=doc_id, target=did, kind="related", weight=float(w)))
        return GraphSnapshot(nodes=list(nodes.values()), edges=edges)

    def delete_by_doc_id(self, doc_id: str) -> None:
        self.documents.pop(doc_id, None)
        self.notes.pop(doc_id, None)
        dead_chunks = {cid for cid, (pid, _k) in self.chunk_parent.items() if pid == doc_id}
        self.chunk_links = {(eid, cid) for eid, cid in self.chunk_links if cid not in dead_chunks}
        for cid in dead_chunks:
            self.chunk_parent.pop(cid, None)
        # 고아 엔티티 정리
        linked = {eid for eid, _cid in self.chunk_links}
        self.entities = {i: e for i, e in self.entities.items() if i in linked}

    def all_entities(self, limit: int = 2000) -> list[Entity]:
        return list(self.entities.values())[:limit]

    def merge_entities(self, target_id: str, source_ids: list[str]) -> int:
        source_ids = [s for s in source_ids if s and s != target_id and s in self.entities]
        if not source_ids or target_id not in self.entities:
            return 0
        srcs = set(source_ids)
        # 청크 언급 이전
        self.chunk_links = {
            (target_id if eid in srcs else eid, cid) for eid, cid in self.chunk_links
        }
        # 관계 이전 (source/target 재지정, 자기참조 제거)
        new_rels: dict[tuple[str, str, str], Relation] = {}
        for (s, t, ty), r in self.relations.items():
            ns = target_id if s in srcs else s
            nt = target_id if t in srcs else t
            if ns == nt:
                continue
            new_rels[(ns, nt, ty)] = Relation(
                source_id=ns, target_id=nt, type=ty, description=r.description, weight=r.weight
            )
        self.relations = new_rels
        for sid in source_ids:
            self.entities.pop(sid, None)
        return len(source_ids)

    def clear_all(self) -> dict[str, int]:
        before = self.stats()
        self.entities.clear()
        self.relations.clear()
        self.chunk_links.clear()
        self.chunk_parent.clear()
        self.documents.clear()
        self.notes.clear()
        self.projects.clear()
        self.keywords.clear()
        return before

    # ── CR-30: Project + 역할 키워드 ─────────────────────────────────────────

    def upsert_project_bundle(self, project: ProjectInfo, keywords: list[KeywordMention]) -> None:
        self.projects[project.doc_id] = project
        # 문서의 기존 키워드 교체 (재인덱싱 멱등)
        self.keywords = {kid: k for kid, k in self.keywords.items() if k.doc_id != project.doc_id}
        for k in keywords:
            self.keywords[k.id] = k

    def find_keywords(self, terms: list[str], limit: int = 30) -> list[KeywordMention]:
        terms_norm = [t.casefold() for t in terms if t.strip()]
        out: list[KeywordMention] = []
        for k in self.keywords.values():
            raw = k.raw_term.casefold()
            norm = k.normalized_term.casefold()
            if any(
                t in raw or (norm and t in norm) or (len(raw) >= 3 and raw in t) for t in terms_norm
            ):
                out.append(k)
            if len(out) >= limit:
                break
        return out

    def keywords_for_doc(self, doc_id: str) -> list[KeywordMention]:
        return [k for k in self.keywords.values() if k.doc_id == doc_id]

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = " ".join((query or "").split()).casefold()
        if len(q) < 2:
            return []
        out: list[dict[str, Any]] = []
        for doc_id, proj in self.projects.items():
            title = (proj.title or "").casefold()
            title_match = q in title
            matched = [
                k.raw_term
                for k in self.keywords.values()
                if k.doc_id == doc_id
                and (
                    q in k.raw_term.casefold()
                    or (k.normalized_term and q in k.normalized_term.casefold())
                )
            ]
            if title_match or matched:
                out.append(
                    {
                        "doc_id": doc_id,
                        "title": proj.title or doc_id,
                        "project_no": proj.project_no,
                        "title_match": title_match,
                        "matched_keywords": matched,
                    }
                )
        out.sort(key=lambda d: (len(d["matched_keywords"]), d["title_match"]), reverse=True)
        return out[:limit]

    def all_keywords(self, limit: int = 5000) -> list[KeywordMention]:
        return list(self.keywords.values())[:limit]

    def update_keyword_normalization(self, keyword_ids: list[str], normalized_term: str) -> int:
        n = 0
        for kid in keyword_ids:
            k = self.keywords.get(kid)
            if k is None:
                continue
            self.keywords[kid] = KeywordMention(
                doc_id=k.doc_id,
                raw_term=k.raw_term,  # raw 보존
                role=k.role,
                confidence=k.confidence,
                normalized_term=normalized_term,
                normalization_status="normalized",
            )
            n += 1
        return n

    def existing_doc_ids(self) -> list[str]:
        # CR-30 그래프 문서 = upsert_project_bundle로 등록된 Project(=Document)
        return list(self.projects)

    def reset_keyword_normalization(self) -> int:
        n = 0
        for kid, k in list(self.keywords.items()):
            self.keywords[kid] = KeywordMention(
                doc_id=k.doc_id,
                raw_term=k.raw_term,
                role=k.role,
                confidence=k.confidence,
                normalized_term="",
                normalization_status="raw",
            )
            n += 1
        return n

    def mark_keywords_processed(self, keyword_ids: list[str]) -> int:
        n = 0
        for kid in keyword_ids:
            k = self.keywords.get(kid)
            if k is None:
                continue
            self.keywords[kid] = KeywordMention(
                doc_id=k.doc_id,
                raw_term=k.raw_term,
                role=k.role,
                confidence=k.confidence,
                normalized_term=k.normalized_term or k.raw_term,
                normalization_status="normalized",
            )
            n += 1
        return n

    def stats(self) -> dict[str, int]:
        return {
            "entities": len(self.entities),
            "relations": len(self.relations),
            "keywords": len(self.keywords),
            "chunks": len(self.chunk_parent),
            "documents": len(self.documents),
            "notes": len(self.notes),
        }

    def close(self) -> None:
        self.closed = True


class FakeVectorStore:
    """get_chunks_by_doc_id / get_chunks_by_chunk_ids만 구현한 대역."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def get_chunks_by_doc_id(self, doc_id: str, limit: int = 30) -> list[dict[str, Any]]:
        return [r for r in self.rows if r.get("doc_id") == doc_id][:limit]

    def get_chunks_by_chunk_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(chunk_ids)
        return [r for r in self.rows if r.get("chunk_id") in wanted]


class FakeEmbedder:
    """CR-36 테스트 대역: 용어를 '개념'별 직교 단위벡터로 임베딩.

    같은 개념끼리는 코사인 1.0(임계값 초과 → 병합), 다른 개념은 0.0(비병합).
    concept_of 미지정 용어는 자기 자신이 개념(= 단독 군집).
    """

    def __init__(
        self,
        concept_of: dict[str, str] | None = None,
        dim: int = 32,
        vectors: dict[str, list[float]] | None = None,
    ) -> None:
        self.concept_of = concept_of or {}
        self.dim = dim
        self.vectors = vectors or {}  # 명시적 벡터(chaining 등 세밀한 유사도 구성용)
        self._concepts: dict[str, int] = {}

    def embed_passages(self, texts: list[str]) -> Any:
        import numpy as np

        vlen = len(next(iter(self.vectors.values()))) if self.vectors else self.dim
        out = np.zeros((len(texts), vlen), dtype=np.float32)
        for i, t in enumerate(texts):
            if t in self.vectors:
                out[i, : len(self.vectors[t])] = self.vectors[t]
                continue
            concept = self.concept_of.get(t, t)
            idx = self._concepts.setdefault(concept, len(self._concepts) % vlen)
            out[i, idx] = 1.0
        return out


def make_row(
    chunk_id: str,
    doc_id: str = "doc1",
    text: str = "본문",
    doc_name: str = "문서.pdf",
    category: str | None = None,
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "category": category,
        "page": 1,
        "section": None,
        "chunk_id": chunk_id,
        "text": text,
        "bbox": None,
        "source_path": "",
    }


class FakeCompleteJson:
    """청크 텍스트에 따라 미리 정의된 추출 결과를 돌려주는 가짜 LLM."""

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []
        self.fail = False

    async def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        self.calls.append(user_prompt)
        if self.fail:
            raise RuntimeError("LLM 죽음")
        for key, resp in self.responses.items():
            if key in user_prompt:
                return resp
        return {"entities": [], "relations": []}

# src/graph_rag/__init__.py
"""M_19 GraphRAG — 엔티티·관계 그래프 기반 RAG 보강 (CR-18).

공개 API:
    GraphStore (ABC), Neo4jGraphStore, EntityExtractor, GraphRagService
    types: Entity, Relation, ChunkLink, GraphNode, GraphEdge, GraphSnapshot,
           EvidenceSubgraph, IndexStatus
"""

from .errors import GraphRagError, GraphStoreError
from .extractor import EntityExtractor
from .service import GraphRagService
from .store import GraphStore
from .types import (
    ChunkLink,
    Entity,
    EvidenceSubgraph,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    IndexStatus,
    Relation,
)

__all__ = [
    "ChunkLink",
    "Entity",
    "EntityExtractor",
    "EvidenceSubgraph",
    "GraphEdge",
    "GraphNode",
    "GraphRagError",
    "GraphRagService",
    "GraphSnapshot",
    "GraphStore",
    "GraphStoreError",
    "IndexStatus",
    "Relation",
]

# src/graph_rag/errors.py
"""M_19 GraphRAG 예외 계층."""


class GraphRagError(Exception):
    """GraphRAG 모듈 최상위 예외."""


class GraphStoreError(GraphRagError):
    """그래프 저장소(연결·쿼리) 오류."""

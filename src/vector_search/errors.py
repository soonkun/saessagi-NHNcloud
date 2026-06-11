# src/vector_search/errors.py
"""M_07 VectorSearch 예외 타입."""


class VectorSearchError(Exception):
    """M_07 공통 기본 예외."""


class EmbedderError(VectorSearchError):
    """BGE-M3 로드·추론 실패."""


class VectorStoreError(VectorSearchError):
    """LanceDB I/O·스키마 불일치·연결 실패."""


class RetrievalError(VectorSearchError):
    """RagService 상위 파사드에서 발생하는 조합 실패(예: embedder 결과 NaN)."""


class RerankerError(VectorSearchError):
    """bge-reranker 로드·추론 실패 (M_18)."""

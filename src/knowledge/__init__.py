"""M_15 — Knowledge Notes Phase 1.

옵시디언 스타일 로컬 markdown 노트. 본문은 RAG에 `__knowledge__` 카테고리로
임베딩돼 일반 질문 답변에서도 hit 대상이 된다.
"""

from .service import KnowledgeService, KnowledgeNote, KnowledgeNoteMeta, KnowledgeGraph

__all__ = [
    "KnowledgeService",
    "KnowledgeNote",
    "KnowledgeNoteMeta",
    "KnowledgeGraph",
]

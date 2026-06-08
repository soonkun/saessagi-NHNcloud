# src/vector_search/rag.py
"""RagService: Embedder + VectorStore 파사드 (M_07 §4.7, §6.3, §7)."""

from __future__ import annotations

import logging
from typing import Literal

from .embedder import Embedder
from .store import VectorStore
from .types import RetrievalResult, SearchHit

logger = logging.getLogger(__name__)


class RagService:
    """Embedder + VectorStore 파사드. sync API.

    Args:
        embedder:   Embedder 인스턴스.
        store:      VectorStore 인스턴스.
        min_score:  "관련 없음" 판정 임계값. 기본 0.35 (스펙 §6.3.2).
                    conf.yaml의 `rag.min_score`로 덮어쓸 수 있다.
        top_k_max:  retrieve 호출의 top_k 상한. 기본 20. (JSON Schema와 일치)
    """

    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        min_score: float = 0.35,
        top_k_max: int = 20,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._min_score = min_score
        self._top_k_max = top_k_max
        logger.info("RagService 초기화 완료: min_score=%.2f, top_k_max=%d", min_score, top_k_max)

    def retrieve(
        self,
        query: str,
        top_k: int = 8,
        category: str | None = None,
        source: Literal["docs", "notes", "both"] = "both",  # M_16: 소스 필터 (신규)
    ) -> RetrievalResult:
        """쿼리를 임베딩해 VectorStore를 검색하고 RetrievalResult를 반환.

        **sync 함수로 확정** (스펙 §15.1). 호출자(ToolRouter._handle_search_docs)가
        `run_in_executor(None, lambda: self._rag.retrieve(query, top_k, category))`로
        외부 async 경계에 어댑팅한다.

        흐름:
          1. query.strip() == "" → 빈 hits + found=False + no_match_reason="쿼리가 비어있습니다"
          2. top_k <= 0 → raise ValueError("top_k must be >= 1")
          3. top_k > top_k_max → clamp(top_k = top_k_max) + warning 로그
          4. query_vec = embedder.embed_query(query)
          5. hits = store.search(query_vec, top_k=top_k, category=category)
          6. found = len(hits) > 0 and hits[0].score >= min_score
          7. found=False면 no_match_reason 생성

        반환 계약 (found=False인 경우의 hits 유지 정책):
          - found=False여도 hits는 그대로 top_k 채워 반환한다(단, 0건일 수도 있음).
        """
        # 1. 빈 쿼리 처리 (스펙 §6.3.1)
        if not query or not query.strip():
            logger.info("empty query received")
            return RetrievalResult(
                hits=[],
                found=False,
                no_match_reason="쿼리가 비어있습니다",
            )

        # 2. top_k 검증 (스펙 §6.3.3)
        if top_k <= 0:
            logger.warning("retrieve top_k <= 0: %d", top_k)
            raise ValueError("top_k must be >= 1")

        # 3. top_k clamp
        if top_k > self._top_k_max:
            logger.warning("retrieve top_k=%d > top_k_max=%d, clamp", top_k, self._top_k_max)
            top_k = self._top_k_max

        # 4. 임베딩
        query_vec = self._embedder.embed_query(query)

        # 5. 검색 (M_16: source 파라미터 전달)
        hits = self._store.search(query_vec, top_k=top_k, category=category, source=source)

        # 6, 7. found 판정 및 no_match_reason 생성 (스펙 §6.3.2)
        if len(hits) > 0 and hits[0].score >= self._min_score:
            found = True
            no_match_reason = None
        else:
            found = False
            if len(hits) == 0:
                no_match_reason = "등록된 문서에서 관련 내용을 찾지 못했습니다"
            else:
                top_score = hits[0].score
                no_match_reason = (
                    f"등록된 문서에서 관련 내용을 찾지 못했습니다 "
                    f"(최고 유사도 {top_score:.2f} < {self._min_score:.2f})"
                )
            logger.info("retrieve found=False: %s", no_match_reason)

        return RetrievalResult(hits=hits, found=found, no_match_reason=no_match_reason)

    def format_citation(self, hit: SearchHit) -> str:
        """인용 문자열을 한국어 고정 포맷으로 생성 (스펙 §7).

        | page | section | 반환 문자열 예 |
        |---|---|---|
        | 12 | "예산 승인 절차" | `예산지침.pdf` 12페이지, '예산 승인 절차' 섹션 |
        | 12 | None | `예산지침.pdf` 12페이지 |
        | None | "1. 서론" | `회의록.docx` '1. 서론' 섹션 |
        | None | None | `메모.txt` |
        """
        doc = f"`{hit.doc_name}`"
        parts: list[str] = [doc]
        if hit.page is not None:
            parts.append(f"{hit.page}페이지")
        if hit.section:
            parts.append(f"'{hit.section}' 섹션")
        # 연결: 첫 요소(doc) 뒤에는 공백, 이후는 ", "
        if len(parts) == 1:
            return parts[0]
        return parts[0] + " " + ", ".join(parts[1:])

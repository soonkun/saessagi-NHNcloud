# src/vector_search/reranker.py
"""bge-reranker-v2-m3 cross-encoder 리랭커 (M_18 §3.1)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from .errors import RerankerError
from .types import SearchHit

logger = logging.getLogger(__name__)


class Reranker:
    """질문-청크 쌍을 cross-encoder로 정밀 재채점해 상위 top_k를 고른다.

    임베더(bi-encoder)는 질문과 청크를 따로 벡터화해 비교하지만, cross-encoder는
    두 텍스트를 한 입력으로 읽어 관련도를 직접 판단하므로 정밀도가 높다.
    대신 후보 수에 비례해 느려서 벡터 검색 상위 후보(기본 30개)에만 적용한다.

    Args:
        model_dir: bge-reranker-v2-m3 로컬 디렉토리.
        device: "cpu" | "cuda" | "auto". 기본 "auto".
        batch_size: predict 배치 크기.

    Raises:
        RerankerError: 모델 디렉토리 부재 또는 로드 실패.
    """

    def __init__(
        self,
        model_dir: str,
        device: str = "auto",
        batch_size: int = 32,
    ) -> None:
        # 오프라인 강제 (Embedder와 동일 정책)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        model_path = Path(model_dir)
        if not model_path.exists() or not (model_path / "config.json").exists():
            raise RerankerError(f"bge-reranker model not found at {model_dir}")

        self._batch_size = batch_size
        self._device = self._resolve_device(device)
        self._model: Any = None

        try:
            from sentence_transformers import CrossEncoder

            kwargs: dict[str, Any] = {"max_length": 512, "device": self._device}
            if self._device == "cuda":
                # FP16: 실측 323ms → 126ms (30쌍), top-8 순서 FP32와 동일 (무손실)
                import torch

                kwargs["automodel_args"] = {"torch_dtype": torch.float16}
            self._model = CrossEncoder(str(model_path), **kwargs)
        except Exception as exc:
            raise RerankerError(f"리랭커 모델 로드 실패: {exc}") from exc

        logger.info("Reranker 초기화 완료: model_dir=%s, device=%s", model_dir, self._device)

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def rerank(self, query: str, hits: list[SearchHit], top_k: int) -> list[SearchHit]:
        """후보 hits를 재채점해 상위 top_k를 반환한다.

        추론 실패 시 예외를 전파하지 않고 원래 순서의 top_k를 반환한다
        (graceful degradation — 검색 자체가 죽으면 안 된다, M_18 §2).

        SearchHit.score는 변경하지 않는다 — found 판정(cosine 기준) 의미 보존.
        """
        if not hits:
            return []
        if len(hits) <= 1:
            return hits[:top_k]

        try:
            pairs = [(query, h.text) for h in hits]
            scores = np.asarray(
                self._model.predict(pairs, batch_size=self._batch_size, show_progress_bar=False)
            )
            order = np.argsort(-scores)
            reranked = [hits[int(i)] for i in order[:top_k]]
            logger.debug(
                "rerank: %d후보 → top%d (1위 변경: %s)",
                len(hits),
                top_k,
                "yes" if reranked and reranked[0].chunk_id != hits[0].chunk_id else "no",
            )
            return reranked
        except Exception as exc:
            logger.warning("rerank 추론 실패 — 벡터 순서 유지: %s", exc)
            return hits[:top_k]

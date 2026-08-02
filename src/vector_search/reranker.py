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


def _disable_cudnn_sdpa() -> None:
    """PyTorch의 cuDNN attention 백엔드를 끈다 (E-88).

    torch 2.11+cu130 / cuDNN 9.19 / B200(sm_100) 조합에서 cross-encoder를 FP16으로
    돌리면 cuDNN이 attention 실행 계획을 만들지 못한다:

        RuntimeError: cuDNN Frontend error: [cudnn_frontend] Error:
                      No valid execution plans built.

    문제는 이 예외를 잡아도 끝이 아니라는 것이다. 계획 수립에 실패한 뒤 **다음 호출에서
    libtorch_cuda.so 안에서 segfault가 나고 프로세스가 통째로 죽는다.** 파이썬 예외가
    아니라 프로세스 사망이라 try/except로는 막을 수 없고, 로그에는 아무 흔적 없이
    서버만 사라진다(dmesg에만 남는다). 딥 리서치가 자주 죽던 원인이 이것이다 —
    검색 한 번에 리랭크가 여러 번 도니 두 번째 호출까지 금방 도달한다.

    cuDNN 백엔드를 끄면 flash/mem-efficient attention으로 내려가고, 실측상 오히려
    빠르다(30쌍 기준 106ms → 12ms). 프로세스 전역 설정이라 한 번만 부르면 된다.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return
        if hasattr(torch.backends.cuda, "enable_cudnn_sdp"):
            torch.backends.cuda.enable_cudnn_sdp(False)
            logger.info("cuDNN SDPA 백엔드 비활성화 (E-88 segfault 회피)")
    except Exception as exc:  # torch 미설치·API 변경 등
        logger.warning("cuDNN SDPA 비활성화 실패 (그대로 진행): %s", exc)


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
                # cuDNN attention 백엔드는 이 조합에서 프로세스를 죽인다 — 모델을 만들기
                # 전에 꺼야 한다 (E-88).
                _disable_cudnn_sdpa()
                # FP16: 실측 323ms → 126ms (30쌍), top-8 순서 FP32와 동일 (무손실)
                import torch

                # `automodel_args`/`torch_dtype`은 둘 다 이름이 바뀌어 경고를 낸다.
                kwargs["model_kwargs"] = {"dtype": torch.float16}
            self._model = CrossEncoder(str(model_path), **kwargs)
        except Exception as exc:
            raise RerankerError(f"리랭커 모델 로드 실패: {exc}") from exc

        if self._device == "cuda":
            self._warmup()

        logger.info("Reranker 초기화 완료: model_dir=%s, device=%s", model_dir, self._device)

    def _warmup(self) -> None:
        """가장 긴 입력으로 한 번 돌려 커널 자동튜닝을 미리 끝낸다.

        첫 호출은 입력 길이에 따라 수 초가 걸린다(최대 길이에서 실측 12초). 그 비용을
        사용자의 첫 질문이 아니라 기동 시점으로 옮긴다. 실패해도 검색에는 지장이 없다.
        """
        try:
            import time

            started = time.time()
            self._model.predict(
                [("워밍업 질의", "본문 " * 400)] * 2, batch_size=2, show_progress_bar=False
            )
            logger.info("Reranker 워밍업 완료 (%.1fs)", time.time() - started)
        except Exception as exc:
            logger.warning("Reranker 워밍업 실패 (그대로 진행): %s", exc)

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

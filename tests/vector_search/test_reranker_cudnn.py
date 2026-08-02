# tests/vector_search/test_reranker_cudnn.py
"""E-88 회귀 방지: 리랭커가 cuDNN attention 백엔드를 끄고 모델을 만드는지.

torch 2.11+cu130 / cuDNN 9.19 / B200 조합에서 FP16 cross-encoder를 돌리면
cuDNN이 실행 계획을 만들지 못하고(RuntimeError), **그 다음 호출에서 프로세스가
segfault로 죽는다.** 파이썬 예외가 아니라 프로세스 사망이라 try/except로 막을 수
없고, 서버 로그에는 아무것도 남지 않는다.

여기서 확인하는 것은 두 가지다:
  1. cuda일 때 `enable_cudnn_sdp(False)`가 **CrossEncoder 생성 전에** 불린다.
     생성 후에 부르면 이미 만들어진 모듈이 옛 백엔드를 잡고 있을 수 있다.
  2. cpu일 때는 건드리지 않는다 (GPU가 없는 환경에서 부작용 금지).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _fake_torch(cuda_available: bool) -> MagicMock:
    torch = MagicMock(name="torch")
    torch.cuda.is_available.return_value = cuda_available
    torch.float16 = "float16-sentinel"
    # hasattr(torch.backends.cuda, "enable_cudnn_sdp") 가 True가 되도록 실제 속성을 둔다
    torch.backends.cuda.enable_cudnn_sdp = MagicMock(name="enable_cudnn_sdp")
    return torch


def _fake_cross_encoder() -> MagicMock:
    ce_cls = MagicMock(name="CrossEncoder")
    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = ce_cls  # type: ignore[attr-defined]
    sys.modules["sentence_transformers"] = module
    return ce_cls


@pytest.fixture
def model_dir(tmp_path: Path) -> str:
    d = tmp_path / "bge-reranker-v2-m3"
    d.mkdir()
    (d / "config.json").write_text("{}", encoding="utf-8")
    return str(d)


class TestCudnnSdpaGuard:
    def test_cuda_disables_cudnn_sdpa_before_model_build(self, model_dir: str) -> None:
        from vector_search.reranker import Reranker

        torch = _fake_torch(cuda_available=True)
        ce_cls = _fake_cross_encoder()

        order: list[str] = []
        torch.backends.cuda.enable_cudnn_sdp.side_effect = lambda *_: order.append("disable")
        ce_cls.side_effect = lambda *a, **k: order.append("build") or MagicMock()

        with patch.dict(sys.modules, {"torch": torch}):
            Reranker(model_dir, device="cuda")

        torch.backends.cuda.enable_cudnn_sdp.assert_called_once_with(False)
        # 순서가 뒤집히면 이미 만들어진 모듈이 옛 백엔드를 물고 있을 수 있다.
        assert order[:2] == ["disable", "build"], order

    def test_cpu_does_not_touch_torch_backends(self, model_dir: str) -> None:
        from vector_search.reranker import Reranker

        torch = _fake_torch(cuda_available=False)
        _fake_cross_encoder()

        with patch.dict(sys.modules, {"torch": torch}):
            Reranker(model_dir, device="cpu")

        torch.backends.cuda.enable_cudnn_sdp.assert_not_called()

    def test_warmup_failure_does_not_break_init(self, model_dir: str) -> None:
        """워밍업은 부가 기능이다 — 실패해도 리랭커는 살아 있어야 한다."""
        from vector_search.reranker import Reranker

        torch = _fake_torch(cuda_available=True)
        ce_cls = _fake_cross_encoder()
        model = MagicMock()
        model.predict.side_effect = RuntimeError("워밍업 실패")
        ce_cls.return_value = model

        with patch.dict(sys.modules, {"torch": torch}):
            rr = Reranker(model_dir, device="cuda")

        assert rr is not None

    def test_disable_helper_survives_missing_torch(self) -> None:
        """torch가 없거나 API가 바뀌어도 예외를 밖으로 던지지 않는다."""
        from vector_search.reranker import _disable_cudnn_sdpa

        with patch.dict(sys.modules, {"torch": None}):
            _disable_cudnn_sdpa()  # 예외가 나면 테스트 실패


class TestRerankStillDegradesGracefully:
    def test_inference_error_keeps_vector_order(self, model_dir: str) -> None:
        """추론이 실패해도 검색 자체는 살아 있어야 한다 (M_18 §2 유지 확인)."""
        from vector_search.reranker import Reranker
        from vector_search.types import SearchHit

        torch = _fake_torch(cuda_available=False)
        ce_cls = _fake_cross_encoder()
        model = MagicMock()
        model.predict.side_effect = RuntimeError("cuDNN Frontend error")
        ce_cls.return_value = model

        with patch.dict(sys.modules, {"torch": torch}):
            rr = Reranker(model_dir, device="cpu")

        hits: list[Any] = [
            SearchHit(
                doc_id="d1",
                doc_name="문서",
                category=None,
                page=1,
                section=None,
                chunk_id=f"c{i}",
                text="본문",
                bbox=None,
                source_path="/docs/test",
                score=0.9,
            )
            for i in range(5)
        ]
        out = rr.rerank("질의", hits, top_k=3)

        assert [h.chunk_id for h in out] == ["c0", "c1", "c2"]

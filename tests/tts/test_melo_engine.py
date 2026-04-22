# tests/tts/test_melo_engine.py
"""MeloTTSEngine 단위 테스트."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_llm_vtuber.tts.tts_interface import TTSInterface
from tts.errors import TTSInitError, TTSRuntimeError
from tts.melo_tts_engine import MeloTTSEngine


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_melo_engine(
    tmp_model_dir: Path,
    mock_melo_instance: MagicMock,
    *,
    device: str = "cpu",
    speed: float = 1.0,
    speaker_id: int | None = None,
) -> MeloTTSEngine:
    """MeloTTS 모듈을 mock 상태에서 MeloTTSEngine을 생성한다."""
    with (
        patch("tts._device._check_cuda_available", return_value=False),
        patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": MagicMock()}),
        patch("builtins.__import__", side_effect=_import_with_mock(mock_melo_instance)),
    ):
        engine = MeloTTSEngine(
            model_dir=str(tmp_model_dir),
            device=device,
            speed=speed,
            speaker_id=speaker_id,
        )
    return engine


def _import_with_mock(mock_instance: MagicMock):  # type: ignore[no-untyped-def]
    """melo.api.TTS import를 mock으로 교체하는 __import__ 패치."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__  # type: ignore[union-attr]

    def _patched_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "melo.api":
            mock_module = MagicMock()
            mock_module.TTS = MagicMock(return_value=mock_instance)
            return mock_module
        return real_import(name, *args, **kwargs)

    return _patched_import


# ---------------------------------------------------------------------------
# 정상 케이스
# ---------------------------------------------------------------------------


class TestMeloTTSEngineInit:
    """N-1: 정상 초기화."""

    def test_normal_init(self, tmp_model_dir: Path, mock_melo_tts: MagicMock) -> None:
        """N-1: MeloTTSEngine 정상 초기화 — speaker, speaker_id, resolved_device 확인."""
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
        ):
            # melo.api.TTS를 sys.modules에서 mock으로 제공
            mock_melo_module = MagicMock()
            mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
            with patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}):
                engine = MeloTTSEngine(
                    model_dir=str(tmp_model_dir),
                    speaker="KR",
                    language="KR",
                    speed=1.0,
                    sample_rate=24000,
                    device="auto",
                )

        assert engine.speaker == "KR"
        assert engine.speaker_id == 0
        assert engine.resolved_device in {"cpu", "cuda"}
        assert engine.language == "KR"
        assert isinstance(engine, TTSInterface)

    def test_normal_init_with_explicit_device_cpu(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock
    ) -> None:
        """device="cpu" 명시적 지정 초기화."""
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(model_dir=str(tmp_model_dir), device="cpu")

        assert engine.resolved_device == "cpu"
        assert engine.device == "cpu"


class TestMeloTTSEngineGenerateAudio:
    """N-2: generate_audio 정상 합성."""

    def _make_engine(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, cache_dir: str
    ) -> MeloTTSEngine:
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(model_dir=str(tmp_model_dir), device="cpu", cache_dir=cache_dir)
        return engine

    def test_generate_audio_creates_file(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """N-2: generate_audio가 파일 경로를 반환하고 tts_to_file 인자가 정확하다."""
        cache_dir = str(tmp_path / "cache")
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, cache_dir)

        text = "안녕하세요 새싹이입니다"

        def fake_tts_to_file(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 1024)

        mock_melo_tts.tts_to_file.side_effect = fake_tts_to_file

        result = engine.generate_audio(text, "greet")

        assert result.endswith("greet.wav")
        assert os.path.exists(result)
        call_args = mock_melo_tts.tts_to_file.call_args
        assert call_args[0][0] == text
        assert call_args[1]["speed"] == 1.0


class TestMeloTTSEngineAsync:
    """N-3: async_generate_audio 경로."""

    def _make_engine(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, cache_dir: str
    ) -> MeloTTSEngine:
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(model_dir=str(tmp_model_dir), device="cpu", cache_dir=cache_dir)
        return engine

    @pytest.mark.asyncio
    async def test_async_generate_audio(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """N-3: async_generate_audio가 동기 generate_audio를 호출하고 경로를 반환한다."""
        cache_dir = str(tmp_path / "cache")
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, cache_dir)

        def fake_tts(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 512)

        mock_melo_tts.tts_to_file.side_effect = fake_tts
        result = await engine.async_generate_audio("반갑습니다")
        assert result.endswith(".wav")
        assert os.path.exists(result)

    @pytest.mark.asyncio
    async def test_async_cancelled_error_propagates(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """N-3: asyncio.CancelledError가 상위로 전파된다."""
        cache_dir = str(tmp_path / "cache")
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, cache_dir)

        # 즉시 취소되는 태스크 생성 후 CancelledError 전파 확인
        async def _run() -> str:
            return await engine.async_generate_audio("취소 테스트")

        task = asyncio.create_task(_run())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# 엣지 케이스
# ---------------------------------------------------------------------------


class TestMeloTTSEngineEdge:
    """E-1 ~ E-3, E-6."""

    def _make_engine(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, cache_dir: str
    ) -> MeloTTSEngine:
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(model_dir=str(tmp_model_dir), device="cpu", cache_dir=cache_dir)
        return engine

    def test_empty_text_raises(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """E-1: 빈 문자열 입력 시 TTSRuntimeError("empty text")."""
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, str(tmp_path / "cache"))
        with pytest.raises(TTSRuntimeError, match="empty text"):
            engine.generate_audio("")
        mock_melo_tts.tts_to_file.assert_not_called()

    def test_text_truncated_at_1000(
        self,
        tmp_model_dir: Path,
        mock_melo_tts: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """E-2: 1000자 초과 텍스트는 절단 + WARNING 로그 1회."""
        cache_dir = str(tmp_path / "cache")
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, cache_dir)

        long_text = "가" * 1500

        def fake_tts(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 512)

        mock_melo_tts.tts_to_file.side_effect = fake_tts

        import logging

        with caplog.at_level(logging.WARNING, logger="tts.melo_tts_engine"):
            engine.generate_audio(long_text)

        call_text = mock_melo_tts.tts_to_file.call_args[0][0]
        assert len(call_text) == 1000
        assert any("truncating" in r.message for r in caplog.records)

    def test_device_auto_no_cuda_resolves_cpu(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock
    ) -> None:
        """E-3: device="auto" + CUDA 미가용 → resolved_device == "cpu", 예외 없음."""
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(
                model_dir=str(tmp_model_dir),
                device="auto",
            )
        assert engine.resolved_device == "cpu"

    def test_slash_in_file_name_no_ext(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """E-6: file_name_no_ext에 슬래시 포함 — cache_dir 내부에 저장됨 (탈출 금지)."""
        cache_dir = str(tmp_path / "cache")
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, cache_dir)

        def fake_tts(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 256)

        mock_melo_tts.tts_to_file.side_effect = fake_tts
        result = engine.generate_audio("테스트", "subdir/name")
        assert result.endswith("name.wav")
        assert os.path.exists(result)

    def test_cache_dir_used_in_output_path(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """M14: generate_audio 출력 경로가 self.cache_dir 하위임을 검증 (upstream 하드코딩 회귀 방지)."""
        custom_cache = tmp_path / "my_custom_cache"
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, str(custom_cache))

        def fake_tts(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 256)

        mock_melo_tts.tts_to_file.side_effect = fake_tts
        result = engine.generate_audio("테스트", "hello")
        assert Path(result).resolve().is_relative_to(custom_cache.resolve())

    def test_dotdot_in_file_name_no_ext_stays_in_cache(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """M13: file_name_no_ext에 '..' 포함 — cache_dir 탈출 금지."""
        cache_dir = tmp_path / "cache"
        engine = self._make_engine(tmp_model_dir, mock_melo_tts, str(cache_dir))

        def fake_tts(t: str, spk_id: int, output_path: str, **kwargs: object) -> None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"\x00" * 256)

        mock_melo_tts.tts_to_file.side_effect = fake_tts
        result = engine.generate_audio("테스트", "../../evil")
        # basename만 남으면 "evil" 이어야 함
        assert Path(result).resolve().is_relative_to(cache_dir.resolve())


# ---------------------------------------------------------------------------
# 적대적 케이스
# ---------------------------------------------------------------------------


class TestMeloTTSEngineAdversarial:
    """A-1, A-2, A-8."""

    def test_model_dir_not_found(self, mock_melo_tts: MagicMock) -> None:
        """A-1: model_dir 부재 → 경고 후 자동 다운로드 경로로 폴백, 초기화 성공.

        이전 버전은 TTSInitError를 발생시켰으나, HF Hub 캐시 자동 로딩을 지원하도록
        변경되어 model_dir 부재 시 경고만 발행하고 계속 진행한다.
        """
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch.dict(sys.modules, {"melo": mock_melo_module, "melo.api": mock_melo_module}),
            patch("builtins.__import__", side_effect=_import_with_mock(mock_melo_tts)),
        ):
            engine = MeloTTSEngine(model_dir="/no/such/dir")
        assert engine.model_dir == ""  # 자동 다운로드 경로 (비어있음)

    def test_cuda_forced_but_unavailable(self, tmp_model_dir: Path) -> None:
        """A-2: device="cuda" + CUDA 미가용 → TTSInitError, auto 폴백 없음."""
        with (
            patch("tts._device._check_cuda_available", return_value=False),
        ):
            with pytest.raises(TTSInitError, match="cuda requested but not available"):
                MeloTTSEngine(model_dir=str(tmp_model_dir), device="cuda")

    def test_backend_raises_runtime_error(
        self, tmp_model_dir: Path, mock_melo_tts: MagicMock, tmp_path: Path
    ) -> None:
        """A-8: tts_to_file가 RuntimeError → TTSRuntimeError, __cause__ 원본."""
        cache_dir = str(tmp_path / "cache")
        mock_melo_module = MagicMock()
        mock_melo_module.TTS = MagicMock(return_value=mock_melo_tts)
        with (
            patch("tts._device._check_cuda_available", return_value=False),
            patch("tts.melo_tts_engine._set_offline_env_vars"),
            patch.dict(sys.modules, {"melo": MagicMock(), "melo.api": mock_melo_module}),
        ):
            engine = MeloTTSEngine(model_dir=str(tmp_model_dir), device="cpu", cache_dir=cache_dir)

        original_exc = RuntimeError("cuda OOM")
        mock_melo_tts.tts_to_file.side_effect = original_exc

        with pytest.raises(TTSRuntimeError, match="cuda OOM") as exc_info:
            engine.generate_audio("테스트")

        assert exc_info.value.__cause__ is original_exc

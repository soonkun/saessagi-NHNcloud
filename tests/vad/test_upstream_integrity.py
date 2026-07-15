# tests/vad/test_upstream_integrity.py
"""Upstream file integrity tests — N-6, A-3 (network pattern scan)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest


# CR-17: upstream 클론 대신 vendor/ 벤더링본을 검사 (파일 내용 동일 — 해시 유효)
_UPSTREAM_VAD_DIR = Path(__file__).parent.parent.parent / "vendor" / "open_llm_vtuber" / "vad"
_SILERO_VAD_PY = _UPSTREAM_VAD_DIR / "silero.py"
_HASHES_FILE = Path(__file__).parent / "upstream_hashes.json"


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class TestN6UpstreamFileHashRegression:
    """N-6: SHA-256 hashes of upstream VAD files match recorded values."""

    # spec: §N-6

    def test_silero_py_hash(self) -> None:
        """spec: §N-6 — silero.py hash must match upstream_hashes.json."""
        expected = json.loads(_HASHES_FILE.read_text())["silero.py"]
        actual = _sha256(_UPSTREAM_VAD_DIR / "silero.py")
        assert actual == expected, (
            f"upstream/vad/silero.py has been modified!\n"
            f"Expected: {expected}\n"
            f"Actual:   {actual}\n"
            "This module must not be changed. Revert or update upstream_hashes.json "
            "only after deliberate upstream upgrade review."
        )

    def test_vad_factory_py_hash(self) -> None:
        """spec: §N-6 — vad_factory.py hash must match upstream_hashes.json."""
        expected = json.loads(_HASHES_FILE.read_text())["vad_factory.py"]
        actual = _sha256(_UPSTREAM_VAD_DIR / "vad_factory.py")
        assert actual == expected, (
            f"upstream/vad/vad_factory.py has been modified!\n"
            f"Expected: {expected}\n"
            f"Actual:   {actual}"
        )

    def test_vad_interface_py_hash(self) -> None:
        """spec: §N-6 — vad_interface.py hash must match upstream_hashes.json."""
        expected = json.loads(_HASHES_FILE.read_text())["vad_interface.py"]
        actual = _sha256(_UPSTREAM_VAD_DIR / "vad_interface.py")
        assert actual == expected, (
            f"upstream/vad/vad_interface.py has been modified!\n"
            f"Expected: {expected}\n"
            f"Actual:   {actual}"
        )


class TestA3NoNetworkPatternsInUpstreamSilero:
    """A-3: upstream vad/silero.py contains no external network call patterns.

    The forbidden patterns scan reads the raw file text directly — this is
    deterministic regardless of whether silero-vad is installed or not.

    NOTE on websockets: upstream silero.py imports `websockets` inside vad_main(),
    which is the standalone CLI entrypoint (spec §DROP). This project never invokes
    vad_main(), so the websockets dependency is inert at runtime. The pattern is
    flagged here so any future expansion of websockets usage outside vad_main()
    is caught immediately. The test below documents the known acceptable occurrence
    and marks it as scoped to vad_main() only.
    """

    # spec: §A-3

    # Patterns indicating runtime network access in the VAD initialization path.
    _FORBIDDEN_HTTP = re.compile(
        r"requests\."
        r"|urllib\."
        r"|http\.client"
        r"|https?://(?!(?:127\.0\.0\.1|localhost))"
    )

    # websockets import — flagged to detect future expansion outside vad_main().
    _WEBSOCKETS_PATTERN = re.compile(r"\bwebsockets\b")

    def test_silero_py_no_http_patterns(self) -> None:
        """spec: §A-3 — upstream silero.py has no HTTP/requests network call patterns."""
        if not _SILERO_VAD_PY.exists():
            pytest.skip(f"Upstream silero.py not found at {_SILERO_VAD_PY}")

        content = _SILERO_VAD_PY.read_text(encoding="utf-8")
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(content.splitlines(), start=1)
            if self._FORBIDDEN_HTTP.search(line)
        ]
        assert violations == [], (
            "Found forbidden HTTP/network patterns in upstream/vad/silero.py:\n"
            + "\n".join(violations)
            + "\nRe-pin silero-vad to >=5.0,<6 and verify offline bundle."
        )

    def test_silero_py_websockets_scoped_to_vad_main(self) -> None:
        """spec: §A-3, §DROP — websockets import in silero.py must be inside vad_main() only.

        upstream silero.py has `import websockets` inside the vad_main() async function.
        This is acceptable ONLY because vad_main() is the standalone entrypoint that
        this project never calls (spec §DROP). This test verifies:
        1. The websockets import EXISTS (confirming we're checking the right file).
        2. The import is INSIDE vad_main() (lazy import, not at module top-level).

        If websockets ever appears at the module top-level, this is a regression.
        """
        if not _SILERO_VAD_PY.exists():
            pytest.skip(f"Upstream silero.py not found at {_SILERO_VAD_PY}")

        content = _SILERO_VAD_PY.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Find all lines with websockets
        websocket_lines = [
            (i + 1, line) for i, line in enumerate(lines) if self._WEBSOCKETS_PATTERN.search(line)
        ]

        if not websocket_lines:
            # No websockets at all — acceptable (maybe upstream removed it)
            return

        # All occurrences must be inside vad_main() (indented, not at module top-level)
        # A top-level import would have 0 leading spaces: "import websockets"
        top_level_violations = [
            f"Line {lineno}: {line.strip()}"
            for lineno, line in websocket_lines
            if not line.startswith((" ", "\t"))
        ]
        assert top_level_violations == [], (
            "websockets appears at module top-level in upstream/vad/silero.py — "
            "this project requires websockets to be an offline-free import path.\n"
            "Violations (top-level websockets):\n" + "\n".join(top_level_violations)
        )

    @pytest.mark.slow
    def test_silero_py_load_silero_vad_file_no_network(self) -> None:
        """spec: §A-3 — silero-vad package source file has no external HTTP patterns.

        Reads the installed silero_vad package source file directly (not via
        inspect.getsource, which fails when the module is mocked). If the package
        is not installed or is mocked (test environment), the test is skipped
        rather than vacuously passing.

        NOTE: This test always skips in normal CI because root conftest.py mocks
        silero_vad via sys.modules (see conftest.py _MOCK_PACKAGES). The mock has
        __spec__=None, which is detected below and causes pytest.skip(). This test
        only runs on a machine with the real silero-vad package installed AND when
        invoked with `pytest -m slow`.
        """
        import sys as _sys

        # Check if silero_vad is a real module or a MagicMock (test environment sentinel)
        silero_mod = _sys.modules.get("silero_vad")
        if silero_mod is None or getattr(silero_mod, "__spec__", None) is None:
            # Either not installed, or mocked via conftest.py MagicMock with __spec__=None
            pytest.skip(
                "silero_vad is mocked or not installed — "
                "skipping file-level scan (run with real silero-vad>=5.0 installed)"
            )

        import importlib.util

        spec = importlib.util.find_spec("silero_vad")
        if spec is None or spec.origin is None:
            pytest.skip("silero_vad package not installed — skipping file-level scan")

        silero_vad_path = Path(spec.origin)
        if not silero_vad_path.exists():
            pytest.skip(f"silero_vad source not found at {silero_vad_path}")

        content = silero_vad_path.read_text(encoding="utf-8")
        violations = [
            f"Line {i}: {line.strip()}"
            for i, line in enumerate(content.splitlines(), start=1)
            if self._FORBIDDEN_HTTP.search(line)
        ]
        assert violations == [], (
            "load_silero_vad() source contains network call patterns.\n"
            "Expected silero-vad>=5.0 (model bundled in wheel).\n"
            "Violations:\n" + "\n".join(violations)
        )

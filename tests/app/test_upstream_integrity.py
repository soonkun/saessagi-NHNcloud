# tests/app/test_upstream_integrity.py
"""upstream 무결성 검증 테스트 — 스펙 DoD.

.gitignore에 upstream/이 제외돼 git diff는 항상 빈 결과이므로,
SHA-256 해시 기반 매니페스트로 무결성을 검증한다.

기준 해시: tests/app/upstream_baseline.json.
baseline은 **정식 관리되는 patches/ 적용 후 상태**를 기준으로 한다 — 즉
patches/*.patch로 관리되는 소수의 의도적 수정(예: conversations TTS 안정화)은
baseline에 반영돼 있고, 그 외 **관리되지 않는 추가 변조**가 생기면 본 테스트가
잡아낸다. 새 패치를 추가하면 patches/에 .patch + README 항목을 넣고
baseline을 재생성해야 한다. (patches/README.md 참조)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_UPSTREAM_DIR = Path(__file__).parent.parent.parent / "upstream" / "Open-LLM-VTuber"
_BASELINE_FILE = Path(__file__).parent / "upstream_baseline.json"


def _compute_hashes(upstream_dir: Path) -> dict[str, str]:
    """upstream_dir 내 .py 파일의 SHA-256 해시를 계산해 반환.

    경로 구분자는 OS 무관하게 '/'로 정규화한다.
    """
    result: dict[str, str] = {}
    for f in sorted(upstream_dir.rglob("*.py")):
        # Windows / Linux 모두 '/'로 정규화
        rel = f.relative_to(upstream_dir).as_posix()
        result[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return result


def test_upstream_files_not_modified() -> None:
    """upstream/Open-LLM-VTuber/ 내 .py 파일들이 기준 해시와 일치하는지 검증.

    upstream 경로가 존재하지 않으면 skip.
    기준 해시 파일(upstream_baseline.json)이 없으면 skip (최초 빌드 환경).
    """
    if not _UPSTREAM_DIR.exists():
        pytest.skip("upstream not present — 무결성 검사 건너뜀")

    if not _BASELINE_FILE.exists():
        pytest.skip(
            f"기준 해시 파일 없음: {_BASELINE_FILE} — "
            "tests/app/upstream_baseline.json을 먼저 생성하세요"
        )

    baseline: dict[str, str] = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    current: dict[str, str] = _compute_hashes(_UPSTREAM_DIR)

    # 수정된 파일 탐지
    modified: list[str] = []
    for path, expected_hash in baseline.items():
        actual = current.get(path)
        if actual is None:
            modified.append(f"삭제됨: {path}")
        elif actual != expected_hash:
            modified.append(f"수정됨: {path}")

    # 새로 추가된 파일 탐지 (baseline에 없는 파일)
    added: list[str] = [p for p in current if p not in baseline]
    for path in added:
        modified.append(f"추가됨: {path}")

    assert not modified, "upstream 파일이 변경됨 (CLAUDE.md §'절대 금지' 위반):\n" + "\n".join(
        f"  - {m}" for m in modified
    )

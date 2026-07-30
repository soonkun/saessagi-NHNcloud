"""M_22 감시 결정 타입 (CR-41).

스캔 로직을 파일시스템·벡터스토어와 분리하기 위해, "무엇을 할지"를 먼저 순수 계산으로
정하고(ScanPlan) 실행은 서비스가 담당한다. 그래야 판단 규칙을 단위 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class WatchDecision(str, Enum):
    """파일 하나에 대한 판단."""

    INGEST = "ingest"
    """신규 — 인제스트한다."""

    SKIP_UNCHANGED = "skip_unchanged"
    """이미 인제스트된 동일 내용 — 아무것도 안 한다."""

    MOVE_FOLDER = "move_folder"
    """내용은 같고 폴더만 바뀜 — 재임베딩 없이 category만 옮긴다."""

    WAIT_UNSTABLE = "wait_unstable"
    """크기·mtime이 직전 스캔과 달라 전송 중으로 보임 — 다음 주기까지 기다린다."""

    SKIP_EXTENSION = "skip_extension"
    """지원하지 않는 확장자."""

    DEFERRED = "deferred"
    """max_per_cycle 초과 — 다음 주기로."""


@dataclass(frozen=True)
class FileCandidate:
    """스캔에서 발견한 파일 하나."""

    path: Path
    """절대 경로."""

    rel_path: str
    """감시 루트 기준 상대 경로 (로그·상태 표시용)."""

    folder_name: str | None
    """소속 앱 폴더 이름. 루트 직하 파일은 None."""

    size: int
    mtime: float


@dataclass
class ScanPlan:
    """한 주기에 무엇을 할지."""

    to_ingest: list[FileCandidate] = field(default_factory=list)
    to_move: list[tuple[FileCandidate, str]] = field(default_factory=list)
    """(파일, doc_id) — 폴더만 이동."""

    unstable: list[FileCandidate] = field(default_factory=list)
    deferred: list[FileCandidate] = field(default_factory=list)
    skipped: int = 0

    folders_to_create_in_app: list[str] = field(default_factory=list)
    folders_to_create_on_disk: list[str] = field(default_factory=list)

    missing_digests: list[str] = field(default_factory=list)
    """상태에는 있는데 디스크에서 사라진 파일의 해시 — delete_policy가 처리한다."""

    def is_empty(self) -> bool:
        return not (
            self.to_ingest
            or self.to_move
            or self.folders_to_create_in_app
            or self.folders_to_create_on_disk
            or self.missing_digests
        )

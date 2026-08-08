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

    QUARANTINED = "quarantined"
    """연속 실패가 한도를 넘음 — 내용이 바뀌기 전까지 시도하지 않는다 (E-91)."""


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

    quarantined: list[FileCandidate] = field(default_factory=list)
    """연속 실패 한도를 넘어 이번 주기에 시도하지 않은 파일 (E-91).

    `to_ingest` 정원을 잡아먹지 않는다 — 이게 핵심이다. 실패 파일이 정원을 채워 정상 파일이
    영원히 밀리는 것이 원래 사고였다. 계획이 비었는지 판단할 때는 세지 않는다(할 일이 아니라
    "하지 않기로 한 것"이므로, 매 주기 저장·로그를 유발하면 안 된다).
    """

    folders_to_create_in_app: list[str] = field(default_factory=list)
    folders_to_create_on_disk: list[str] = field(default_factory=list)

    # CR-46: 폴더 삭제 전파. known 상태가 있어야 "신규"와 "반대편에서 삭제됨"을 구분할 수 있다.
    folders_deleted_on_disk: list[str] = field(default_factory=list)
    """디스크 디렉토리가 사라진 폴더 — 앱에서도 지울 후보 (문서까지 삭제되므로 정책 확인 필요)."""

    folders_deleted_in_app: list[str] = field(default_factory=list)
    """UI에서 지워진 폴더 — 디스크 디렉토리를 지울 후보 (비어 있을 때만)."""

    folders_confirmed: list[str] = field(default_factory=list)
    """이번 스캔에서 디스크·앱 양쪽에 모두 존재하는 것이 확인된 폴더.

    known 집합의 **유일한 학습 경로**다. 이게 없으면 "이미 양쪽에 있는 폴더"는 어떤 분기에도
    걸리지 않아 known에 등록되지 못하고, 그 상태에서 한쪽을 지우면 신규 폴더로 오인되어
    반대편이 되살린다 (E-68). 계획이 비었는지 판단할 때는 세지 않는다 — 관측 결과일 뿐이다.
    """

    missing_digests: list[str] = field(default_factory=list)
    """상태에는 있는데 디스크에서 사라진 파일의 해시 — delete_policy가 처리한다."""

    grace_digests: list[str] = field(default_factory=list)
    """사라진 것으로 보이나 유예 중인 해시 (이동일 수 있어 이번엔 삭제하지 않는다)."""

    def is_empty(self) -> bool:
        return not (
            self.to_ingest
            or self.to_move
            or self.folders_to_create_in_app
            or self.folders_to_create_on_disk
            or self.folders_deleted_on_disk
            or self.folders_deleted_in_app
            or self.missing_digests
        )

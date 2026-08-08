"""M_22 스캐너 (CR-41) — 디스크를 훑어 "무엇을 할지"만 결정한다.

임베딩·저장은 하지 않는다. 판단 규칙을 파일시스템만 있는 상태에서 단위 테스트할 수 있게
분리한 것이다.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .state import WatchState, file_digest
from .types import FileCandidate, ScanPlan

# 앱의 업로드 라우트와 동일하게 유지할 것 — 여기만 넓히면 파싱 실패로 이어진다.
ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".pdf", ".docx", ".pptx", ".hwpx"})

# 편집기·SFTP 클라이언트가 남기는 임시 파일. 인제스트하면 쓰레기가 색인된다.
_IGNORED_PREFIXES = (".", "~$")
_IGNORED_SUFFIXES = (".tmp", ".part", ".crdownload", ".filepart", ".swp")


def sanitize_folder_name(raw: str) -> str | None:
    """디렉토리 이름을 앱 폴더 이름으로 정규화. 부적절하면 None.

    경로 탈출(`..`)과 구분자를 막는다 — 감시 루트는 사용자가 파일을 던져 넣는 곳이므로
    디렉토리 이름을 그대로 신뢰하지 않는다.
    """
    name = raw.strip().strip("/\\").replace("\x00", "")
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name:
        return None
    return name


def _is_ignored(path: Path) -> bool:
    n = path.name
    if n.startswith(_IGNORED_PREFIXES):
        return True
    return n.lower().endswith(_IGNORED_SUFFIXES)


def collect_candidates(root: Path) -> tuple[list[FileCandidate], set[str]]:
    """감시 루트를 훑어 후보 파일과 서브디렉토리 이름 집합을 돌려준다.

    앱 폴더 모델이 1단이므로, 2단 이상 깊이의 파일은 최상위 서브디렉토리 폴더로 평탄화한다.
    """
    candidates: list[FileCandidate] = []
    folder_names: set[str] = set()

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            folder = sanitize_folder_name(entry.name)
            if folder is None:
                logger.warning(f"rag_watch: 폴더명 사용 불가, 건너뜀: {entry.name!r}")
                continue
            folder_names.add(folder)
            for sub in sorted(entry.rglob("*")):
                if sub.is_file() and not _is_ignored(sub):
                    candidates.append(_make_candidate(sub, root, folder))
        elif entry.is_file() and not _is_ignored(entry):
            # 루트 직하 파일 → 폴더 없음
            candidates.append(_make_candidate(entry, root, None))

    return candidates, folder_names


def _make_candidate(path: Path, root: Path, folder: str | None) -> FileCandidate:
    st = path.stat()
    return FileCandidate(
        path=path,
        rel_path=str(path.relative_to(root)),
        folder_name=folder,
        size=st.st_size,
        mtime=st.st_mtime,
    )


def build_plan(
    root: Path,
    state: WatchState,
    *,
    app_folder_names: set[str],
    max_per_cycle: int,
    delete_grace_cycles: int = 2,
    max_ingest_failures: int = 3,
) -> ScanPlan:
    """한 주기 실행 계획을 세운다."""
    plan = ScanPlan()
    candidates, disk_folders = collect_candidates(root)

    # ── 폴더 양방향 맞춤 (CR-46) ────────────────────────────────────────────
    #
    # 이전 구현은 "한쪽에 있고 반대쪽에 없으면 만든다"였다. 그런데 그 조건은
    #   (a) 방금 새로 생긴 폴더
    #   (b) 반대쪽에서 사용자가 지운 폴더
    # 두 경우에 똑같이 성립한다. 그래서 어느 쪽을 지워도 다음 스캔이 반대쪽을 근거로
    # 되살렸고, UI에서 지우면 디스크가, 디스크에서 지우면 UI가 부활시키는 핑퐁이 됐다 (E-68).
    #
    # known(= 직전에 양쪽 모두에 있다고 확인된 집합)을 기억하면 둘을 구분할 수 있다.
    known_folders = state.known_folders()

    plan.folders_to_create_in_app = sorted(disk_folders - app_folder_names - known_folders)
    plan.folders_to_create_on_disk = sorted(app_folder_names - disk_folders - known_folders)
    # known이었는데 한쪽에서 사라졌다 → 사용자가 그쪽에서 지운 것
    plan.folders_deleted_on_disk = sorted((known_folders & app_folder_names) - disk_folders)
    plan.folders_deleted_in_app = sorted((known_folders & disk_folders) - app_folder_names)
    # 양쪽에 다 있으면 known으로 학습한다. 이 줄이 없으면 known이 한 번 비거나 어긋난 뒤
    # 스스로 회복하지 못하고, 다음 삭제가 다시 부활로 나타난다.
    plan.folders_confirmed = sorted(disk_folders & app_folder_names)

    seen_rel: set[str] = set()
    live_digests: set[str] = set()

    for cand in candidates:
        seen_rel.add(cand.rel_path)

        if cand.path.suffix.lower() not in ALLOWED_SUFFIXES:
            plan.skipped += 1
            continue

        # 전송 중 파일 걸러내기 — 해시 계산 전에 확인해 큰 파일을 헛되게 읽지 않는다.
        if not state.is_stable(cand.rel_path, cand.size, cand.mtime):
            plan.unstable.append(cand)
            continue

        try:
            digest = file_digest(cand.path)
        except OSError as exc:
            logger.warning(f"rag_watch: 읽기 실패, 건너뜀 {cand.rel_path}: {exc}")
            continue

        live_digests.add(digest)
        entry = state.get(digest)

        if entry is None:
            # E-91: 같은 내용으로 계속 실패한 파일은 정원을 쓰지 않는다.
            # 실패는 상태의 files에 남지 않으므로 매 주기 "신규"로 되돌아온다. 정렬 순서상
            # 앞쪽에 있으면 max_per_cycle을 통째로 차지해 뒤 파일이 영원히 밀린다.
            if max_ingest_failures > 0 and state.failure_count(digest) >= max_ingest_failures:
                plan.quarantined.append(cand)
                continue
            if len(plan.to_ingest) >= max_per_cycle:
                plan.deferred.append(cand)
            else:
                plan.to_ingest.append(cand)
            continue

        if entry.get("folder_name") != cand.folder_name:
            plan.to_move.append((cand, str(entry.get("doc_id") or "")))
        else:
            plan.skipped += 1

    # 상태에는 있는데 디스크에 없는 것.
    #
    # live_digests만으로 판단하면 안 된다. 안정화 대기(첫 주기·전송 중) 파일은 해시를
    # 계산하지 않으므로 live_digests에 없고, 그러면 **재시작 직후 모든 문서가 삭제 대상으로
    # 잡힌다**. delete_policy=unindex였다면 색인 전체가 날아간다.
    # 그래서 기록된 경로가 실제로 남아 있는지 stat으로 함께 확인한다.
    #
    # CR-46: 여기에 유예를 하나 더 둔다. 파일을 다른 폴더로 옮기면
    #   · 기록된 옛 경로는 이미 없고
    #   · 새 경로는 첫 관측이라 "안정화 대기"로 해시를 구하지 않아 live_digests에도 없다
    # → 그 순간 "삭제됨"으로 오판해 색인을 지웠고, 다음 주기에 새 파일로 재임베딩됐다.
    # 실제로 270건 중 44건이 이렇게 삭제·재임베딩됐다 (E-69).
    # 연속 delete_grace_cycles 회 이상 사라진 상태가 유지될 때만 삭제로 확정한다.
    vanished = sorted(
        d for d in state.known_digests() - live_digests if not _recorded_path_exists(root, state, d)
    )
    for digest in live_digests:
        state.clear_miss(digest)  # 다시 보였으면 유예 카운터 리셋
    for digest in vanished:
        if state.bump_miss(digest) >= delete_grace_cycles:
            plan.missing_digests.append(digest)
        else:
            plan.grace_digests.append(digest)
    state.clear_all_misses(set(vanished))

    state.drop_seen(seen_rel)

    # E-91: 사라진 파일의 실패 기록 정리.
    #
    # live_digests로 판단하면 안 된다 — 재시작 직후에는 모든 파일이 "안정화 대기"라 해시를
    # 계산하지 않아 live_digests가 비고, 그러면 **매 재시작마다 실패 기록이 전부 지워져**
    # 문제 파일이 다시 정원을 차지한다. 기록해 둔 경로가 실제로 남아 있는지로 판단한다.
    still_present = {
        digest
        for digest, entry in state.failures().items()
        if (rel := str(entry.get("path") or "")) and (root / rel).exists()
    }
    state.drop_failures(still_present)

    return plan


def _recorded_path_exists(root: Path, state: WatchState, digest: str) -> bool:
    entry = state.get(digest)
    rel = str((entry or {}).get("path") or "")
    if not rel:
        return False
    return (root / rel).exists()

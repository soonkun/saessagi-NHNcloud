"""M_22 감시 상태 파일 (CR-41).

키를 **내용 해시**로 잡는다. 경로를 키로 쓰면 파일을 다른 폴더로 옮기거나 이름을 바꿨을 때
같은 문서를 다시 임베딩한다(수백 건이면 치명적). 해시로 잡으면 이동은 category 갱신으로
끝난다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_READ_CHUNK = 1024 * 1024


def file_digest(path: Path) -> str:
    """파일 내용의 sha256. 큰 PDF도 있으므로 스트리밍으로 읽는다."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_READ_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class WatchState:
    """인제스트 이력. 원자적으로 저장해 중간에 죽어도 파일이 깨지지 않게 한다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._files: dict[str, dict[str, Any]] = {}
        # CR-46: 양쪽(디스크·앱)에 모두 존재한다고 확인된 폴더 이름.
        # 이게 없으면 "새로 생긴 폴더"와 "반대편에서 지워진 폴더"를 구분할 수 없어
        # 어느 쪽을 지워도 반대편이 30초 뒤 되살리는 핑퐁이 발생한다 (E-68).
        self._folders: set[str] = set()
        # 직전 스캔의 (크기, mtime) — 전송 중 파일을 걸러내는 안정화 확인용.
        # 디스크에 저장하지 않는다(재시작하면 한 주기 더 기다리면 되므로).
        self._seen: dict[str, tuple[int, float]] = {}
        # CR-46: digest → 연속으로 "사라짐"으로 관측된 횟수. 파일을 다른 폴더로 옮기면
        # 새 경로가 안정화되기까지 한 주기가 걸리는데, 그 사이 옛 경로가 이미 없어서
        # 삭제로 오판된다 — 유예 주기를 두어 이동이 먼저 해소되게 한다 (E-69).
        self._miss_counts: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._files = dict(raw.get("files") or {})
            self._folders = set(raw.get("folders") or [])
            logger.info(
                f"rag_watch: 상태 로드 파일 {len(self._files)}건 / "
                f"폴더 {len(self._folders)}개 ({self._path})"
            )
        except Exception as exc:
            # 상태가 깨졌다고 기능을 멈추지 않는다 — 최악의 경우 재인제스트일 뿐이다.
            logger.warning(f"rag_watch: 상태 파일 손상, 새로 시작합니다: {exc!r}")
            self._files = {}
            self._folders = set()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"files": self._files, "folders": sorted(self._folders)},
            ensure_ascii=False,
            indent=1,
        )
        # 같은 디렉토리에 임시 파일 → rename (같은 파일시스템이어야 원자적)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ── 조회 ────────────────────────────────────────────────────────────────

    def get(self, digest: str) -> dict[str, Any] | None:
        return self._files.get(digest)

    def known_digests(self) -> set[str]:
        return set(self._files)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """digest → 기록 사본 (순회 중 수정해도 안전하도록 복사해 준다)."""
        return dict(self._files)

    def __len__(self) -> int:
        return len(self._files)

    # ── 기록 ────────────────────────────────────────────────────────────────

    def record(
        self,
        digest: str,
        *,
        rel_path: str,
        doc_id: str,
        folder_name: str | None,
        size: int,
    ) -> None:
        self._files[digest] = {
            "path": rel_path,
            "doc_id": doc_id,
            "folder_name": folder_name,
            "size": size,
            "ingested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def update_location(self, digest: str, *, rel_path: str, folder_name: str | None) -> None:
        entry = self._files.get(digest)
        if entry is not None:
            entry["path"] = rel_path
            entry["folder_name"] = folder_name

    def forget(self, digest: str) -> None:
        self._files.pop(digest, None)

    # ── 안정화 확인 ─────────────────────────────────────────────────────────

    def is_stable(self, rel_path: str, size: int, mtime: float) -> bool:
        """직전 스캔과 (크기, mtime)이 같으면 전송이 끝난 것으로 본다.

        SFTP로 큰 파일을 올리는 중에 인제스트하면 잘린 문서가 색인된다. 크기만 보면
        같은 크기로 잠깐 멈춘 순간에 걸리므로 mtime도 함께 본다.
        """
        prev = self._seen.get(rel_path)
        self._seen[rel_path] = (size, mtime)
        return prev == (size, mtime)

    def drop_seen(self, rel_paths: set[str]) -> None:
        """사라진 파일의 안정화 기록 정리 (메모리 누수 방지)."""
        for p in list(self._seen):
            if p not in rel_paths:
                del self._seen[p]

    # ── 폴더 동기화 상태 (CR-46) ────────────────────────────────────────────

    def known_folders(self) -> set[str]:
        """양쪽에 모두 있다고 확인된 폴더 이름 집합."""
        return set(self._folders)

    def set_known_folders(self, names: set[str]) -> None:
        self._folders = set(names)

    # ── 삭제 유예 (CR-46) ───────────────────────────────────────────────────

    def bump_miss(self, digest: str) -> int:
        """이 digest가 연속 몇 번째로 '사라짐'으로 보이는지 세어 반환한다."""
        n = self._miss_counts.get(digest, 0) + 1
        self._miss_counts[digest] = n
        return n

    def clear_miss(self, digest: str) -> None:
        """다시 발견되면 카운터를 리셋한다 (이동이 해소된 경우)."""
        self._miss_counts.pop(digest, None)

    def clear_all_misses(self, keep: set[str]) -> None:
        """살아있는 digest들의 카운터를 정리한다."""
        for d in list(self._miss_counts):
            if d not in keep:
                del self._miss_counts[d]

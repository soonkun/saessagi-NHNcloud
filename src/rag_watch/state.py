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
        # 직전 스캔의 (크기, mtime) — 전송 중 파일을 걸러내는 안정화 확인용.
        # 디스크에 저장하지 않는다(재시작하면 한 주기 더 기다리면 되므로).
        self._seen: dict[str, tuple[int, float]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._files = dict(raw.get("files") or {})
            logger.info(f"rag_watch: 상태 로드 {len(self._files)}건 ({self._path})")
        except Exception as exc:
            # 상태가 깨졌다고 기능을 멈추지 않는다 — 최악의 경우 재인제스트일 뿐이다.
            logger.warning(f"rag_watch: 상태 파일 손상, 새로 시작합니다: {exc!r}")
            self._files = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"files": self._files}, ensure_ascii=False, indent=1)
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

"""M_22 RagWatchService (CR-41) — 계획을 실제로 실행한다.

앱 폴더 조작·인제스트는 `rag_routes`의 함수를 재사용한다. 폴더 목록 저장 형식이나
청킹 파라미터를 두 벌 유지하면 UI와 자동 인제스트의 결과가 갈라진다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from .scanner import build_plan, sanitize_folder_name
from .state import WatchState, file_digest
from .types import ScanPlan


class RagWatchService:
    """주기적으로 감시 루트를 스캔해 새 파일을 인제스트한다.

    한 번에 하나의 스캔만 돌게 잠금을 둔다 — 인제스트가 주기보다 오래 걸리면
    스캔이 겹쳐 같은 파일을 두 번 인제스트한다.
    """

    def __init__(
        self,
        *,
        root: Path,
        state_path: Path,
        service_context: Any,
        max_per_cycle: int = 20,
        delete_policy: str = "ignore",
        unindex_guard_ratio: float = 0.25,
        unindex_guard_min: int = 5,
    ) -> None:
        self._root = root
        self._ctx = service_context
        self._max = max_per_cycle
        self._delete_policy = delete_policy
        self._guard_ratio = unindex_guard_ratio
        self._guard_min = unindex_guard_min
        self._state = WatchState(state_path)
        self._lock = asyncio.Lock()
        self._cycles = 0

    @property
    def state(self) -> WatchState:
        return self._state

    # ── 최초 시딩 ───────────────────────────────────────────────────────────

    async def seed_from_existing(self) -> int:
        """이미 인제스트된 문서를 상태에 등록한다 (첫 스캔 전에 1회).

        이게 없으면 감시를 처음 켤 때 **이미 색인된 파일을 전부 다시 임베딩한다.**
        270건이면 색인이 두 배로 불고 검색 결과에 중복이 뜬다.

        매칭 기준은 (파일명, 폴더명). 사람이 스크립트를 돌리는 대신 앱이 자동으로 하므로
        "감시를 켰는데 시딩을 잊는" 사고가 생기지 않는다.

        이미 시딩된 상태(기록이 있음)면 아무것도 하지 않는다 — 재시작마다 반복할 필요가 없다.
        """
        if len(self._state) > 0:
            return 0
        if not self._root.is_dir():
            return 0

        try:
            seeded = await asyncio.to_thread(self._seed_sync)
        except Exception as exc:
            # 시딩 실패는 기능을 막지 않는다. 다만 중복 인제스트 위험을 크게 알린다.
            logger.error(
                f"rag_watch: 최초 시딩 실패 — 이미 색인된 파일이 재임베딩될 수 있습니다: {exc!r}"
            )
            return 0

        if seeded:
            await asyncio.to_thread(self._state.save)
            logger.info(
                f"rag_watch: 최초 시딩 {seeded}건 — 이미 색인된 파일은 재임베딩하지 않습니다"
            )
        return seeded

    def _seed_sync(self) -> int:
        # 문서 목록은 라우트가 쓰는 헬퍼를 그대로 쓴다 — 스토어에 list_documents가 없고,
        # 청크에서 문서 단위로 접는 로직이 이 헬퍼에만 있다.
        from app.rag_routes import _list_documents_from_store, _load_folders

        from .scanner import collect_candidates

        rag = getattr(self._ctx, "rag_service", None)
        store = getattr(rag, "store", None) or getattr(rag, "_store", None)
        if store is None:
            raise RuntimeError("vector store unavailable")

        # folder_id → 폴더 이름
        id_to_name = {str(f["folder_id"]): str(f["name"]) for f in _load_folders()}

        # (파일명, 폴더명) → doc_id
        index: dict[tuple[str, str | None], str] = {}
        for info in _list_documents_from_store(store):
            fid = getattr(info, "folder_id", None)
            index[(info.filename, id_to_name.get(str(fid)) if fid else None)] = info.doc_id

        candidates, _ = collect_candidates(self._root)
        seeded = 0
        for cand in candidates:
            doc_id = index.get((cand.path.name, cand.folder_name))
            if doc_id is None:
                continue
            digest = file_digest(cand.path)
            if self._state.get(digest) is not None:
                continue
            self._state.record(
                digest,
                rel_path=cand.rel_path,
                doc_id=doc_id,
                folder_name=cand.folder_name,
                size=cand.size,
            )
            seeded += 1
        return seeded

    # ── 한 주기 ─────────────────────────────────────────────────────────────

    async def run_once(self) -> ScanPlan:
        """스캔 1회. 겹치면 즉시 빈 계획을 돌려준다."""
        if self._lock.locked():
            logger.debug("rag_watch: 이전 스캔이 진행 중 — 이번 주기 건너뜀")
            return ScanPlan()

        async with self._lock:
            if not self._root.is_dir():
                logger.warning(f"rag_watch: 감시 루트가 없습니다: {self._root}")
                return ScanPlan()

            app_folders = await asyncio.to_thread(self._app_folder_names)
            plan = await asyncio.to_thread(
                build_plan,
                self._root,
                self._state,
                app_folder_names=app_folders,
                max_per_cycle=self._max,
            )

            await self._apply_folders(plan)
            await self._apply_ingest(plan)
            await self._apply_moves(plan)
            await self._apply_deletions(plan)

            if not plan.is_empty():
                await asyncio.to_thread(self._state.save)
                logger.info(
                    f"rag_watch: 인제스트 {len(plan.to_ingest)} / 이동 {len(plan.to_move)} / "
                    f"보류 {len(plan.unstable)} / 다음주기 {len(plan.deferred)} / "
                    f"변화없음 {plan.skipped} (누적 {len(self._state)}건)"
                )

            self._cycles += 1
            return plan

    # ── 폴더 ────────────────────────────────────────────────────────────────

    def _app_folder_names(self) -> set[str]:
        from app.rag_routes import _load_folders

        return {str(f["name"]) for f in _load_folders()}

    async def _apply_folders(self, plan: ScanPlan) -> None:
        for name in plan.folders_to_create_in_app:
            try:
                await asyncio.to_thread(self._create_app_folder, name)
                logger.info(f"rag_watch: 앱 폴더 생성 '{name}' (디스크에서 발견)")
            except Exception as exc:
                logger.warning(f"rag_watch: 앱 폴더 생성 실패 '{name}': {exc!r}")

        for name in plan.folders_to_create_on_disk:
            safe = sanitize_folder_name(name)
            if safe is None:
                continue
            try:
                (self._root / safe).mkdir(parents=True, exist_ok=True)
                logger.info(f"rag_watch: 디스크 폴더 생성 '{safe}' (UI에서 만든 폴더)")
            except Exception as exc:
                logger.warning(f"rag_watch: 디스크 폴더 생성 실패 '{safe}': {exc!r}")

    def _create_app_folder(self, name: str) -> str:
        """rag_folders.json에 폴더를 추가하고 folder_id를 돌려준다."""
        import uuid

        from app.rag_routes import _ensure_folder_dir, _load_folders, _save_folders

        folders = _load_folders()
        existing = next((f for f in folders if f["name"] == name), None)
        if existing is not None:
            return str(existing["folder_id"])

        folder_id = uuid.uuid4().hex[:12]
        folders.append({"folder_id": folder_id, "name": name})
        _save_folders(folders)
        _ensure_folder_dir(folder_id)
        return folder_id

    def _folder_id_for(self, name: str | None) -> str | None:
        if name is None:
            return None
        return self._create_app_folder(name)

    # ── 인제스트 ────────────────────────────────────────────────────────────

    async def _apply_ingest(self, plan: ScanPlan) -> None:
        from app.rag_routes import ingest_document_bytes

        for cand in plan.to_ingest:
            try:
                data = await asyncio.to_thread(cand.path.read_bytes)
                # 해시는 읽은 내용에서 다시 계산한다 — 스캔 시점과 다르면 그 사이 바뀐 것이라
                # 다음 주기에 처리하도록 넘긴다.
                digest = await asyncio.to_thread(file_digest, cand.path)
                folder_id = await asyncio.to_thread(self._folder_id_for, cand.folder_name)

                res = await ingest_document_bytes(
                    self._ctx,
                    filename=cand.path.name,
                    data=data,
                    folder_id=folder_id,
                )
                self._state.record(
                    digest,
                    rel_path=cand.rel_path,
                    doc_id=res.doc_id,
                    folder_name=cand.folder_name,
                    size=cand.size,
                )
                logger.info(
                    f"rag_watch: 인제스트 완료 {cand.rel_path} "
                    f"(doc_id={res.doc_id}, 청크 {res.chunk_count})"
                )
            except Exception as exc:
                # 한 파일이 실패해도 나머지는 계속한다. 상태에 기록하지 않으므로 다음 주기에 재시도.
                logger.warning(f"rag_watch: 인제스트 실패 {cand.rel_path}: {exc!r}")

    # ── 폴더 이동 ───────────────────────────────────────────────────────────

    async def _apply_moves(self, plan: ScanPlan) -> None:
        for cand, doc_id in plan.to_move:
            if not doc_id:
                continue
            try:
                folder_id = await asyncio.to_thread(self._folder_id_for, cand.folder_name)
                await asyncio.to_thread(self._move_doc, doc_id, folder_id)
                digest = await asyncio.to_thread(file_digest, cand.path)
                self._state.update_location(
                    digest, rel_path=cand.rel_path, folder_name=cand.folder_name
                )
                logger.info(
                    f"rag_watch: 폴더 이동 {cand.rel_path} → '{cand.folder_name}' "
                    "(재임베딩 없음)"
                )
            except Exception as exc:
                logger.warning(f"rag_watch: 폴더 이동 실패 {cand.rel_path}: {exc!r}")

    def _exceeds_guard(self, missing: int) -> bool:
        """한 주기 삭제량이 가드를 넘는가.

        비율만 쓰면 문서가 4건일 때 1건 삭제도 25%를 넘어 막힌다. 그래서 절대 개수
        하한(unindex_guard_min)을 함께 둔다.
        """
        if self._guard_ratio <= 0:
            return False
        if missing <= self._guard_min:
            return False
        tracked = len(self._state)
        if tracked == 0:
            return False
        return (missing / tracked) > self._guard_ratio

    def _move_doc(self, doc_id: str, folder_id: str | None) -> None:
        """청크 category 갱신 — PATCH /documents/{doc_id}와 같은 효과."""
        rag = getattr(self._ctx, "rag_service", None)
        store = getattr(rag, "store", None) or getattr(rag, "_store", None)
        if store is None:
            raise RuntimeError("vector store unavailable")
        store.update_doc_category(doc_id, folder_id)

    # ── 삭제 ────────────────────────────────────────────────────────────────

    async def _apply_deletions(self, plan: ScanPlan) -> None:
        if not plan.missing_digests:
            return

        if self._delete_policy != "unindex":
            # 기본값. 마운트 실패·실수한 이동으로 문서 수백 건이 조용히 사라지는 것이
            # 잘못 남아 있는 것보다 위험하다.
            logger.info(
                f"rag_watch: 사라진 파일 {len(plan.missing_digests)}건 — "
                "delete_policy=ignore이므로 색인은 유지합니다"
            )
            return

        # 대량 삭제 가드 — 마운트 실패·권한 오류로 폴더가 비어 보이면 색인 전체가 날아간다.
        # "파일이 없다"와 "폴더를 읽을 수 없다"를 구분할 방법이 없으므로, 한 주기에
        # 사라진 양이 비정상적으로 많으면 삭제하지 않고 사람이 판단하게 한다.
        if self._exceeds_guard(len(plan.missing_digests)):
            logger.error(
                f"rag_watch: 사라진 파일이 {len(plan.missing_digests)}건 "
                f"(추적 {len(self._state)}건 중)으로 비정상적으로 많아 삭제를 중단했습니다. "
                f"감시 루트({self._root})가 정상 마운트됐는지 확인하세요. "
                "의도한 대량 삭제라면 문서 탭에서 직접 삭제하거나 "
                "app.rag_watch.unindex_guard_ratio를 조정하세요."
            )
            return

        rag = getattr(self._ctx, "rag_service", None)
        store = getattr(rag, "store", None) or getattr(rag, "_store", None)
        for digest in plan.missing_digests:
            entry = self._state.get(digest) or {}
            doc_id = str(entry.get("doc_id") or "")
            if not doc_id or store is None:
                continue
            try:
                await asyncio.to_thread(store.delete_by_doc_id, doc_id)
                self._state.forget(digest)
                logger.info(f"rag_watch: 색인 제거 {entry.get('path')} (doc_id={doc_id})")
            except Exception as exc:
                logger.warning(f"rag_watch: 색인 제거 실패 {doc_id}: {exc!r}")

# src/kg/service.py
"""M_23 파이프라인을 앱 안에서 돌리는 서비스 계층 (스펙 §8).

CLI(`scripts/kg_run.py`)만 두면 사용자가 터미널을 열어야 한다. 이미 UI에 그래프
재인덱싱·중단 버튼이 있는데 새 파이프라인만 CLI로 두는 것은 앞뒤가 안 맞는다
(사용자 지적). 백엔드가 백그라운드 작업으로 돌리고, 진행 상태와 중단을 REST로 노출한다.

동시에 하나만 돈다 — 추출은 GPU를 오래 잡는 작업이라 여러 개가 겹치면 대화까지 느려진다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .candidates import CandidateStore
from .config import KnowledgeGraphConfig
from .documents import build_folder_index
from .extract import DocumentOutcome, ExtractionRunner
from .llm import JsonCompletionClient, probe_schema_mode

logger = logging.getLogger(__name__)


@dataclass
class JobProgress:
    """UI에 내려줄 진행 상태."""

    job_id: str = ""
    state: str = "IDLE"  # IDLE | RUNNING | STOPPING | COMPLETED | CANCELLED | FAILED
    scope: str = ""
    total_documents: int = 0
    processed: int = 0
    completed: int = 0
    partial_failed: int = 0
    failed: int = 0
    accepted: int = 0
    rejected: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    current_doc: str = ""
    error: str = ""
    recent: list[str] = field(default_factory=list)

    @property
    def elapsed_sec(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.monotonic()
        return end - self.started_at

    @property
    def eta_sec(self) -> float:
        if self.processed <= 0 or self.state != "RUNNING":
            return 0.0
        per = self.elapsed_sec / self.processed
        return per * max(0, self.total_documents - self.processed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "scope": self.scope,
            "total_documents": self.total_documents,
            "processed": self.processed,
            "completed": self.completed,
            "partial_failed": self.partial_failed,
            "failed": self.failed,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "eta_sec": round(self.eta_sec, 1),
            "current_doc": self.current_doc,
            "error": self.error,
            "recent": self.recent[-8:],
        }


class KnowledgeGraphService:
    """추출 작업의 시작·중단·상태 조회."""

    def __init__(
        self,
        config: KnowledgeGraphConfig,
        vector_store: Any,
        ollama_base_url: str,
        root: Path,
    ) -> None:
        self._cfg = config
        self._vstore = vector_store
        self._base_url = ollama_base_url.rstrip("/")
        self._root = root
        self._store = CandidateStore(root / config.candidate_db_path)
        self._folders = self._load_folders()
        self._task: asyncio.Task[None] | None = None
        self._runner: ExtractionRunner | None = None
        self._progress = JobProgress()
        self._lock = asyncio.Lock()

    def _load_folders(self) -> dict[str, Any]:
        path = self._root / "data" / "rag_folders.json"
        if not path.exists():
            return {}
        try:
            return build_folder_index(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("KG: 폴더 목록 읽기 실패: %s", exc)
            return {}

    # ── 조회 ──────────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def progress(self) -> dict[str, Any]:
        return self._progress.as_dict()

    def stats(self) -> dict[str, Any]:
        return self._store.stats()

    def folders(self) -> list[dict[str, Any]]:
        """UI 드롭다운용 — 폴더별 문서 수와 처리 현황."""
        tbl = getattr(self._vstore, "_tbl", None)
        if tbl is None:
            return []
        rows = tbl.search().select(["doc_id", "category"]).limit(400_000).to_list()
        by_folder: dict[str, set[str]] = {}
        for r in rows:
            by_folder.setdefault(str(r.get("category") or ""), set()).add(str(r.get("doc_id")))
        done = {d.doc_id for d in self._store.documents_by_state("COMPLETED", limit=100_000)}
        out: list[dict[str, Any]] = []
        for fid, docs in by_folder.items():
            info = self._folders.get(fid)
            if info is None:
                continue
            out.append(
                {
                    "folder_id": fid,
                    "name": info.folder_name,
                    "document_type": info.document_type,
                    "documents": len(docs),
                    "extracted": len(docs & done),
                }
            )
        return sorted(out, key=lambda x: x["name"])

    # ── 실행 ──────────────────────────────────────────────────────────────────

    def _documents_in_folder(self, folder_id: str) -> list[str]:
        tbl = getattr(self._vstore, "_tbl", None)
        if tbl is None:
            return []
        rows = tbl.search().select(["doc_id", "category"]).limit(400_000).to_list()
        return sorted({str(r["doc_id"]) for r in rows if str(r.get("category") or "") == folder_id})

    async def start(
        self,
        *,
        folder_id: str = "",
        doc_ids: list[str] | None = None,
        limit: int = 0,
        resume: bool = True,
        chunks_per_document: int | None = None,
    ) -> dict[str, Any]:
        """추출 작업 시작. 이미 돌고 있으면 거절한다."""
        async with self._lock:
            if self.running:
                return {"started": False, "reason": "이미 진행 중입니다.", **self.progress()}

            targets = list(doc_ids or [])
            scope = "지정 문서"
            if folder_id:
                targets = self._documents_in_folder(folder_id)
                info = self._folders.get(folder_id)
                scope = info.folder_name if info else folder_id
            if resume:
                done = {
                    d.doc_id for d in self._store.documents_by_state("COMPLETED", limit=100_000)
                }
                targets = [d for d in targets if d not in done]
            if limit > 0:
                targets = targets[:limit]
            if not targets:
                return {
                    "started": False,
                    "reason": "대상 문서가 없습니다 (이미 모두 완료).",
                    **self.progress(),
                }

            cfg = self._cfg.model_copy(deep=True)
            if chunks_per_document:
                cfg.extraction.chunks_per_document = chunks_per_document

            model = cfg.extraction.ollama_model
            mode = await probe_schema_mode(self._base_url, model)
            client = JsonCompletionClient(
                self._base_url,
                model,
                schema_mode=mode,
                max_connections=max(16, cfg.jobs.extraction_concurrency * 4),
            )
            runner = ExtractionRunner(
                complete_json=client,
                store=self._store,
                vector_store=self._vstore,
                config=cfg,
                folder_index=self._folders,
                model_name=model,
            )
            self._runner = runner

            job_id = f"kg-{int(time.time())}"
            self._progress = JobProgress(
                job_id=job_id,
                state="RUNNING",
                scope=scope,
                total_documents=len(targets),
                started_at=time.monotonic(),
            )
            self._store.start_job(job_id, "extract", scope=f"{scope}:{len(targets)}docs")
            self._task = asyncio.create_task(self._run(runner, client, targets, job_id))
            logger.info("KG 추출 시작: %s %d문서 (모드=%s)", scope, len(targets), mode)
            return {"started": True, **self.progress()}

    async def _run(
        self,
        runner: ExtractionRunner,
        client: JsonCompletionClient,
        targets: list[str],
        job_id: str,
    ) -> None:
        p = self._progress
        try:
            for doc_id in targets:
                if runner._stop_requested():  # noqa: SLF001 — 같은 모듈군의 협조적 중단
                    p.state = "CANCELLED"
                    break
                p.current_doc = doc_id
                out: DocumentOutcome = await runner.extract_document(doc_id)
                p.processed += 1
                p.accepted += out.accepted
                p.rejected += out.rejected
                if out.state == "COMPLETED":
                    p.completed += 1
                elif out.state == "PARTIAL_FAILED":
                    p.partial_failed += 1
                elif out.state in ("FAILED", "CANCELLED"):
                    p.failed += 1
                p.recent.append(
                    f"{out.state} · 후보 {out.accepted} · {out.elapsed:.0f}초 · {doc_id[:44]}"
                )
            if p.state != "CANCELLED":
                p.state = "COMPLETED"
        except asyncio.CancelledError:
            p.state = "CANCELLED"
            raise
        except Exception as exc:
            p.state = "FAILED"
            p.error = str(exc)[:300]
            logger.error("KG 추출 작업 실패: %s", exc)
        finally:
            p.finished_at = time.monotonic()
            p.current_doc = ""
            await client.aclose()
            self._store.finish_job(
                job_id,
                p.state,
                {
                    "documents": p.processed,
                    "completed": p.completed,
                    "accepted": p.accepted,
                    "rejected": p.rejected,
                },
            )
            logger.info(
                "KG 추출 종료(%s): 문서 %d/%d · 후보 %d건 · %.0f초",
                p.state,
                p.processed,
                p.total_documents,
                p.accepted,
                p.elapsed_sec,
            )

    async def stop(self) -> dict[str, Any]:
        """안전 중단 — 진행 중인 청크가 끝나면 멈춘다.

        작업을 강제 취소하지 않는다. 취소하면 처리 중이던 청크 결과가 날아가고 문서
        상태가 RUNNING에 걸린 채 남는다.
        """
        if not self.running or self._runner is None:
            return {"stopped": False, "reason": "진행 중인 작업이 없습니다.", **self.progress()}
        self._runner.cancel()
        self._progress.state = "STOPPING"
        logger.info("KG 추출 중단 요청 — 청크 경계에서 멈춥니다")
        return {"stopped": True, **self.progress()}

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
import threading
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


class _BuildStopped(Exception):
    """구축 중단 신호 — 단계 경계에서 던진다."""


@dataclass
class BuildProgress:
    """6~9단계 구축 진행 상태. 추출과 단위가 달라(문서가 아니라 단계) 따로 둔다."""

    job_id: str = ""
    state: str = "IDLE"  # IDLE | RUNNING | STOPPING | COMPLETED | CANCELLED | FAILED
    scope: str = ""
    stage: str = ""
    done: int = 0
    total: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    counts: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_sec(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.finished_at or time.monotonic()) - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "scope": self.scope,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "error": self.error,
            "counts": self.counts,
        }


class KnowledgeGraphService:
    """추출 작업과 그래프 구축의 시작·중단·상태 조회."""

    def __init__(
        self,
        config: KnowledgeGraphConfig,
        vector_store: Any,
        ollama_base_url: str,
        root: Path,
        graph_store_factory: Any = None,
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
        # 6~9단계 구축 (CR-61) — 추출과 별개 작업이라 상태도 따로 갖는다.
        self._build_task: asyncio.Task[None] | None = None
        self._build_progress = BuildProgress()
        self._build_lock = asyncio.Lock()
        self._build_stop = threading.Event()
        # 추출이 끝나면 구축까지 자동으로 잇는다. 10시간짜리 작업이 새벽에 끝나도
        # 아침에 그래프가 준비돼 있어야 한다 (CR-61).
        self._graph_store_factory = graph_store_factory
        self._build_after = False

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

    def _scan_doc_categories(self) -> list[tuple[str, str]]:
        """벡터 스토어에서 (doc_id, category)를 **빠짐없이** 읽는다 (E-91).

        여기 limit을 상수로 박으면 코퍼스가 그 값을 넘는 순간 **조용히 잘린다.**
        실제로 그랬다 — 상한 400,000에 청크가 599,338개가 되면서 폴더 3개가 드롭다운에서
        통째로 사라졌고, 사용자가 고를 수 없으니 문서 5,953건(49%)이 추출되지 않았다.
        오류도 경고도 없었고 목록이 그냥 짧았다.

        그래서 상한을 현재 행 수에서 구하고, 그래도 꽉 찼으면 **에러 로그를 남긴다.**
        조용히 틀리느니 시끄럽게 틀리는 편이 낫다.
        """
        tbl = getattr(self._vstore, "_tbl", None)
        if tbl is None:
            return []
        try:
            total = int(tbl.count_rows())
        except Exception as exc:
            logger.warning("KG: 청크 수 조회 실패, 넉넉한 상한으로 대체: %s", exc)
            total = 5_000_000
        cap = total + 10_000
        rows = tbl.search().select(["doc_id", "category"]).limit(cap).to_list()
        if len(rows) >= cap:
            logger.error(
                "KG 폴더 스캔이 상한(%d)에 걸렸다 — 폴더 목록이 불완전할 수 있다. "
                "청크 %d개. 상한 계산을 확인할 것.",
                cap,
                total,
            )
        return [(str(r.get("doc_id") or ""), str(r.get("category") or "")) for r in rows]

    def folders(self) -> list[dict[str, Any]]:
        """UI 드롭다운용 — 폴더별 문서 수와 처리 현황."""
        tbl = getattr(self._vstore, "_tbl", None)
        if tbl is None:
            return []
        by_folder: dict[str, set[str]] = {}
        for doc_id, category in self._scan_doc_categories():
            by_folder.setdefault(category, set()).add(doc_id)
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
        """폴더 안 문서 목록 — **추출 대상 선정에 쓰이므로 빠짐이 있으면 안 된다** (E-91).

        예전 상한 400,000 때문에 이 함수가 폴더의 일부만 돌려주던 시기가 있었다.
        그 결과 "추출 완료"로 보이는 폴더에 실제로는 처리 안 된 문서가 남았다.
        """
        return sorted({d for d, c in self._scan_doc_categories() if c == folder_id})

    def _documents_all_folders(self) -> list[str]:
        """등록된 모든 폴더의 문서 — **작은 폴더부터** 정렬해서 돌려준다.

        정렬이 취향 문제가 아니다. 전체 추출은 25시간짜리라 큰 폴더를 먼저 잡으면
        몇 시간 동안 진행률이 한 폴더 안에서만 기어간다. 작은 것부터 끝내면 사용자가
        "돌아가고 있다"를 일찍 확인할 수 있다.

        `self._folders`에 없는 카테고리(`__knowledge__` 노트 등)는 자연히 빠진다 —
        노트는 연구문서가 아니라 추출 대상이 아니다.
        """
        by_folder: dict[str, set[str]] = {}
        for doc_id, category in self._scan_doc_categories():
            if category in self._folders:
                by_folder.setdefault(category, set()).add(doc_id)
        out: list[str] = []
        for _fid, docs in sorted(by_folder.items(), key=lambda kv: len(kv[1])):
            out.extend(sorted(docs))
        return out

    async def start(
        self,
        *,
        folder_id: str = "",
        doc_ids: list[str] | None = None,
        limit: int = 0,
        resume: bool = True,
        chunks_per_document: int | None = None,
        all_folders: bool = False,
        build_after: bool = False,
    ) -> dict[str, Any]:
        """추출 작업 시작. 이미 돌고 있으면 거절한다.

        `build_after=True`면 추출이 **정상 완료**됐을 때 6~9단계 구축을 자동으로 잇는다.
        """
        async with self._lock:
            if self.running:
                return {"started": False, "reason": "이미 진행 중입니다.", **self.progress()}

            targets = list(doc_ids or [])
            scope = "지정 문서"
            if all_folders:
                targets = self._documents_all_folders()
                scope = "전체"
            elif folder_id:
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
            if all_folders:
                scope = f"전체 (미추출 {len(targets):,}건)"

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
            self._build_after = build_after
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

        # ── 추출 → 구축 자동 연결 (CR-61) ────────────────────────────────────
        #
        # `finally` **밖**에서 건다. 안에서 걸면 취소·실패한 작업에도 구축이 붙고,
        # 예외가 나면 그게 원래 예외를 덮는다. 여기까지 왔다는 것은 정상 종료라는 뜻이다.
        #
        # 10시간짜리 추출이 새벽에 끝나도 아침에 그래프가 준비돼 있어야 한다. 사용자가
        # 화면을 보고 있을 필요도, 브라우저가 켜져 있을 필요도 없다 — 백엔드가 스스로 잇는다.
        if self._build_after and p.state == "COMPLETED":
            self._build_after = False
            if p.accepted <= 0:
                logger.warning("추출이 후보를 하나도 못 만들어 자동 구축을 건너뛴다")
                return
            logger.info(
                "추출 완료 — 그래프 구축을 자동으로 이어서 시작합니다 (후보 %d건)", p.accepted
            )
            try:
                await self.start_build(graph_store_factory=self._graph_store_factory)
            except Exception as exc:  # 구축 실패가 추출 결과를 무효화하지는 않는다
                logger.error("자동 그래프 구축 시작 실패 (수동으로 눌러야 합니다): %s", exc)
        elif self._build_after:
            self._build_after = False
            logger.info("추출이 %s로 끝나 자동 구축을 걸지 않는다", p.state)

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

    # ── 6~9단계 그래프 구축 (CR-61) ───────────────────────────────────────────
    #
    # 추출(1~5)과 성격이 다르다. LLM을 부르지 않고 GPU를 쓰지 않으며 CPU·SQLite만
    # 쓴다. 그래서 별도 작업으로 두고, 추출과 동시에 돌지 않게만 막는다 — 같은 SQLite에
    # 쓰는데 추출이 후보를 넣는 동안 구축이 그것을 읽으면 반쪽짜리 그래프가 나온다.

    @property
    def build_running(self) -> bool:
        return self._build_task is not None and not self._build_task.done()

    def build_progress(self) -> dict[str, Any]:
        return self._build_progress.as_dict()

    async def start_build(
        self,
        *,
        folder_id: str = "",
        dry_run: bool = False,
        purge_legacy: bool = False,
        graph_store_factory: Any = None,
    ) -> dict[str, Any]:
        """6~9단계를 백그라운드로 돌린다."""
        async with self._build_lock:
            if self.build_running:
                return {"started": False, "reason": "구축이 이미 진행 중입니다."}
            if self.running:
                return {
                    "started": False,
                    "reason": "추출이 진행 중입니다. 끝난 뒤에 구축하세요.",
                }

            scope = "전체"
            doc_ids: list[str] | None = None
            if folder_id:
                info = self._folders.get(folder_id)
                scope = info.folder_name if info else folder_id
                rows = self._store._conn.execute(  # noqa: SLF001
                    "SELECT doc_id FROM documents WHERE folder_name=? OR folder_id=?",
                    (scope, folder_id),
                ).fetchall()
                doc_ids = [r["doc_id"] for r in rows]

            self._build_stop.clear()
            self._build_progress = BuildProgress(
                job_id=f"kgbuild-{int(time.time())}",
                state="RUNNING",
                scope=scope,
                started_at=time.monotonic(),
            )
            self._build_task = asyncio.create_task(
                asyncio.to_thread(
                    self._run_build, doc_ids, dry_run, purge_legacy, graph_store_factory
                )
            )
            logger.info("KG 그래프 구축 시작: %s (dry_run=%s)", scope, dry_run)
            return {"started": True, **self.build_progress()}

    def _run_build(
        self,
        doc_ids: list[str] | None,
        dry_run: bool,
        purge_legacy: bool,
        graph_store_factory: Any,
    ) -> None:
        """스레드에서 도는 구축 본체. CPU 작업이라 이벤트 루프를 막지 않게 분리한다."""
        from .derive import derive_all
        from .neo4j_load import load_graph, load_summary
        from .normalize import consolidate_documents, normalize_global

        p = self._build_progress
        # 스레드 전용 연결 — CandidateStore는 스레드마다 연결을 따로 갖는다.
        store = CandidateStore(self._root / self._cfg.candidate_db_path)

        def progress(stage: str, done: int, total: int) -> None:
            p.stage = stage
            p.done = done
            p.total = total

        def should_stop() -> bool:
            return self._build_stop.is_set()

        try:
            self._store.start_job(p.job_id, "build", scope=p.scope)
            p.stage = "consolidate"
            p.counts["consolidate"] = consolidate_documents(
                store, self._cfg, doc_ids, progress, should_stop
            ).as_dict()
            if should_stop():
                raise _BuildStopped
            p.stage = "normalize"
            p.counts["normalize"] = normalize_global(
                store, self._cfg, progress, should_stop
            ).as_dict()
            if should_stop():
                raise _BuildStopped
            p.stage = "derive"
            p.counts["derive"] = derive_all(store, self._cfg, should_stop).as_dict()
            if should_stop():
                raise _BuildStopped

            if dry_run or graph_store_factory is None:
                p.counts["load_preview"] = load_summary(store)
                p.counts["note"] = "dry-run — Neo4j에 쓰지 않음"
            else:
                p.stage = "load"
                graph = graph_store_factory()
                try:
                    if not graph.ping():
                        p.counts["load"] = {"error": "neo4j_unavailable"}
                    else:
                        p.counts["load"] = load_graph(
                            store,
                            graph,
                            self._cfg,
                            purge_legacy=purge_legacy,
                            progress=progress,
                            should_stop=should_stop,
                        ).as_dict()
                finally:
                    graph.close()
            p.state = "CANCELLED" if should_stop() else "COMPLETED"
        except _BuildStopped:
            p.state = "CANCELLED"
        except Exception as exc:
            p.state = "FAILED"
            p.error = str(exc)[:300]
            logger.exception("KG 그래프 구축 실패: %s", exc)
        finally:
            p.finished_at = time.monotonic()
            p.stage = ""
            self._store.finish_job(p.job_id, p.state, p.counts, p.error)
            store.close()
            logger.info("KG 그래프 구축 종료(%s): %.0f초", p.state, p.elapsed_sec)

    async def stop_build(self) -> dict[str, Any]:
        """단계 경계에서 멈춘다. 이미 끝난 단계의 결과는 SQLite에 남는다."""
        if not self.build_running:
            return {"stopped": False, "reason": "진행 중인 구축이 없습니다."}
        self._build_stop.set()
        self._build_progress.state = "STOPPING"
        logger.info("KG 그래프 구축 중단 요청")
        return {"stopped": True, **self.build_progress()}

    def review_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        from .normalize import review_queue as _rq

        return _rq(self._store, limit)

    def report(self) -> dict[str, Any]:
        from .report import build_report, connectivity_warnings

        rep = build_report(self._store, self._cfg)
        rep["경고"] = connectivity_warnings(rep)
        return rep

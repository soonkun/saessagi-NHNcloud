# src/kg/extract.py
"""M_23 후보 추출 실행기 (스펙 §3 (2)~(5)).

이미 임베딩이 끝난 청크를 **읽기만** 한다. 파싱·청킹·임베딩은 건드리지 않는다.

한 청크 = 한 호출이다. 여러 청크를 묶어 보내면 호출 수는 줄지만 근거가 어느 청크 것인지
뒤섞인다 — 지침서가 최우선으로 꼽은 Evidence Accuracy를 지키려면 묶으면 안 된다.

실패는 배치를 멈추지 않는다(지침서 11.2). 청크 하나가 세 번 실패하면 그 청크만 FAILED로
남기고 다음으로 간다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .candidates import CandidateStore, DocumentMeta, EntityCandidate
from .config import KnowledgeGraphConfig
from .documents import FolderInfo, ScoredChunk, select_chunks
from .identity import ProjectIdentity, extract_identity
from .prompts import (
    EXTRACTOR_VERSION,
    PROMPT_VERSION,
    extraction_json_schema,
    extraction_prompts,
)
from .validate import ValidationResult, target_key, validate_entities

logger = logging.getLogger(__name__)

# 청크 본문이 지나치게 길면 잘라 보낸다 — 컨텍스트 초과로 응답이 통째로 날아가는 것을 막는다.
_MAX_CHUNK_CHARS = 6000
# 식별 정보(과제번호·과제명)를 찾을 때 볼 앞부분 분량.
_IDENTITY_SCAN_CHARS = 8000


class CompleteJsonFn(Protocol):
    """`GemmaChatAgent.complete_json`과 같은 계약. 테스트에서는 대역을 끼운다."""

    async def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = ...,
        temperature: float = ...,
        timeout_seconds: float = ...,
    ) -> dict[str, Any]: ...


@dataclass
class ChunkOutcome:
    """청크 하나의 처리 결과."""

    chunk_id: str
    state: str  # DONE | FAILED | SKIPPED
    accepted: int = 0
    rejected: int = 0
    attempts: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)
    error: str = ""
    elapsed: float = 0.0


@dataclass
class DocumentOutcome:
    """문서 하나의 처리 결과 — 실측 보고와 게이트 판정의 재료."""

    doc_id: str
    state: str
    identity: ProjectIdentity = field(default_factory=ProjectIdentity)
    total_chunks: int = 0
    selected_chunks: int = 0
    chunks: list[ChunkOutcome] = field(default_factory=list)
    elapsed: float = 0.0
    error: str = ""

    @property
    def accepted(self) -> int:
        return sum(c.accepted for c in self.chunks)

    @property
    def rejected(self) -> int:
        return sum(c.rejected for c in self.chunks)

    @property
    def failed_chunks(self) -> int:
        return sum(1 for c in self.chunks if c.state == "FAILED")


def _candidate_id(doc_id: str, chunk_id: str, entity_type: str, name: str) -> str:
    """후보 id는 **내용으로 정한다**(해시).

    같은 청크를 다시 처리하면 같은 id가 나와야 중복이 쌓이지 않는다. 무작위 id를 쓰면
    재실행마다 새 행이 생겨 지침서 4.6(재실행 안전성)이 깨진다.
    """
    raw = f"{doc_id}|{chunk_id}|{entity_type}|{name}".encode()
    return "ec_" + hashlib.sha1(raw).hexdigest()[:20]


class ExtractionRunner:
    """문서 단위로 후보를 뽑아 후보 저장소에 넣는다."""

    def __init__(
        self,
        complete_json: CompleteJsonFn,
        store: CandidateStore,
        vector_store: Any,
        config: KnowledgeGraphConfig,
        folder_index: dict[str, FolderInfo] | None = None,
        model_name: str = "",
    ) -> None:
        self._complete = complete_json
        self._store = store
        self._vstore = vector_store
        self._cfg = config
        self._folders = folder_index or {}
        self._model = model_name
        self._cancelled = False
        self._stop_file: Path | None = None

    def cancel(self) -> None:
        """안전한 중단 — 청크 경계에서 멈춘다 (지침서 23장)."""
        self._cancelled = True

    def set_stop_file(self, path: "Path | None") -> None:
        """중단 신호 파일. 배치가 백그라운드로 도는 동안 밖에서 멈출 수 있게 한다.

        프로세스를 강제 종료하면 처리 중이던 청크의 결과가 날아가고 문서 상태가
        RUNNING에 걸린 채 남는다. 파일을 보고 **청크 경계에서** 스스로 멈추면 이미 저장한
        후보가 온전히 남고 다음 실행이 이어받을 수 있다.
        """
        self._stop_file = path

    def _stop_requested(self) -> bool:
        if self._cancelled:
            return True
        stop = getattr(self, "_stop_file", None)
        if stop is not None and stop.exists():
            logger.info("KG 추출: 중단 신호 파일 감지 — 청크 경계에서 멈춥니다")
            self._cancelled = True
            return True
        return False

    def reset_cancel(self) -> None:
        self._cancelled = False

    # ── 문서 처리 ─────────────────────────────────────────────────────────────

    async def extract_document(self, doc_id: str) -> DocumentOutcome:
        started = time.monotonic()
        loop = asyncio.get_running_loop()

        rows: list[dict[str, Any]] = await loop.run_in_executor(
            None, lambda: self._vstore.get_chunks_by_doc_id(doc_id, limit=100_000)
        )
        if not rows:
            self._store.set_document_state(doc_id, "FAILED", "청크 없음")
            return DocumentOutcome(doc_id=doc_id, state="FAILED", error="청크 없음")

        first = rows[0]
        doc_name = str(first.get("doc_name") or doc_id)
        folder_id = str(first.get("category") or "")
        folder = self._folders.get(folder_id)

        identity = self._read_identity(rows)
        meta = DocumentMeta(
            doc_id=doc_id,
            doc_name=doc_name,
            folder_id=folder_id,
            folder_name=folder.folder_name if folder else "",
            document_type=folder.document_type if folder else "UNKNOWN",
            # 연도는 **폴더가 우선**이다. 본문 연도 정규식은 인용문·선행연구의 옛 연도를
            # 잡을 수 있다(실측: 2021년 보고서가 1992로 기록됨). 폴더명("2021완결보고서")은
            # 사람이 정리해 둔 값이라 훨씬 믿을 만하다.
            year=((folder.year if folder else None) or identity.start_year),
            project_no=identity.project_no,
            rfp_no=identity.rfp_no,
            title=identity.title or doc_name,
            chunk_count=len(rows),
            extract_state="RUNNING",
            extractor_version=EXTRACTOR_VERSION,
            prompt_version=PROMPT_VERSION,
        )
        self._store.upsert_document(meta)

        selected = select_chunks(rows, self._cfg.extraction.chunks_per_document)
        outcome = DocumentOutcome(
            doc_id=doc_id,
            state="RUNNING",
            identity=identity,
            total_chunks=len(rows),
            selected_chunks=len(selected),
        )

        # 청크를 동시에 처리한다. 실측상 동시성 4에서 처리량이 2.07배가 된다
        # (7.3 → 15.1 청크/분). 6·8은 나아지지 않아 기본값을 4로 둔다.
        # **청크끼리는 완전히 독립**이라 동시 처리해도 근거 귀속이 섞이지 않는다 —
        # 여러 청크를 한 프롬프트에 묶는 것과는 다르다.
        semaphore = asyncio.Semaphore(max(1, self._cfg.jobs.extraction_concurrency))

        async def run_one(chunk: ScoredChunk) -> ChunkOutcome:
            async with semaphore:
                if self._stop_requested():
                    return ChunkOutcome(chunk.chunk_id, "SKIPPED")
                # 대화가 시작되면 남은 청크는 기다렸다 간다 (E-87 GPU 경합).
                await self._yield_to_conversation()
                return await self._extract_chunk(meta, chunk)

        if selected:
            results = await asyncio.gather(*(run_one(c) for c in selected))
            outcome.chunks.extend(results)

        if self._cancelled and any(c.state == "SKIPPED" for c in outcome.chunks):
            outcome.state = "CANCELLED"
            self._store.set_document_state(doc_id, "CANCELLED")
            outcome.elapsed = time.monotonic() - started
            return outcome

        outcome.elapsed = time.monotonic() - started
        if outcome.failed_chunks == len(selected) and selected:
            outcome.state = "FAILED"
            self._store.set_document_state(doc_id, "FAILED", "모든 청크 실패")
        elif outcome.failed_chunks:
            outcome.state = "PARTIAL_FAILED"
            self._store.set_document_state(
                doc_id, "PARTIAL_FAILED", f"{outcome.failed_chunks}청크 실패"
            )
        else:
            outcome.state = "COMPLETED"
            self._store.set_document_state(doc_id, "COMPLETED")

        logger.info(
            "KG 추출 완료: doc=%s 상태=%s 청크 %d/%d 후보 %d건(거절 %d) %.1f초",
            doc_id,
            outcome.state,
            outcome.selected_chunks,
            outcome.total_chunks,
            outcome.accepted,
            outcome.rejected,
            outcome.elapsed,
        )
        return outcome

    def _read_identity(self, rows: list[dict[str, Any]]) -> ProjectIdentity:
        """앞쪽 청크를 이어 붙여 과제번호·과제명을 찾는다 (LLM 호출 없음)."""

        def page_of(row: dict[str, Any]) -> int:
            try:
                return int(row.get("page") or 0)
            except (TypeError, ValueError):
                return 0

        head = sorted(rows, key=page_of)[:8]
        text = "\n".join(str(r.get("text") or "") for r in head)
        return extract_identity(text[:_IDENTITY_SCAN_CHARS])

    async def _yield_to_conversation(self) -> None:
        """대화가 도는 중이면 잠깐 물러난다 (E-87 GPU 경합)."""
        if not self._cfg.jobs.yield_to_conversation:
            return
        try:
            from rag_watch.activity import is_conversation_active
        except Exception:
            return
        waited = 0.0
        while is_conversation_active() and waited < 300.0 and not self._cancelled:
            await asyncio.sleep(2.0)
            waited += 2.0

    # ── 청크 처리 ─────────────────────────────────────────────────────────────

    async def _extract_chunk(self, meta: DocumentMeta, chunk: ScoredChunk) -> ChunkOutcome:
        cfg = self._cfg.extraction
        started = time.monotonic()
        text = chunk.text[:_MAX_CHUNK_CHARS]
        schema = extraction_json_schema(cfg.enabled_entity_types, cfg.max_entities_per_chunk)

        last_error = ""
        # 시도 1·2는 같은 프롬프트, 마지막 시도는 축약 프롬프트 (지침서 11.2)
        for attempt in range(cfg.max_retries + 1):
            if self._stop_requested():
                return ChunkOutcome(chunk.chunk_id, "SKIPPED", attempts=attempt)
            retry_mode = attempt >= cfg.max_retries and cfg.max_retries > 0
            system, user = extraction_prompts(
                doc_id=meta.doc_id,
                doc_name=meta.doc_name,
                document_type=meta.document_type,
                year=meta.year,
                project_no=meta.project_no,
                project_title=meta.title,
                section_hint=chunk.section_hint,
                chunk_id=chunk.chunk_id,
                page=chunk.page,
                chunk_text=text,
                entity_types=cfg.enabled_entity_types,
                max_entities=cfg.max_entities_per_chunk,
                retry=retry_mode,
            )
            try:
                raw = await self._complete(
                    system,
                    user,
                    schema,
                    max_tokens=self._cfg.extraction.max_output_tokens,
                    temperature=cfg.temperature,
                    timeout_seconds=cfg.timeout_seconds,
                )
            except Exception as exc:  # 타임아웃·연결 실패·JSON 파싱 실패
                last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning(
                    "KG 추출 실패 (doc=%s chunk=%s 시도 %d): %s",
                    meta.doc_id,
                    chunk.chunk_id,
                    attempt + 1,
                    last_error,
                )
                continue

            entities = raw.get("entities")
            if not isinstance(entities, list):
                last_error = "entities 필드 없음"
                continue

            result = self._validate(entities, text)
            self._save(meta, chunk, result)
            return ChunkOutcome(
                chunk_id=chunk.chunk_id,
                state="DONE",
                accepted=len(result.accepted),
                rejected=len(result.rejected),
                attempts=attempt + 1,
                reject_reasons={
                    k: v for k, v in result.counts.items() if k not in ("accepted", "rejected")
                },
                elapsed=time.monotonic() - started,
            )

        # 세 번 실패 — 이 청크만 포기하고 배치는 계속한다.
        return ChunkOutcome(
            chunk_id=chunk.chunk_id,
            state="FAILED",
            attempts=cfg.max_retries + 1,
            error=last_error,
            elapsed=time.monotonic() - started,
        )

    def _validate(self, entities: list[Any], source_text: str) -> ValidationResult:
        cfg = self._cfg.extraction
        return validate_entities(
            [e for e in entities if isinstance(e, dict)],
            source_text=source_text,
            allowed_types=cfg.enabled_entity_types,
            max_entities=cfg.max_entities_per_chunk,
            minimum_confidence=cfg.minimum_confidence,
            evidence_threshold=cfg.evidence_match_threshold,
            skip_citation_only=cfg.skip_citation_only,
        )

    def _save(self, meta: DocumentMeta, chunk: ScoredChunk, result: ValidationResult) -> None:
        candidates = [
            EntityCandidate(
                candidate_id=_candidate_id(meta.doc_id, chunk.chunk_id, e.entity_type, e.name),
                doc_id=meta.doc_id,
                chunk_id=chunk.chunk_id,
                page=chunk.page,
                temp_id=e.temp_id,
                entity_type=e.entity_type,
                surface_form=e.name,
                canonical_name_candidate=e.canonical_name_candidate,
                description=e.description,
                statement_status=e.statement_status,
                project_relevance=e.project_relevance,
                specificity=e.specificity,
                # 대상 키(작물·병해충·지역)는 병합 금지 판정(스펙 §6 R2)의 입력이다.
                # LLM이 짚어 준 값이라 코드가 작물 목록을 가질 필요가 없다.
                target_key=target_key(e.target_terms),
                evidence=e.evidence,
                confidence=e.confidence,
                section_hint=chunk.section_hint,
                extractor_model=self._model,
                extractor_version=EXTRACTOR_VERSION,
                prompt_version=PROMPT_VERSION,
                state="PENDING",
                reject_reason="",
                doc_entity_id=None,
                canonical_id=None,
            )
            for e in result.accepted
        ]
        self._store.replace_candidates_for_chunk(meta.doc_id, chunk.chunk_id, candidates)

    # ── 여러 문서 ─────────────────────────────────────────────────────────────

    async def extract_documents(self, doc_ids: list[str]) -> list[DocumentOutcome]:
        """문서 목록을 순차 처리한다. 중단 요청이 오면 문서 경계에서 멈춘다."""
        outcomes: list[DocumentOutcome] = []
        for doc_id in doc_ids:
            if self._stop_requested():
                break
            try:
                outcomes.append(await self.extract_document(doc_id))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("KG 추출 중 예외 (doc=%s): %s", doc_id, exc)
                self._store.set_document_state(doc_id, "FAILED", str(exc))
                outcomes.append(DocumentOutcome(doc_id=doc_id, state="FAILED", error=str(exc)))
        return outcomes


def entity_target_key(target_terms: list[str]) -> str:
    """추출 결과의 대상 키 — merge 단계가 쓰는 값 (validate.target_key 재수출)."""
    return target_key(target_terms)

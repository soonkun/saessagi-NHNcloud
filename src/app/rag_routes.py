from __future__ import annotations

import asyncio
import concurrent.futures
import html
import json
import logging
import multiprocessing as mp
import os
import re
import shutil
import threading
import uuid
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".pptx", ".hwpx", ".markdown"}
# app_config 부재 시(테스트 등) fallback. 실제 값은 conf.yaml app.rag_chunk_chars/overlap.
_CHUNK_SIZE = 2000
_CHUNK_OVERLAP = 150

_ROOT = Path(os.environ.get("SAESSAGI_ROOT", "."))
_FOLDERS_FILE = _ROOT / "data" / "rag_folders.json"
_ORIGINALS_DIR = _ROOT / "data" / "rag_originals"
_NO_FOLDER_BUCKET = "__no_folder__"


# Windows 파일명 금지 문자 (HTML 엔티티 디코딩 후 나올 수 있음)
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 임베딩 직렬화 락 — 프론트가 파일 여러 개를 동시 업로드하면(동시 6연결 관측)
# to_thread로 옮긴 임베딩이 같은 SentenceTransformer 인스턴스에 병렬 진입한다.
# 모델 호출은 스레드 안전이 보장되지 않고 VRAM 스파이크도 생기므로 1개씩 처리.
_EMBED_LOCK = threading.Lock()

# 업로드/삭제 후 컴팩션 디바운스 태스크 (E-40)
_optimize_task: "asyncio.Task[None] | None" = None


def _schedule_store_optimize(store: Any, delay_seconds: float = 60.0) -> None:
    """마지막 업로드/삭제 후 delay_seconds 뒤에 컴팩션 1회 실행 (디바운스).

    일괄 업로드 중에는 매 요청이 타이머를 리셋하므로, 배치가 끝나고 잠잠해진
    시점에 한 번만 돈다. 컴팩션 없이 업서트·삭제가 누적되면 작은 프래그먼트가
    수백 개로 늘어 검색이 느려진다 (E-40).
    """
    global _optimize_task
    if _optimize_task is not None and not _optimize_task.done():
        _optimize_task.cancel()

    async def _run() -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await asyncio.to_thread(store.optimize)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("store optimize 실패 (무시): %s", exc)

    _optimize_task = asyncio.create_task(_run())


def _sanitize_filename(raw: str) -> str:
    """업로드 파일명 정규화.

    웹 포털에서 받은 파일은 이름에 HTML 엔티티(&#8729; 등)가 그대로 남아 있는 경우가
    있다. 이 엔티티의 '#'이 doc_id에 들어가면 URL fragment로 해석되는 등 문제를
    일으키므로 실제 문자로 디코딩하고, 디코딩 결과의 Windows 금지 문자는 제거한다.
    """
    # multipart 파싱이 UTF-8 파일명을 surrogateescape로 잘못 디코딩한 경우 복구.
    # (복구하지 않으면 doc_id가 UTF-8 인코딩 불가 문자열이 되어 URL/JSON 모두 깨진다)
    try:
        raw = raw.encode("utf-8", "surrogateescape").decode("utf-8")
    except UnicodeError:
        raw = raw.encode("utf-8", "replace").decode("utf-8")
    name = html.unescape(raw).strip()
    name = _INVALID_FILENAME_CHARS_RE.sub("", name)
    return name or "upload"


def _folder_bucket(folder_id: str | None) -> Path:
    """폴더 ID → 원본 파일 저장 디렉토리.

    None이면 __no_folder__ 버킷. 그 외엔 folder_id 자체가 디렉토리 이름.
    """
    return _ORIGINALS_DIR / (folder_id or _NO_FOLDER_BUCKET)


def _ensure_folder_dir(folder_id: str | None) -> Path:
    bucket = _folder_bucket(folder_id)
    bucket.mkdir(parents=True, exist_ok=True)
    return bucket


def _save_original(folder_id: str | None, doc_id: str, filename: str, data: bytes) -> Path:
    """원본을 data/rag_originals/<folder|__no_folder__>/<doc_id>/<filename> 에 저장.

    청크 upsert 성공 후 호출 — 실패 경로에서 호출 금지.
    """
    target_dir = _folder_bucket(folder_id) / doc_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_bytes(data)
    return target_path


def _find_doc_dir(doc_id: str) -> Path | None:
    """모든 폴더 버킷을 글로빙해서 doc_id 디렉토리 위치를 찾는다."""
    if not _ORIGINALS_DIR.is_dir():
        return None
    for bucket in _ORIGINALS_DIR.iterdir():
        if not bucket.is_dir():
            continue
        candidate = bucket / doc_id
        if candidate.is_dir():
            return candidate
    return None


def _delete_original(doc_id: str) -> None:
    """원본 디렉토리를 제거 (있는 위치에서)."""
    d = _find_doc_dir(doc_id)
    if d is not None:
        shutil.rmtree(d, ignore_errors=True)


def _delete_folder_dir(folder_id: str) -> None:
    """폴더 버킷 전체를 제거 (폴더 안 모든 doc 디렉토리 포함)."""
    shutil.rmtree(_folder_bucket(folder_id), ignore_errors=True)


# ---------- Pydantic models ----------


class FolderInfo(BaseModel):
    folder_id: str
    name: str


class CreateFolderRequest(BaseModel):
    name: str


class RenameFolderRequest(BaseModel):
    name: str


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    chunk_count: int
    folder_id: str | None = None


class UploadResponse(BaseModel):
    doc_id: str
    filename: str
    chunk_count: int
    folder_id: str | None = None


class DeleteResponse(BaseModel):
    ok: bool
    deleted_chunks: int


# ---------- folder helpers ----------


def _load_folders() -> list[dict[str, str]]:
    if _FOLDERS_FILE.exists():
        try:
            data = json.loads(_FOLDERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save_folders(folders: list[dict[str, str]]) -> None:
    # 원자적 쓰기: temp에 쓰고 flush+fsync 후 os.replace로 교체.
    # 직접 write_text는 쓰는 중 프로세스 종료/정전 시 파일이 빈/잘린 상태로 남아
    # 폴더 정의가 통째로 유실될 수 있다 (E-56).
    _FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FOLDERS_FILE.with_suffix(".json.tmp")
    payload = json.dumps(folders, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _FOLDERS_FILE)


def _get_folder_or_404(folder_id: str) -> dict[str, str]:
    folders = _load_folders()
    for f in folders:
        if f["folder_id"] == folder_id:
            return f
    raise HTTPException(status_code=404, detail=f"folder_id '{folder_id}' not found")


# ---------- helpers ----------


def _get_context(request: Request) -> Any:
    return request.app.state.service_context


def _require_rag(ctx: Any) -> Any:
    svc = getattr(ctx, "rag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="rag_service unavailable")
    return svc


@router.get("/search")
async def search_content(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    top_k: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """CR-22: 내용 기반 문서 검색 (그래프 탭 검색창 등 프론트 직접 호출용).

    파일명이 아니라 본문 임베딩+키워드 하이브리드로 찾는다. 그래프RAG가 켜져 있으면
    그래프 융합 검색을 사용한다.
    """
    ctx = _get_context(request)
    graph_rag = getattr(ctx, "graph_rag_service", None)
    if graph_rag is not None:
        result = await graph_rag.hybrid_retrieve(q, top_k=top_k)
    else:
        rag = _require_rag(ctx)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: rag.retrieve(q, top_k))
    return {
        "hits": [
            {
                "doc_id": h.doc_id,
                "doc_name": h.doc_name,
                "page": h.page,
                "snippet": (h.text or "")[:160],
                "score": round(float(h.score), 3),
            }
            for h in result.hits
        ],
        "found": result.found,
    }


def _chunk_with_meta(text: str, doc_name: str, page: int | None) -> str:
    """청크 텍스트 앞에 출처 메타정보를 삽입해 LLM이 자연스럽게 인식하도록 한다."""
    if page is not None:
        return f"[출처: {doc_name}, {page}페이지] {text}"
    return f"[출처: {doc_name}] {text}"


def _parse_to_meta_segments(filename: str, data: bytes) -> list[tuple[str, int | None]]:
    """파서를 호출해 (text, page) 튜플 목록을 반환한다.

    정본 로직은 document_ingest.subprocess_parse.parse_to_meta_segments 에 있다
    (별도 프로세스에서도 동일하게 쓰기 위해 경량 모듈로 분리). 이 함수는 동기 호출용
    얇은 래퍼다.
    """
    from document_ingest.subprocess_parse import parse_to_meta_segments

    return parse_to_meta_segments(filename, data)


async def _parse_isolated(filename: str, data: bytes) -> list[tuple[str, int | None]]:
    """파싱을 **별도 프로세스**에서 수행한다.

    pypdfium2 등 네이티브 파서가 손상/비호환 PDF 페이지에서 illegal-instruction
    (WinError 0xc000001d 등)으로 프로세스를 통째로 죽이는 사고가 반복됐다(E-48).
    파싱을 자식 프로세스로 격리하면 그런 크래시가 나도 **백엔드 메인 프로세스는
    살아남고**, 해당 파일만 422로 깔끔히 실패한다.

    - 매 호출마다 새 프로세스(max_workers=1) → 연속 업로드 시 자원 누적/스파이크 방지.
    - fork가 아닌 spawn → 맥/리눅스에서 CUDA/torch 상태 상속으로 인한 2차 크래시 방지.
    """
    from document_ingest.subprocess_parse import parse_to_meta_segments

    loop = asyncio.get_running_loop()
    ctx = mp.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
        try:
            return await loop.run_in_executor(ex, parse_to_meta_segments, filename, data)
        except BrokenProcessPool as exc:
            logger.error("PDF 파서 프로세스 비정상 종료(손상/비호환 페이지 추정): %s", filename)
            raise HTTPException(
                status_code=422,
                detail=(
                    "이 PDF를 처리할 수 없습니다(손상되었거나 호환되지 않는 페이지 포함). "
                    "PDF를 '다른 이름으로 인쇄/저장'하여 다시 시도해 주세요."
                ),
            ) from exc


def _list_documents_from_store(store: Any) -> list[DocumentInfo]:
    try:
        # 필요한 3개 컬럼만 select — to_arrow()는 1024차원 벡터까지 전부
        # 메모리에 올려(수만 청크 기준 100MB+) 목록 조회마다 수 초가 걸렸다.
        rows: list[dict[str, Any]] = (
            store._tbl.search()
            .select(["doc_id", "doc_name", "category"])
            .limit(1_000_000)
            .to_list()
        )
        if not rows:
            return []

        seen: dict[str, DocumentInfo] = {}
        for row in rows:
            doc_id = row["doc_id"]
            doc_name = row["doc_name"]
            category = row.get("category")
            # M_15: __knowledge__ 카테고리는 문서 탭에서 제외 (노트 탭에서 별도 관리)
            if category == "__knowledge__":
                continue
            if doc_id not in seen:
                seen[doc_id] = DocumentInfo(
                    doc_id=doc_id,
                    filename=doc_name,
                    chunk_count=0,
                    folder_id=category or None,
                )
            seen[doc_id] = DocumentInfo(
                doc_id=doc_id,
                filename=seen[doc_id].filename,
                chunk_count=seen[doc_id].chunk_count + 1,
                folder_id=seen[doc_id].folder_id,
            )
        return list(seen.values())
    except Exception as exc:
        logger.error("list_documents_from_store error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------- folder endpoints ----------


@router.get("/folders", response_model=list[FolderInfo])
async def list_folders() -> list[FolderInfo]:
    return [FolderInfo(**f) for f in _load_folders()]


@router.post("/folders", response_model=FolderInfo, status_code=201)
async def create_folder(body: CreateFolderRequest) -> FolderInfo:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="폴더 이름이 비어있습니다")
    folders = _load_folders()
    for f in folders:
        if f["name"] == name:
            raise HTTPException(status_code=409, detail=f"이미 존재하는 폴더 이름: {name}")
    folder_id = uuid.uuid4().hex[:12]
    new_folder = {"folder_id": folder_id, "name": name}
    folders.append(new_folder)
    _save_folders(folders)
    # 원본 저장소 디렉토리도 동기 생성
    _ensure_folder_dir(folder_id)
    logger.info("create_folder: %s (%s)", name, folder_id)
    return FolderInfo(**new_folder)


@router.patch("/folders/{folder_id}", response_model=FolderInfo)
async def rename_folder(folder_id: str, body: RenameFolderRequest) -> FolderInfo:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="폴더 이름이 비어있습니다")
    folders = _load_folders()
    target = None
    for f in folders:
        if f["folder_id"] == folder_id:
            target = f
        elif f["name"] == name:
            raise HTTPException(status_code=409, detail=f"이미 존재하는 폴더 이름: {name}")
    if target is None:
        raise HTTPException(status_code=404, detail=f"folder_id '{folder_id}' not found")
    target["name"] = name
    _save_folders(folders)
    logger.info("rename_folder: %s → %s", folder_id, name)
    return FolderInfo(**target)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    request: Request,
    folder_id: str,
    delete_docs: bool = Query(default=True),
) -> dict[str, Any]:
    _get_folder_or_404(folder_id)

    deleted_chunks = 0
    if delete_docs:
        ctx = _get_context(request)
        rag = _require_rag(ctx)
        store = getattr(rag, "store", None) or getattr(rag, "_store", None)
        if store is not None:
            # 청크 삭제 실패 시 폴더 레지스트리 삭제도 중단 (고아 청크 방지).
            # category 단일 predicate 일괄 삭제 + to_thread —
            # 문서별 반복 삭제(테이블 전체 스캔 × 문서 수)는 수만 청크에서 이벤트
            # 루프를 수 분간 점유해 앱 전체가 멈췄다 (E-34).
            deleted_chunks = await asyncio.to_thread(store.delete_by_category, folder_id)
            _schedule_store_optimize(store)

    folders = _load_folders()
    _save_folders([f for f in folders if f["folder_id"] != folder_id])
    # delete_docs=True 인 경우만 원본 디렉토리 통째 제거 (안의 doc 디렉토리도 함께)
    # delete_docs=False 라면 원본은 보존하되 폴더 메타만 사라짐 — 이후 doc들은 "폴더 없음" 상태로 노출
    if delete_docs:
        _delete_folder_dir(folder_id)
    logger.info(
        "delete_folder: %s, deleted_chunks=%d, delete_docs=%s",
        folder_id,
        deleted_chunks,
        delete_docs,
    )
    return {"ok": True, "folder_id": folder_id, "deleted_chunks": deleted_chunks}


# ---------- document endpoints ----------


_MAX_UPLOAD_PART_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


@router.post("/documents", response_model=UploadResponse, status_code=201)
async def upload_document(request: Request) -> UploadResponse:
    # Starlette 기본 max_part_size(1 MB)를 우회하기 위해 form()을 직접 호출
    try:
        form = await request.form(max_part_size=_MAX_UPLOAD_PART_BYTES)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"폼 파싱 실패: {exc}")

    file: UploadFile | None = form.get("file")  # type: ignore[assignment]
    # request.form()은 starlette.UploadFile을 반환하므로 StarletteUploadFile로 체크
    if not isinstance(file, StarletteUploadFile):
        raise HTTPException(status_code=422, detail="file 필드가 없습니다")

    folder_id_raw = form.get("folder_id")
    folder_id: str | None = folder_id_raw if isinstance(folder_id_raw, str) else None

    # folder_name 지원 — 폴더 ID 대신 이름으로 분류 가능 (없으면 자동 생성).
    # 채팅 첨부 흐름에서 "업무노트" 폴더로 자동 분류용.
    folder_name_raw = form.get("folder_name")
    folder_name: str | None = folder_name_raw.strip() if isinstance(folder_name_raw, str) else None

    ctx = _get_context(request)
    rag = _require_rag(ctx)

    filename = _sanitize_filename(file.filename or "upload")
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"허용되지 않은 파일 형식: {suffix}. 허용: {', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    # folder_name이 있으면 해당 이름의 폴더로 분류 (없으면 자동 생성)
    if folder_name and not folder_id:
        folders = _load_folders()
        existing = next((f for f in folders if f["name"] == folder_name), None)
        if existing is not None:
            folder_id = existing["folder_id"]
        else:
            new_folder_id = uuid.uuid4().hex[:12]
            new_folder = {"folder_id": new_folder_id, "name": folder_name}
            folders.append(new_folder)
            _save_folders(folders)
            _ensure_folder_dir(new_folder_id)
            folder_id = new_folder_id
            logger.info("upload_document: '%s' 폴더 자동 생성 (id=%s)", folder_name, new_folder_id)

    # folder_id 유효성 확인
    if folder_id is not None:
        _get_folder_or_404(folder_id)

    data = await file.read()
    try:
        # 파싱을 별도 프로세스에서 수행 — 네이티브 파서 크래시로부터 백엔드 보호 (E-48)
        meta_segments = await _parse_isolated(filename, data)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"텍스트 추출 실패: {exc}")

    if not meta_segments:
        raise HTTPException(status_code=422, detail="문서에서 텍스트를 추출할 수 없습니다")

    # 인접한 짧은 세그먼트(개조식 불릿 등)를 chunk_chars까지 병합해 청킹한다.
    # page가 다르면 병합하지 않으므로 출처 페이지 메타는 청크마다 보존된다.
    from document_ingest.segments import chunk_meta_segments

    app_cfg = getattr(ctx, "app_config", None)
    chunk_metas: list[tuple[str, int | None]] = chunk_meta_segments(
        meta_segments,
        chunk_chars=getattr(app_cfg, "rag_chunk_chars", _CHUNK_SIZE),
        overlap_chars=getattr(app_cfg, "rag_chunk_overlap", _CHUNK_OVERLAP),
        topic_min_chars=getattr(app_cfg, "rag_topic_min_chars", 250),
    )

    if not chunk_metas:
        raise HTTPException(status_code=422, detail="청킹 결과가 비어있습니다")

    doc_id = f"{filename}_{uuid.uuid4().hex[:8]}"

    store = getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    def _embed_and_upsert() -> None:
        """임베딩 + upsert (blocking) — to_thread로 실행해 채팅/TTS가 멈추지 않게 한다."""
        from vector_search.types import DocumentChunk

        embedder = rag._embedder
        texts = [c for c, _ in chunk_metas]
        with _EMBED_LOCK:
            vectors: np.ndarray[Any, np.dtype[np.float32]] = embedder.embed_passages(texts)

        doc_chunks = [
            DocumentChunk(
                doc_id=doc_id,
                doc_name=filename,
                category=folder_id,
                page=seg_page,
                section=None,
                chunk_id=str(uuid.uuid4()),
                text=_chunk_with_meta(chunk_text, filename, seg_page),
                bbox=None,
                source_path="",
            )
            for chunk_text, seg_page in chunk_metas
        ]
        store.upsert(doc_chunks, vectors)

    try:
        await asyncio.to_thread(_embed_and_upsert)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("upload_document error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    _schedule_store_optimize(store)

    # 청크 upsert 성공 후에만 원본 저장 — 다운로드용
    try:
        _save_original(folder_id, doc_id, filename, data)
    except Exception as exc:
        logger.error("original save failed (doc_id=%s): %s", doc_id, exc)
        # 원본 저장 실패는 업로드를 실패로 처리하지 않는다(이미 임베딩은 성공).
        # 대신 다음 다운로드 요청 시 404로 응답된다.

    logger.info(
        "upload_document: doc_id=%s, chunks=%d, folder_id=%s", doc_id, len(chunk_metas), folder_id
    )

    # M_19: 그래프 인덱싱 백그라운드 스케줄 — CR-25: auto_index 켜져 있을 때만.
    # 꺼져 있으면 임베딩만 하고, 그래프 구축은 그래프 탭 "재인덱싱"으로 수동 실행.
    graph_rag = getattr(ctx, "graph_rag_service", None)
    _gr_cfg = getattr(getattr(ctx, "app_config", None), "graphrag", None)
    if graph_rag is not None and _gr_cfg is not None and _gr_cfg.auto_index:
        try:
            graph_rag.schedule_index_document(doc_id)
        except Exception as exc:
            logger.warning("GraphRAG 인덱싱 스케줄 실패 (무시): %s", exc)

    return UploadResponse(
        doc_id=doc_id, filename=filename, chunk_count=len(chunk_metas), folder_id=folder_id
    )


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents(request: Request) -> list[DocumentInfo]:
    ctx = _get_context(request)
    rag = _require_rag(ctx)

    store = getattr(rag, "store", None) or getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    if hasattr(store, "list_documents"):
        try:
            return [
                DocumentInfo(
                    doc_id=d["doc_id"],
                    filename=d["filename"],
                    chunk_count=d["chunk_count"],
                    folder_id=d.get("folder_id"),
                )
                for d in store.list_documents()
            ]
        except Exception as exc:
            logger.error("list_documents via list_documents() error: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    return await asyncio.to_thread(_list_documents_from_store, store)


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(request: Request, doc_id: str) -> dict[str, Any]:
    """CR-28: 문서의 청크 목록 조회 — 청킹 결과 검증·기준 튜닝용 뷰어."""
    ctx = _get_context(request)
    rag = _require_rag(ctx)
    store = getattr(rag, "store", None) or getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    rows = await asyncio.to_thread(store.get_chunks_by_doc_id, doc_id, 1000)
    if not rows:
        raise HTTPException(status_code=404, detail=f"doc_id '{doc_id}' not found")

    chunks = [
        {
            "page": r.get("page"),
            "chars": len(str(r.get("text") or "")),
            "text": str(r.get("text") or ""),
        }
        for r in rows
    ]
    return {
        "doc_id": doc_id,
        "doc_name": str(rows[0].get("doc_name") or doc_id),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str) -> FileResponse:
    """업로드 시 보관한 원본 파일을 그대로 반환.

    저장 위치는 폴더별 버킷(__no_folder__ 또는 folder_id) 하위의 doc_id 디렉토리.
    """
    doc_dir = _find_doc_dir(doc_id)
    if doc_dir is None:
        raise HTTPException(status_code=404, detail=f"원본 파일이 없습니다: {doc_id}")
    files = [p for p in doc_dir.iterdir() if p.is_file()]
    if not files:
        raise HTTPException(status_code=404, detail=f"원본 파일이 비어있습니다: {doc_id}")
    target = files[0]
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


_RECHUNK_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_line(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/rechunk-stream")
async def rechunk_all_stream(request: Request) -> StreamingResponse:
    """CR-27: 모든 문서를 저장된 원본에서 현재 청킹 기준으로 재청크·재임베딩 (SSE).

    - 노트(__knowledge__)는 제외 — 노트 저장 훅이 자체 재임베딩한다.
    - 원본이 없는 문서는 스킵하고 보고한다 (기존 청크 유지).
    - doc_id·폴더 소속은 유지되므로 다운로드·이동 이력이 보존된다.
    """
    ctx = _get_context(request)
    rag = _require_rag(ctx)
    store = getattr(rag, "store", None) or getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    app_cfg = getattr(ctx, "app_config", None)
    chunk_chars = getattr(app_cfg, "rag_chunk_chars", _CHUNK_SIZE)
    overlap_chars = getattr(app_cfg, "rag_chunk_overlap", _CHUNK_OVERLAP)

    async def event_stream() -> Any:
        from document_ingest.segments import chunk_meta_segments
        from vector_search.types import DocumentChunk

        # 문서 인벤토리 수집 (doc_id → 이름·폴더·기존 청크 수)
        def _inventory() -> dict[str, dict[str, Any]]:
            tbl = getattr(store, "_tbl", None)
            rows = (
                tbl.search().select(["doc_id", "doc_name", "category"]).limit(200_000).to_list()
                if tbl is not None
                else []
            )
            docs: dict[str, dict[str, Any]] = {}
            for r in rows:
                did = str(r.get("doc_id") or "")
                cat = r.get("category")
                if not did or cat == "__knowledge__":
                    continue
                d = docs.setdefault(
                    did, {"doc_name": str(r.get("doc_name") or did), "category": cat, "old": 0}
                )
                d["old"] += 1
            return docs

        docs = await asyncio.to_thread(_inventory)
        total = len(docs)
        yield _sse_line({"stage": "start", "total": total})

        old_total = new_total = done = skipped = 0
        for doc_id, info in docs.items():
            done += 1
            folder = info["category"] or None
            original_dir = _folder_bucket(folder) / doc_id
            files = sorted(original_dir.iterdir()) if original_dir.is_dir() else []
            if not files:
                skipped += 1
                yield _sse_line(
                    {
                        "stage": "skip",
                        "i": done,
                        "total": total,
                        "doc_name": info["doc_name"],
                        "reason": "원본 없음 (기존 청크 유지)",
                    }
                )
                continue
            src = files[0]
            try:
                data = src.read_bytes()
                meta_segments = await _parse_isolated(src.name, data)
                chunk_metas = chunk_meta_segments(
                    meta_segments,
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                    topic_min_chars=getattr(app_cfg, "rag_topic_min_chars", 250),
                )
                if not chunk_metas:
                    raise ValueError("청킹 결과가 비어있음")

                def _reembed(
                    metas: list[tuple[str, int | None]] = chunk_metas,
                    did: str = doc_id,
                    name: str = info["doc_name"],
                    cat: Any = folder,
                ) -> None:
                    embedder = rag._embedder
                    texts = [c for c, _ in metas]
                    with _EMBED_LOCK:
                        vectors = embedder.embed_passages(texts)
                    doc_chunks = [
                        DocumentChunk(
                            doc_id=did,
                            doc_name=name,
                            category=cat,
                            page=seg_page,
                            section=None,
                            chunk_id=str(uuid.uuid4()),
                            text=_chunk_with_meta(chunk_text, name, seg_page),
                            bbox=None,
                            source_path="",
                        )
                        for chunk_text, seg_page in metas
                    ]
                    store.delete_by_doc_id(did)
                    store.upsert(doc_chunks, vectors)

                await asyncio.to_thread(_reembed)
                old_total += info["old"]
                new_total += len(chunk_metas)
                yield _sse_line(
                    {
                        "stage": "doc",
                        "i": done,
                        "total": total,
                        "doc_name": info["doc_name"],
                        "old": info["old"],
                        "new": len(chunk_metas),
                    }
                )
            except Exception as exc:
                skipped += 1
                logger.warning("재청킹 실패 (기존 청크 유지, doc_id=%s): %s", doc_id, exc)
                yield _sse_line(
                    {
                        "stage": "skip",
                        "i": done,
                        "total": total,
                        "doc_name": info["doc_name"],
                        "reason": str(exc)[:80],
                    }
                )

        _schedule_store_optimize(store)
        logger.info(
            "재청킹 완료: 문서 %d (스킵 %d), 청크 %d → %d",
            total,
            skipped,
            old_total,
            new_total,
        )
        yield _sse_line(
            {
                "stage": "done",
                "docs": total,
                "skipped": skipped,
                "old_total": old_total,
                "new_total": new_total,
            }
        )

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=_RECHUNK_SSE_HEADERS
    )


class MoveDocumentRequest(BaseModel):
    folder_id: str | None = None  # None = 미분류로 이동


@router.patch("/documents/{doc_id}")
async def move_document(request: Request, doc_id: str, body: MoveDocumentRequest) -> dict[str, Any]:
    """CR-24: 문서를 다른 폴더로 이동 — 청크 category 갱신 + 원본 디렉토리 이동."""
    ctx = _get_context(request)
    rag = _require_rag(ctx)
    store = getattr(rag, "store", None) or getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    new_folder = body.folder_id or None
    if new_folder is not None:
        _get_folder_or_404(new_folder)  # 존재하는 폴더인지 검증

    rows = await asyncio.to_thread(store.get_chunks_by_doc_id, doc_id, 1)
    if not rows:
        raise HTTPException(status_code=404, detail=f"doc_id '{doc_id}' not found")
    old_folder = rows[0].get("category") or None
    doc_name = str(rows[0].get("doc_name") or doc_id)

    if old_folder == new_folder:
        return {"ok": True, "doc_id": doc_id, "folder_id": new_folder, "moved_chunks": 0}

    try:
        moved = await asyncio.to_thread(store.update_doc_category, doc_id, new_folder)
    except Exception as exc:
        logger.error("move_document 청크 갱신 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"문서 이동 실패: {exc}") from exc

    # 원본 파일 디렉토리 이동 (부재 시 무시 — 청크가 정본)
    old_dir = _folder_bucket(old_folder) / doc_id
    if old_dir.is_dir():
        try:
            target_parent = _ensure_folder_dir(new_folder)
            shutil.move(str(old_dir), str(target_parent / doc_id))
        except Exception as exc:
            logger.warning("move_document 원본 이동 실패 (청크는 이동됨): %s", exc)

    # M_19: 그래프 Document 노드 category 동기화 (best-effort)
    graph_rag = getattr(ctx, "graph_rag_service", None)
    if graph_rag is not None:
        try:
            await asyncio.to_thread(
                graph_rag._graph.upsert_document, doc_id, doc_name, new_folder or ""
            )
        except Exception as exc:
            logger.debug("move_document 그래프 동기화 실패 (무시): %s", exc)

    logger.info("move_document: doc_id=%s, %r → %r (%d청크)", doc_id, old_folder, new_folder, moved)
    return {"ok": True, "doc_id": doc_id, "folder_id": new_folder, "moved_chunks": moved}


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(request: Request, doc_id: str) -> DeleteResponse:
    ctx = _get_context(request)
    rag = _require_rag(ctx)

    store = getattr(rag, "store", None) or getattr(rag, "_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    try:
        deleted = store.delete_by_doc_id(doc_id)
    except Exception as exc:
        logger.error("delete_document error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"doc_id '{doc_id}' not found")

    # 청크 삭제 성공 시 원본도 정리 (디렉토리 부재 OK)
    _delete_original(doc_id)
    _schedule_store_optimize(store)

    # M_19: 그래프에서도 연쇄 삭제 (활성 시)
    graph_rag = getattr(ctx, "graph_rag_service", None)
    if graph_rag is not None:
        await asyncio.to_thread(graph_rag.delete_document, doc_id)

    logger.info("delete_document: doc_id=%s, deleted=%d", doc_id, deleted)
    return DeleteResponse(ok=True, deleted_chunks=deleted)

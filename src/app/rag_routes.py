from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from starlette.datastructures import UploadFile as StarletteUploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".pptx", ".hwpx", ".markdown"}
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50

_ROOT = Path(os.environ.get("SAESSAGI_ROOT", "."))
_FOLDERS_FILE = _ROOT / "data" / "rag_folders.json"


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
            return json.loads(_FOLDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_folders(folders: list[dict[str, str]]) -> None:
    _FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FOLDERS_FILE.write_text(
        json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _chunk_with_meta(text: str, doc_name: str, page: int | None) -> str:
    """청크 텍스트 앞에 출처 메타정보를 삽입해 LLM이 자연스럽게 인식하도록 한다."""
    if page is not None:
        return f"[출처: {doc_name}, {page}페이지] {text}"
    return f"[출처: {doc_name}] {text}"


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return [c for c in chunks if c.strip()]


def _parse_to_meta_segments(
    filename: str, data: bytes
) -> list[tuple[str, int | None]]:
    """파서를 호출해 (text, page) 튜플 목록을 반환한다.

    - PDF/PPTX : page = 실제 페이지/슬라이드 번호(1-based).
    - HWPX/DOCX/TXT/MD : page = None (페이지 개념 없음).
    - 알 수 없는 확장자 : 전체 텍스트를 page=None 1건으로 반환.
    """
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        from document_ingest.parsers.docx import _parse_docx
        from document_ingest.parsers.hwpx import _parse_hwpx
        from document_ingest.parsers.md import _parse_md
        from document_ingest.parsers.pdf import _parse_pdf
        from document_ingest.parsers.pptx import _parse_pptx
        from document_ingest.parsers.txt import _parse_txt

        parser_map = {
            ".pdf": _parse_pdf,
            ".docx": _parse_docx,
            ".pptx": _parse_pptx,
            ".hwpx": _parse_hwpx,
            ".txt": _parse_txt,
            ".md": _parse_md,
            ".markdown": _parse_md,
        }

        parser = parser_map.get(suffix)
        if parser is None:
            raw = data.decode("utf-8", errors="replace").strip()
            return [(raw, None)] if raw else []

        return [
            (seg.text.strip(), seg.page)
            for seg in parser(tmp_path)
            if seg.text.strip()
        ]
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _list_documents_from_store(store: Any) -> list[DocumentInfo]:
    try:
        import pyarrow as pa

        arrow_tbl: pa.Table = store._tbl.to_arrow()
        if arrow_tbl.num_rows == 0:
            return []

        doc_ids = arrow_tbl.column("doc_id").to_pylist()
        doc_names = arrow_tbl.column("doc_name").to_pylist()

        # category 컬럼은 없을 수도 있음 (구 스키마 호환)
        categories: list[Any]
        if "category" in arrow_tbl.schema.names:
            categories = arrow_tbl.column("category").to_pylist()
        else:
            categories = [None] * arrow_tbl.num_rows

        seen: dict[str, DocumentInfo] = {}
        for doc_id, doc_name, category in zip(doc_ids, doc_names, categories):
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
            # 청크 삭제 실패 시 폴더 레지스트리 삭제도 중단 (고아 청크 방지)
            import pyarrow as pa

            arrow_tbl: pa.Table = store._tbl.to_arrow()
            if arrow_tbl.num_rows > 0 and "category" in arrow_tbl.schema.names:
                doc_ids_col = arrow_tbl.column("doc_id").to_pylist()
                categories_col = arrow_tbl.column("category").to_pylist()
                target_doc_ids = {
                    doc_id
                    for doc_id, cat in zip(doc_ids_col, categories_col)
                    if cat == folder_id
                }
                for did in target_doc_ids:
                    deleted_chunks += store.delete_by_doc_id(did)

    folders = _load_folders()
    _save_folders([f for f in folders if f["folder_id"] != folder_id])
    logger.info("delete_folder: %s, deleted_chunks=%d", folder_id, deleted_chunks)
    return {"ok": True, "folder_id": folder_id, "deleted_chunks": deleted_chunks}


# ---------- document endpoints ----------


_MAX_UPLOAD_PART_BYTES = 50 * 1024 * 1024  # 50 MB (Starlette 기본 1 MB 한도 우회)


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

    ctx = _get_context(request)
    rag = _require_rag(ctx)

    filename = file.filename or "upload"
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"허용되지 않은 파일 형식: {suffix}. 허용: {', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    # folder_id 유효성 확인
    if folder_id is not None:
        _get_folder_or_404(folder_id)

    data = await file.read()
    try:
        meta_segments = _parse_to_meta_segments(filename, data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"텍스트 추출 실패: {exc}")

    if not meta_segments:
        raise HTTPException(status_code=422, detail="문서에서 텍스트를 추출할 수 없습니다")

    # 각 세그먼트를 청킹하되 page 메타를 청크마다 보존
    chunk_metas: list[tuple[str, int | None]] = []
    for seg_text, seg_page in meta_segments:
        for c in _chunk_text(seg_text):
            chunk_metas.append((c, seg_page))

    if not chunk_metas:
        raise HTTPException(status_code=422, detail="청킹 결과가 비어있습니다")

    doc_id = f"{filename}_{uuid.uuid4().hex[:8]}"

    try:
        from vector_search.types import DocumentChunk

        embedder = rag._embedder
        texts = [c for c, _ in chunk_metas]
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

        store = getattr(rag, "_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="vector store unavailable")
        store.upsert(doc_chunks, vectors)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("upload_document error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info("upload_document: doc_id=%s, chunks=%d, folder_id=%s", doc_id, len(chunk_metas), folder_id)
    return UploadResponse(doc_id=doc_id, filename=filename, chunk_count=len(chunk_metas), folder_id=folder_id)


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

    return _list_documents_from_store(store)


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

    logger.info("delete_document: doc_id=%s, deleted=%d", doc_id, deleted)
    return DeleteResponse(ok=True, deleted_chunks=deleted)

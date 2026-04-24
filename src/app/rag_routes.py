from __future__ import annotations

import logging
import uuid
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf"}
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50


# ---------- Pydantic models ----------


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    chunk_count: int


class UploadResponse(BaseModel):
    doc_id: str
    filename: str
    chunk_count: int


class DeleteResponse(BaseModel):
    ok: bool
    deleted_chunks: int


# ---------- helpers ----------


def _get_context(request: Request) -> Any:
    return request.app.state.service_context


def _require_rag(ctx: Any) -> Any:
    svc = getattr(ctx, "rag_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="rag_service unavailable")
    return svc


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


def _extract_text(filename: str, data: bytes) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == ".pdf":
        try:
            import io

            import pypdf

            reader = pypdf.PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raise HTTPException(status_code=422, detail="PDF 지원 불가: pypdf 패키지 미설치")
    return data.decode("utf-8", errors="replace")


def _list_documents_from_store(store: Any) -> list[DocumentInfo]:
    """VectorStore에서 distinct doc_id 목록을 직접 조회."""
    try:
        import pyarrow as pa

        arrow_tbl: pa.Table = store._tbl.to_arrow()
        if arrow_tbl.num_rows == 0:
            return []

        doc_ids = arrow_tbl.column("doc_id").to_pylist()
        doc_names = arrow_tbl.column("doc_name").to_pylist()

        seen: dict[str, DocumentInfo] = {}
        for doc_id, doc_name in zip(doc_ids, doc_names):
            if doc_id not in seen:
                seen[doc_id] = DocumentInfo(doc_id=doc_id, filename=doc_name, chunk_count=0)
            seen[doc_id] = DocumentInfo(
                doc_id=doc_id,
                filename=seen[doc_id].filename,
                chunk_count=seen[doc_id].chunk_count + 1,
            )
        return list(seen.values())
    except Exception as exc:
        logger.error("list_documents_from_store error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------- endpoints ----------


@router.post("/documents", response_model=UploadResponse, status_code=201)
async def upload_document(request: Request, file: UploadFile) -> UploadResponse:
    ctx = _get_context(request)
    rag = _require_rag(ctx)

    filename = file.filename or "upload"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"허용되지 않은 파일 형식: {suffix}. 허용: {', '.join(_ALLOWED_SUFFIXES)}",
        )

    data = await file.read()
    text = _extract_text(filename, data)

    if not text.strip():
        raise HTTPException(status_code=422, detail="문서에서 텍스트를 추출할 수 없습니다")

    chunks = _chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=422, detail="청킹 결과가 비어있습니다")

    doc_id = f"{filename}_{uuid.uuid4().hex[:8]}"

    try:
        from vector_search.types import DocumentChunk

        embedder = rag._embedder
        vectors: np.ndarray[Any, np.dtype[np.float32]] = embedder.embed_passages(chunks)

        doc_chunks = [
            DocumentChunk(
                doc_id=doc_id,
                doc_name=filename,
                category=None,
                page=None,
                section=None,
                chunk_id=str(uuid.uuid4()),
                text=chunk,
                bbox=None,
                source_path="",
            )
            for chunk in chunks
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

    logger.info("upload_document: doc_id=%s, chunks=%d", doc_id, len(chunks))
    return UploadResponse(doc_id=doc_id, filename=filename, chunk_count=len(chunks))


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
                    doc_id=d["doc_id"], filename=d["filename"], chunk_count=d["chunk_count"]
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

# src/vector_search/store.py
"""LanceDB 기반 벡터 저장소 (M_07 §4.6, §5, §6.2)."""

from __future__ import annotations

import logging
from typing import Any, Literal

import numpy as np
import pyarrow as pa

from .errors import VectorStoreError
from .schema import CHUNKS_SCHEMA, EMBEDDING_DIM
from .types import DocumentChunk, SearchHit

logger = logging.getLogger(__name__)


def _escape_category(value: str) -> str:
    """category 값의 single quote를 double-escape해 SQL-like 인젝션 차단 (스펙 §6.2.2)."""
    # ASCII 제어 문자(0x00~0x1F) 포함 시 에러
    for ch in value:
        if ord(ch) < 0x20:
            raise VectorStoreError(f"invalid category: 제어 문자 포함 (ord={ord(ch)})")
    if len(value) > 100:
        raise VectorStoreError(f"invalid category: 길이 초과 ({len(value)} > 100)")
    return value.replace("'", "''")


def _chunk_to_row(chunk: DocumentChunk, vector: np.ndarray) -> dict[str, Any]:
    """DocumentChunk + vector → LanceDB row dict."""
    bbox_val: list[float] | None = list(chunk.bbox) if chunk.bbox is not None else None
    return {
        "doc_id": chunk.doc_id,
        "doc_name": chunk.doc_name,
        "category": chunk.category,
        "page": chunk.page,
        "section": chunk.section,
        "chunk_id": chunk.chunk_id,
        "text": chunk.text,
        "bbox": bbox_val,
        "source_path": chunk.source_path,
        "vector": vector.tolist(),
    }


def _row_to_search_hit(row: dict[str, Any]) -> SearchHit:
    """LanceDB 검색 결과 row → SearchHit. 점수 정규화 포함 (스펙 §6.2.1)."""
    distance = float(row.get("_distance", 0.0))
    score = max(0.0, min(1.0, 1.0 - distance / 2.0))

    raw_bbox = row.get("bbox")
    bbox: tuple[float, float, float, float] | None = None
    if raw_bbox is not None:
        try:
            floats = [float(v) for v in raw_bbox]
            if len(floats) == 4:
                bbox = (floats[0], floats[1], floats[2], floats[3])
        except (TypeError, ValueError):
            bbox = None

    # page는 numpy int32일 수 있으므로 int()로 변환, None은 유지
    raw_page = row.get("page")
    page: int | None = int(raw_page) if raw_page is not None else None

    return SearchHit(
        doc_id=str(row.get("doc_id", "")),
        doc_name=str(row.get("doc_name", "")),
        category=row.get("category") or None,
        page=page,
        section=row.get("section") or None,
        chunk_id=str(row.get("chunk_id", "")),
        text=str(row.get("text", "")),
        bbox=bbox,
        source_path=str(row.get("source_path", "")),
        score=score,
    )


class VectorStore:
    """LanceDB 기반 청크 저장소.

    동기 API로 확정. LanceDB의 `connect` 및 `add`/`merge_insert`/`search`는 sync 메서드이며,
    본 모듈은 async 래핑을 하지 않는다.

    Args:
        db_path: LanceDB 디렉토리 경로. 부재 시 자동 생성.
        table:   테이블명. 기본 "chunks".

    Raises:
        VectorStoreError: db_path 접근 실패, 기존 테이블의 vector 차원이 1024가 아닐 때.
    """

    def __init__(self, db_path: str, table: str = "chunks") -> None:
        try:
            import lancedb

            self._db = lancedb.connect(db_path)
        except Exception as exc:
            raise VectorStoreError(f"LanceDB 연결 실패: {db_path}: {exc}") from exc

        self._table_name = table
        self._tbl = self._open_or_create_table()
        logger.info("VectorStore 초기화 완료: db_path=%s, table=%s", db_path, table)

    def _open_or_create_table(self) -> Any:
        """테이블 열기 또는 생성 (스펙 §5.4)."""
        # LanceDB 0.30.x: list_tables() returns ListTablesResponse with .tables attribute.
        # table_names() is deprecated but simpler. Use list_tables().tables for forward compat.
        resp = self._db.list_tables()
        existing_tables: list[str] = resp.tables if hasattr(resp, "tables") else list(resp)
        if self._table_name in existing_tables:
            tbl = self._db.open_table(self._table_name)
            # 스키마 검증: vector 필드 차원 확인
            schema = tbl.schema
            try:
                vector_field = schema.field("vector")
                vec_type = vector_field.type
                # list_(float32, 1024) 검증 — list_size 속성으로 확인
                if hasattr(vec_type, "list_size"):
                    dim = vec_type.list_size
                elif hasattr(vec_type, "value_type"):
                    dim = getattr(vec_type, "list_size", None)
                    if dim is None:
                        # 일반 list 타입인 경우 — dimension 알 수 없음, 허용
                        dim = EMBEDDING_DIM
                else:
                    dim = EMBEDDING_DIM

                if dim != EMBEDDING_DIM:
                    raise VectorStoreError(
                        f"schema mismatch: migration required "
                        f"(기존 vector 차원={dim}, 기대={EMBEDDING_DIM})"
                    )
            except KeyError:
                raise VectorStoreError("schema mismatch: vector 필드가 없습니다")
            logger.debug("기존 테이블 열기: %s", self._table_name)
            return tbl
        else:
            tbl = self._db.create_table(self._table_name, schema=CHUNKS_SCHEMA, mode="create")
            logger.debug("새 테이블 생성: %s", self._table_name)
            return tbl

    def upsert(
        self,
        chunks: list[DocumentChunk],
        vectors: np.ndarray,  # shape (len(chunks), 1024) float32
    ) -> int:
        """chunk_id 기준 멱등 upsert (스펙 §4.6, §5.3).

        Returns:
            실제 written row 수(신규 insert + 업데이트 합).

        Raises:
            VectorStoreError: 길이/차원 불일치, Arrow 스키마 충돌.
        """
        if len(chunks) == 0:
            logger.debug("upsert: 빈 입력, 0 반환")
            return 0

        if vectors.shape[0] != len(chunks):
            raise VectorStoreError(
                f"upsert chunks/vectors 길이 불일치: "
                f"chunks={len(chunks)}, vectors.shape[0]={vectors.shape[0]}"
            )

        if len(vectors.shape) < 2 or vectors.shape[1] != EMBEDDING_DIM:
            dim = vectors.shape[1] if len(vectors.shape) >= 2 else vectors.shape[0]
            raise VectorStoreError(f"upsert vectors 차원 불일치: 기대={EMBEDDING_DIM}, 실제={dim}")

        # dtype cast (warning 없이 허용)
        vecs = vectors.astype(np.float32, copy=False)

        rows = [_chunk_to_row(chunk, vecs[i]) for i, chunk in enumerate(chunks)]

        try:
            arrow_table = pa.table(
                {
                    "doc_id": [r["doc_id"] for r in rows],
                    "doc_name": [r["doc_name"] for r in rows],
                    "category": pa.array([r["category"] for r in rows], type=pa.string()),
                    "page": pa.array([r["page"] for r in rows], type=pa.int32()),
                    "section": pa.array([r["section"] for r in rows], type=pa.string()),
                    "chunk_id": [r["chunk_id"] for r in rows],
                    "text": [r["text"] for r in rows],
                    "bbox": pa.array(
                        [r["bbox"] for r in rows],
                        type=pa.list_(pa.float32(), 4),
                    ),
                    "source_path": [r["source_path"] for r in rows],
                    "vector": pa.array(
                        [r["vector"] for r in rows],
                        type=pa.list_(pa.float32(), EMBEDDING_DIM),
                    ),
                },
                schema=CHUNKS_SCHEMA,
            )
        except Exception as exc:
            raise VectorStoreError(f"Arrow 테이블 빌드 실패: {exc}") from exc

        try:
            (
                self._tbl.merge_insert("chunk_id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(arrow_table)
            )
        except Exception as exc:
            raise VectorStoreError(f"merge_insert 실패: {exc}") from exc

        logger.debug("upsert 완료: %d개 청크", len(chunks))
        return len(chunks)

    def search(
        self,
        query_vec: np.ndarray,  # shape (1024,) float32
        top_k: int = 8,
        category: str | None = None,
        source: Literal["docs", "notes", "both"] = "both",  # M_16: 소스 필터 (신규)
    ) -> list[SearchHit]:
        """코사인 유사도 ANN 검색 (스펙 §4.6, §6.2).

        Args:
            query_vec: 쿼리 임베딩 벡터 shape (1024,).
            top_k: 반환할 최대 결과 수.
            category: 기존 정확일치 필터 (호환 유지).
            source: M_16 소스 필터.
                "docs"  → 노트 제외 전부  (category IS NULL OR category != '__knowledge__')
                "notes" → 노트만          (category = '__knowledge__')
                "both"  → 필터 없음 (기본값, 현행 동작 유지)

        Returns:
            cosine similarity 내림차순 정렬된 SearchHit 리스트. 결과 0건 → [].

        Raises:
            VectorStoreError: query_vec shape 불일치, category 제어 문자.
            ValueError: top_k <= 0.
        """
        if query_vec.shape != (EMBEDDING_DIM,):
            raise VectorStoreError(
                f"query_vec shape 불일치: 기대=({EMBEDDING_DIM},), 실제={query_vec.shape}"
            )

        if top_k <= 0:
            logger.warning("search top_k <= 0: %d", top_k)
            raise ValueError(f"top_k must be >= 1, got {top_k}")

        if top_k > 50:
            logger.warning("search top_k > 50, clamp to 50 (요청=%d)", top_k)
            top_k = 50

        # M_16: knowledge category 상수 (src/knowledge/service.py:19 참조)
        _KNOWLEDGE_CATEGORY = "__knowledge__"

        try:
            q = (
                self._tbl.search(query_vec.tolist(), vector_column_name="vector")
                .metric("cosine")
                .limit(top_k)
            )

            # where 절 구성: category 정확일치 + source 필터 AND 직교
            clauses: list[str] = []

            if category is not None:
                escaped = _escape_category(category)
                clauses.append(f"category = '{escaped}'")  # 기존 라인 유지

            if source == "notes":
                escaped_kc = _escape_category(_KNOWLEDGE_CATEGORY)
                clauses.append(f"category = '{escaped_kc}'")
            elif source == "docs":
                escaped_kc = _escape_category(_KNOWLEDGE_CATEGORY)
                clauses.append(f"(category IS NULL OR category != '{escaped_kc}')")
            # source == "both": 절 추가 없음 (현행)

            if clauses:
                q = q.where(" AND ".join(clauses))

            rows: list[dict[str, Any]] = q.to_list()
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"LanceDB search 실패: {exc}") from exc

        hits: list[SearchHit] = [_row_to_search_hit(row) for row in rows]

        logger.debug(
            "search 완료: top_k=%d, category=%s, source=%s, 결과=%d건",
            top_k,
            category,
            source,
            len(hits),
        )
        return hits

    def get_chunks_by_doc_id(self, doc_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """특정 문서의 청크를 처음 N개까지 가져온다.

        벡터 검색 없이 doc_id 정확 매칭. 첨부 파일 내용을 LLM 컨텍스트로
        자동 주입할 때 사용 — 사용자가 첨부한 파일의 내용을 LLM이 자연스럽게 본다.
        """
        try:
            escaped = doc_id.replace("'", "''")
            rows = self._tbl.search().where(f"doc_id = '{escaped}'").limit(limit).to_list()
            return rows
        except Exception as exc:
            logger.warning("get_chunks_by_doc_id 실패 (doc_id=%s): %s", doc_id, exc)
            return []

    def delete_by_doc_id(self, doc_id: str) -> int:
        """특정 문서의 모든 청크 삭제 (스펙 §4.6).

        Returns:
            삭제된 row 수 (존재하지 않으면 0).

        Raises:
            VectorStoreError: 삭제 실패.
        """
        try:
            escaped = doc_id.replace("'", "''")
            # 삭제 전 개수 확인 — to_arrow()로 pandas 없이 행 수 확인
            arrow_tbl: pa.Table = self._tbl.to_arrow()
            doc_ids = arrow_tbl.column("doc_id").to_pylist()
            before_count: int = int(doc_ids.count(doc_id))

            if before_count == 0:
                logger.debug("delete_by_doc_id: doc_id=%s 존재하지 않음, 0 반환", doc_id)
                return 0

            self._tbl.delete(f"doc_id = '{escaped}'")
            logger.debug("delete_by_doc_id: doc_id=%s, 삭제=%d건", doc_id, before_count)
            return before_count
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"delete_by_doc_id 실패: {exc}") from exc

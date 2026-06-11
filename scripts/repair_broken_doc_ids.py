# scripts/repair_broken_doc_ids.py
"""E-33 일회성 데이터 수리: surrogate/HTML-entity가 섞인 doc_id 정리.

- '23.'으로 시작하는 미분류 문서: 사용자 요청으로 삭제.
- 나머지 미분류 문서 2건: doc_id/doc_name/text를 정상 문자열로 수리 후 재-upsert.
- data/rag_originals/__no_folder__/ 하위 원본 디렉토리도 동기화.

실행: PYTHONPATH=src uv run python scripts/repair_broken_doc_ids.py
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

import numpy as np

_INVALID_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def repair_bytes(s: str) -> str:
    """surrogateescape로 깨진 문자열을 원래 UTF-8 텍스트로 복구."""
    try:
        return s.encode("utf-8", "surrogateescape").decode("utf-8")
    except UnicodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def fix_name(s: str) -> str:
    """바이트 복구 + HTML 엔티티 디코딩 + Windows 금지문자 제거."""
    return _INVALID_RE.sub("", html.unescape(repair_bytes(s))).strip()


def safe(s: str) -> str:
    """콘솔 출력용 (cp949 콘솔에서도 안 깨지게)."""
    return s.encode("ascii", "backslashreplace").decode()


def main() -> int:
    from vector_search.store import VectorStore
    from vector_search.types import DocumentChunk

    store = VectorStore(db_path="data/vector_store")
    tbl = store._tbl
    arrow = tbl.to_arrow()

    doc_ids = arrow.column("doc_id").to_pylist()
    cats = arrow.column("category").to_pylist()

    # 미분류(category None) 문서 수집
    targets: dict[str, int] = {}
    for d, c in zip(doc_ids, cats):
        if c is None:
            targets[d] = targets.get(d, 0) + 1

    print(f"misc docs: {len(targets)}")
    originals = Path("data/rag_originals/__no_folder__")

    for old_id, cnt in targets.items():
        fixed_id = fix_name(old_id)
        is_delete = fixed_id.startswith("23.")
        print(f"- {safe(fixed_id[:50])} chunks={cnt} -> {'DELETE' if is_delete else 'REPAIR'}")

        # 해당 문서의 row 인덱스
        idxs = [i for i, d in enumerate(doc_ids) if d == old_id]

        # 1) 기존 청크 삭제 — doc_id에 surrogate가 있어 SQL 문자열 비교가 불가할 수
        #    있으므로 ASCII-safe한 chunk_id(UUID)로 삭제한다.
        chunk_ids = [arrow.column("chunk_id")[i].as_py() for i in idxs]
        for batch_start in range(0, len(chunk_ids), 200):
            batch = chunk_ids[batch_start : batch_start + 200]
            in_list = ", ".join(f"'{c}'" for c in batch)
            tbl.delete(f"chunk_id IN ({in_list})")
        print(f"  deleted {len(chunk_ids)} chunks")

        # 원본 디렉토리 찾기 (이름도 깨져 있을 수 있어 fix_name으로 비교)
        orig_dir = None
        if originals.is_dir():
            for child in originals.iterdir():
                if child.name == old_id or fix_name(child.name) == fixed_id:
                    orig_dir = child
                    break

        if is_delete:
            if orig_dir is not None:
                shutil.rmtree(orig_dir, ignore_errors=True)
                print("  originals removed")
            continue

        # 2) 수리 후 재-upsert
        new_name = fix_name(arrow.column("doc_name")[idxs[0]].as_py())
        chunks: list[DocumentChunk] = []
        vecs: list[list[float]] = []
        for i in idxs:
            row_text = repair_bytes(arrow.column("text")[i].as_py())
            page_v = arrow.column("page")[i].as_py()
            chunks.append(
                DocumentChunk(
                    doc_id=fixed_id,
                    doc_name=new_name,
                    category=None,
                    page=int(page_v) if page_v is not None else None,
                    section=arrow.column("section")[i].as_py(),
                    chunk_id=arrow.column("chunk_id")[i].as_py(),
                    text=row_text,
                    bbox=None,
                    source_path="",
                )
            )
            vecs.append(arrow.column("vector")[i].as_py())

        n = store.upsert(chunks, np.asarray(vecs, dtype=np.float32))
        print(f"  re-upserted {n} chunks as {safe(fixed_id[:50])}")

        # 원본 디렉토리/파일명도 새 doc_id로 정리
        if orig_dir is not None:
            new_dir = originals / fixed_id
            if orig_dir != new_dir:
                orig_dir.rename(new_dir)
            for f in new_dir.iterdir():
                fixed_fname = fix_name(f.name)
                if f.name != fixed_fname:
                    f.rename(new_dir / fixed_fname)
            print("  originals renamed")

    return 0


if __name__ == "__main__":
    sys.exit(main())

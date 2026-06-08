"""KnowledgeService — markdown 노트 CRUD + RAG 임베딩 + 그래프 빌드."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .parser import ParsedNote, extract_wikilinks, parse, serialize

logger = logging.getLogger(__name__)

KNOWLEDGE_CATEGORY = "__knowledge__"
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50


@dataclass
class KnowledgeNoteMeta:
    slug: str
    title: str
    tags: list[str] = field(default_factory=list)
    related_docs: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""


@dataclass
class KnowledgeNote(KnowledgeNoteMeta):
    content: str = ""


@dataclass
class KnowledgeGraphEdge:
    source: str
    target: str
    kind: str  # "wikilink" | "tag" | "doc"


@dataclass
class KnowledgeGraph:
    nodes: list[dict[str, Any]]
    edges: list[KnowledgeGraphEdge]


# ── slugify ──────────────────────────────────────────────────────────────────

_SLUG_KEEP_RE = re.compile(r"[^\w가-힣\-]+", flags=re.UNICODE)


def _slugify(title: str) -> str:
    s = title.strip().replace(" ", "-")
    s = _SLUG_KEEP_RE.sub("", s)
    s = s.strip("-").lower() if s.isascii() else s.strip("-")
    if not s:
        s = f"note-{uuid.uuid4().hex[:8]}"
    return s


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return [c for c in chunks if c.strip()]


class KnowledgeService:
    """노트 CRUD + RAG 임베딩 + 그래프 빌드."""

    def __init__(self, root: Path | str, rag_service: Any | None = None) -> None:
        self._root_base = Path(root)
        self._root = Path(root) / "data" / "knowledge"
        self._root.mkdir(parents=True, exist_ok=True)
        self._originals_dir = Path(root) / "data" / "rag_originals"
        self._rag = rag_service

    # ── related_docs filename 조회 ──

    def resolve_related_docs(self, related_docs: list[str]) -> list[dict[str, str | None]]:
        """doc_id 목록 → [{id, filename}] 매핑.

        rag_originals/<bucket>/<doc_id>/<filename> 디렉토리를 글로빙해 filename 조회.
        파일이 없으면 filename=None으로 반환 (UI에서 doc_id로 표시).
        """
        if not related_docs:
            return []
        result: list[dict[str, str | None]] = []
        for doc_id in related_docs:
            filename = self._find_filename(doc_id)
            result.append({"id": doc_id, "filename": filename})
        return result

    def _find_filename(self, doc_id: str) -> str | None:
        if not self._originals_dir.is_dir():
            return None
        for bucket in self._originals_dir.iterdir():
            if not bucket.is_dir():
                continue
            candidate = bucket / doc_id
            if candidate.is_dir():
                files = [p for p in candidate.iterdir() if p.is_file()]
                if files:
                    return files[0].name
        return None

    # ── 경로 ──

    def _path(self, slug: str) -> Path:
        return self._root / f"{slug}.md"

    def _doc_id(self, slug: str) -> str:
        return f"{KNOWLEDGE_CATEGORY}:{slug}"

    # ── slug 충돌 회피 ──

    def _unique_slug(self, base: str) -> str:
        if not self._path(base).exists():
            return base
        i = 2
        while True:
            candidate = f"{base}-{i}"
            if not self._path(candidate).exists():
                return candidate
            i += 1

    # ── 읽기 ──

    def _read_note(self, slug: str) -> KnowledgeNote | None:
        p = self._path(slug)
        if not p.exists():
            return None
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("note read failed (slug=%s): %s", slug, exc)
            return None
        parsed: ParsedNote = parse(text)
        fm = parsed.frontmatter
        return KnowledgeNote(
            slug=str(fm.get("slug", slug)),
            title=str(fm.get("title", slug)),
            tags=list(fm.get("tags") or []),
            related_docs=list(fm.get("related_docs") or []),
            created=str(fm.get("created", "")),
            updated=str(fm.get("updated", "")),
            content=parsed.body,
        )

    def list_notes(self) -> list[KnowledgeNoteMeta]:
        out: list[KnowledgeNoteMeta] = []
        for p in sorted(self._root.glob("*.md")):
            slug = p.stem
            note = self._read_note(slug)
            if note is None:
                logger.warning("skipping unreadable note: %s", p.name)
                continue
            out.append(
                KnowledgeNoteMeta(
                    slug=note.slug,
                    title=note.title,
                    tags=note.tags,
                    related_docs=note.related_docs,
                    created=note.created,
                    updated=note.updated,
                )
            )
        # 최신 수정 순
        out.sort(key=lambda n: n.updated or n.created, reverse=True)
        return out

    def get_note(self, slug: str) -> KnowledgeNote | None:
        return self._read_note(slug)

    # ── 쓰기 ──

    def create_note(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        related_docs: list[str] | None = None,
    ) -> KnowledgeNote:
        base = _slugify(title)
        slug = self._unique_slug(base)
        now = datetime.now().isoformat(timespec="seconds")
        fm = {
            "title": title,
            "slug": slug,
            "created": now,
            "updated": now,
            "tags": tags or [],
            "related_docs": related_docs or [],
        }
        self._path(slug).write_text(serialize(fm, content), encoding="utf-8")
        self._reembed(slug, title, content)
        return KnowledgeNote(
            slug=slug,
            title=title,
            tags=tags or [],
            related_docs=related_docs or [],
            created=now,
            updated=now,
            content=content,
        )

    def update_note(
        self,
        slug: str,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        related_docs: list[str] | None = None,
    ) -> KnowledgeNote | None:
        existing = self._read_note(slug)
        if existing is None:
            return None
        new_title = title if title is not None else existing.title
        new_content = content if content is not None else existing.content
        new_tags = tags if tags is not None else existing.tags
        new_docs = related_docs if related_docs is not None else existing.related_docs
        now = datetime.now().isoformat(timespec="seconds")
        fm = {
            "title": new_title,
            "slug": slug,
            "created": existing.created or now,
            "updated": now,
            "tags": new_tags,
            "related_docs": new_docs,
        }
        self._path(slug).write_text(serialize(fm, new_content), encoding="utf-8")
        # 본문이나 제목이 바뀌었으면 재임베딩
        if content is not None or title is not None:
            self._reembed(slug, new_title, new_content)
        return KnowledgeNote(
            slug=slug,
            title=new_title,
            tags=new_tags,
            related_docs=new_docs,
            created=existing.created or now,
            updated=now,
            content=new_content,
        )

    def delete_note(self, slug: str) -> bool:
        p = self._path(slug)
        if not p.exists():
            return False
        # 청크 먼저 삭제 — 실패해도 md는 지운다(고아 청크는 다음 재임베딩에서 사라짐)
        try:
            store = self._store()
            if store is not None:
                store.delete_by_doc_id(self._doc_id(slug))
        except Exception as exc:
            logger.warning("knowledge chunk delete failed (slug=%s): %s", slug, exc)
        p.unlink()
        return True

    # ── RAG 임베딩 ──

    def _store(self) -> Any | None:
        if self._rag is None:
            return None
        return getattr(self._rag, "store", None) or getattr(self._rag, "_store", None)

    def _embedder(self) -> Any | None:
        if self._rag is None:
            return None
        return getattr(self._rag, "_embedder", None) or getattr(self._rag, "embedder", None)

    def _reembed(self, slug: str, title: str, body: str) -> None:
        """기존 chunks 제거 후 본문을 다시 청킹·임베딩."""
        store = self._store()
        embedder = self._embedder()
        if store is None or embedder is None:
            logger.debug("knowledge reembed skipped (rag unavailable): slug=%s", slug)
            return

        doc_id = self._doc_id(slug)
        try:
            store.delete_by_doc_id(doc_id)
        except Exception as exc:
            logger.warning("knowledge prior delete failed (slug=%s): %s", slug, exc)

        chunks = _chunk_text(body)
        if not chunks:
            return

        try:
            from vector_search.types import DocumentChunk

            texts = [f"[출처: 업무노트/{title}] {c}" for c in chunks]
            vectors = embedder.embed_passages(texts)
            doc_chunks = [
                DocumentChunk(
                    doc_id=doc_id,
                    doc_name=title,
                    category=KNOWLEDGE_CATEGORY,
                    page=None,
                    section=None,
                    chunk_id=str(uuid.uuid4()),
                    text=t,
                    bbox=None,
                    source_path="",
                )
                for t in texts
            ]
            store.upsert(doc_chunks, vectors)
            logger.info("knowledge reembed: slug=%s, chunks=%d", slug, len(chunks))
        except Exception as exc:
            logger.error("knowledge reembed failed (slug=%s): %s", slug, exc)

    # ── 그래프 ──

    def build_graph(self) -> KnowledgeGraph:
        notes = [self._read_note(p.stem) for p in self._root.glob("*.md")]
        notes = [n for n in notes if n is not None]

        nodes: list[dict[str, Any]] = [
            {"slug": n.slug, "title": n.title, "tags": n.tags} for n in notes
        ]

        edges: list[KnowledgeGraphEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edge(a: str, b: str, kind: str) -> None:
            if a == b:
                return
            key = (min(a, b), max(a, b), kind)
            if key in seen:
                return
            seen.add(key)
            edges.append(KnowledgeGraphEdge(source=a, target=b, kind=kind))

        # wikilink: 본문에서 [[other-slug]] 발견
        slug_set = {n.slug for n in notes}
        for n in notes:
            for target in extract_wikilinks(n.content):
                if target in slug_set:
                    add_edge(n.slug, target, "wikilink")

        # tag 공유
        for i, n1 in enumerate(notes):
            for n2 in notes[i + 1 :]:
                if set(n1.tags) & set(n2.tags):
                    add_edge(n1.slug, n2.slug, "tag")

        # doc 공유
        for i, n1 in enumerate(notes):
            for n2 in notes[i + 1 :]:
                if set(n1.related_docs) & set(n2.related_docs):
                    add_edge(n1.slug, n2.slug, "doc")

        return KnowledgeGraph(nodes=nodes, edges=edges)

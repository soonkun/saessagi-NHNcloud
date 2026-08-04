# src/kg/candidates.py
"""M_23 후보 저장소 — LLM 결과가 Neo4j 정규 노드가 되기 전에 머무는 곳 (스펙 §5.1).

**이 단계를 건너뛰는 경로는 두지 않는다.** LLM은 후보를 제안할 뿐이고, 정규 노드는 검증·
정규화를 통과한 것만 만든다. 그래서 후보는 그래프가 아니라 별도 SQLite에 쌓는다.

SQLite를 쓰는 이유:
- 실패·재시도·검토 대기 같은 중간 상태를 그래프에 노출하지 않는다.
- 재실행할 때 이미 뽑아 둔 후보를 그대로 써서 **LLM 호출을 건너뛴다** (수십 시간짜리
  작업이라 이게 없으면 중단이 곧 처음부터 다시다).
- 이 프로젝트는 이미 sqlite3(calendar.db)를 쓰고 있어 새 의존성이 없다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 후보 상태 (스펙 §5.1)
CANDIDATE_STATES: frozenset[str] = frozenset(
    {"PENDING", "MATCHED", "NEW_ENTITY", "REVIEW_REQUIRED", "REJECTED", "FAILED"}
)

# 작업 상태 (스펙 §8)
JOB_STATES: frozenset[str] = frozenset(
    {"PENDING", "RUNNING", "PAUSED", "COMPLETED", "PARTIAL_FAILED", "FAILED", "CANCELLED"}
)

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,
    doc_name        TEXT NOT NULL DEFAULT '',
    folder_id       TEXT NOT NULL DEFAULT '',
    folder_name     TEXT NOT NULL DEFAULT '',
    document_type   TEXT NOT NULL DEFAULT 'UNKNOWN',
    year            INTEGER,
    project_no      TEXT NOT NULL DEFAULT '',
    rfp_no          TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    extract_state   TEXT NOT NULL DEFAULT 'PENDING',
    extractor_version TEXT NOT NULL DEFAULT '',
    prompt_version  TEXT NOT NULL DEFAULT '',
    error_message   TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_state ON documents(extract_state);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_no);

CREATE TABLE IF NOT EXISTS entity_candidates (
    candidate_id    TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    page            INTEGER,
    temp_id         TEXT NOT NULL DEFAULT '',
    entity_type     TEXT NOT NULL,
    surface_form    TEXT NOT NULL,
    canonical_name_candidate TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    statement_status TEXT NOT NULL DEFAULT 'UNCERTAIN',
    project_relevance TEXT NOT NULL DEFAULT 'UNCERTAIN',
    specificity     TEXT NOT NULL DEFAULT '',
    target_key      TEXT NOT NULL DEFAULT '',
    evidence        TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.0,
    section_hint    TEXT NOT NULL DEFAULT '',
    extractor_model TEXT NOT NULL DEFAULT '',
    extractor_version TEXT NOT NULL DEFAULT '',
    prompt_version  TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'PENDING',
    reject_reason   TEXT NOT NULL DEFAULT '',
    doc_entity_id   TEXT,
    canonical_id    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cand_doc ON entity_candidates(doc_id);
CREATE INDEX IF NOT EXISTS idx_cand_state ON entity_candidates(state);
CREATE INDEX IF NOT EXISTS idx_cand_type ON entity_candidates(entity_type);
CREATE INDEX IF NOT EXISTS idx_cand_chunk ON entity_candidates(chunk_id);

CREATE TABLE IF NOT EXISTS doc_entities (
    doc_entity_id   TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    canonical_name_candidate TEXT NOT NULL,
    normalized_name TEXT NOT NULL DEFAULT '',
    target_key      TEXT NOT NULL DEFAULT '',
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    statuses_json   TEXT NOT NULL DEFAULT '[]',
    source_candidate_ids_json TEXT NOT NULL DEFAULT '[]',
    mention_count   INTEGER NOT NULL DEFAULT 0,
    max_confidence  REAL NOT NULL DEFAULT 0.0,
    review_required INTEGER NOT NULL DEFAULT 0,
    canonical_id    TEXT,
    state           TEXT NOT NULL DEFAULT 'PENDING',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_docent_doc ON doc_entities(doc_id);
CREATE INDEX IF NOT EXISTS idx_docent_state ON doc_entities(state);
CREATE INDEX IF NOT EXISTS idx_docent_type ON doc_entities(entity_type);

CREATE TABLE IF NOT EXISTS canonical_entities (
    canonical_id    TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    target_key      TEXT NOT NULL DEFAULT '',
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    review_status   TEXT NOT NULL DEFAULT 'AUTO_APPROVED',
    mention_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_canon_type ON canonical_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_canon_norm ON canonical_entities(entity_type, normalized_name);

CREATE TABLE IF NOT EXISTS relation_candidates (
    relation_candidate_id TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    page            INTEGER,
    project_no      TEXT NOT NULL DEFAULT '',
    source_canonical_id TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    target_canonical_id TEXT NOT NULL,
    statement_status TEXT NOT NULL DEFAULT 'UNCERTAIN',
    evidence        TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.0,
    state           TEXT NOT NULL DEFAULT 'PENDING',
    reject_reason   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rel_doc ON relation_candidates(doc_id);
CREATE INDEX IF NOT EXISTS idx_rel_state ON relation_candidates(state);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,
    scope           TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'PENDING',
    counts_json     TEXT NOT NULL DEFAULT '{}',
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    error           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class DocumentMeta:
    """문서 단위 메타 — 폴더에서 유도한 문서유형·연도 + 추출로 얻은 과제번호."""

    doc_id: str
    doc_name: str = ""
    folder_id: str = ""
    folder_name: str = ""
    document_type: str = "UNKNOWN"
    year: int | None = None
    project_no: str = ""
    rfp_no: str = ""
    title: str = ""
    chunk_count: int = 0
    extract_state: str = "PENDING"
    extractor_version: str = ""
    prompt_version: str = ""
    error_message: str = ""


@dataclass
class EntityCandidate:
    """LLM이 제안한 엔티티 후보 하나. **아직 그래프 노드가 아니다.**"""

    candidate_id: str
    doc_id: str
    chunk_id: str
    entity_type: str
    surface_form: str
    page: int | None = None
    temp_id: str = ""
    canonical_name_candidate: str = ""
    description: str = ""
    statement_status: str = "UNCERTAIN"
    project_relevance: str = "UNCERTAIN"
    specificity: str = ""
    target_key: str = ""
    evidence: str = ""
    confidence: float = 0.0
    section_hint: str = ""
    extractor_model: str = ""
    extractor_version: str = ""
    prompt_version: str = ""
    state: str = "PENDING"
    reject_reason: str = ""
    doc_entity_id: str | None = None
    canonical_id: str | None = None


@dataclass
class DocEntity:
    """문서 단위로 통합된 엔티티 — 같은 문서 안의 표기 변형을 하나로 묶은 결과."""

    doc_entity_id: str
    doc_id: str
    entity_type: str
    canonical_name_candidate: str
    normalized_name: str = ""
    target_key: str = ""
    aliases: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    source_candidate_ids: list[str] = field(default_factory=list)
    mention_count: int = 0
    max_confidence: float = 0.0
    review_required: bool = False
    canonical_id: str | None = None
    state: str = "PENDING"


@dataclass
class CanonicalEntity:
    """정규 엔티티 — Neo4j에 적재되는 최종 형태."""

    canonical_id: str
    entity_type: str
    canonical_name: str
    normalized_name: str
    target_key: str = ""
    aliases: list[str] = field(default_factory=list)
    review_status: str = "AUTO_APPROVED"
    mention_count: int = 0


@dataclass
class RelationCandidate:
    """관계 후보 — 정규 엔티티 사이에서만 만들어진다 (새 엔티티 생성 금지)."""

    relation_candidate_id: str
    doc_id: str
    chunk_id: str
    source_canonical_id: str
    relation_type: str
    target_canonical_id: str
    page: int | None = None
    project_no: str = ""
    statement_status: str = "UNCERTAIN"
    evidence: str = ""
    confidence: float = 0.0
    state: str = "PENDING"
    reject_reason: str = ""


class CandidateStore:
    """후보 SQLite 저장소.

    스레드마다 연결을 따로 갖는다 — sqlite3 연결은 스레드 간 공유가 안전하지 않은데,
    추출은 executor 스레드에서 돌아간다.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._init_lock:
            self._ensure_schema()

    # ── 연결 ──────────────────────────────────────────────────────────────────

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            # 배치 쓰기 중에도 조회가 막히지 않게 한다.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_schema(self) -> None:
        conn = self._conn
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── 문서 ──────────────────────────────────────────────────────────────────

    def upsert_document(self, meta: DocumentMeta) -> None:
        """문서 메타 저장. 같은 doc_id를 다시 넣어도 행이 늘지 않는다(재실행 안전)."""
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO documents(doc_id, doc_name, folder_id, folder_name, document_type,
                    year, project_no, rfp_no, title, chunk_count, extract_state,
                    extractor_version, prompt_version, error_message, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
                ON CONFLICT(doc_id) DO UPDATE SET
                    doc_name=excluded.doc_name,
                    folder_id=excluded.folder_id,
                    folder_name=excluded.folder_name,
                    document_type=excluded.document_type,
                    year=excluded.year,
                    project_no=CASE WHEN excluded.project_no != '' THEN excluded.project_no
                                    ELSE documents.project_no END,
                    rfp_no=CASE WHEN excluded.rfp_no != '' THEN excluded.rfp_no
                                ELSE documents.rfp_no END,
                    title=CASE WHEN excluded.title != '' THEN excluded.title ELSE documents.title END,
                    chunk_count=excluded.chunk_count,
                    extract_state=excluded.extract_state,
                    extractor_version=excluded.extractor_version,
                    prompt_version=excluded.prompt_version,
                    error_message=excluded.error_message,
                    updated_at=datetime('now')
                """,
                (
                    meta.doc_id,
                    meta.doc_name,
                    meta.folder_id,
                    meta.folder_name,
                    meta.document_type,
                    meta.year,
                    meta.project_no,
                    meta.rfp_no,
                    meta.title,
                    meta.chunk_count,
                    meta.extract_state,
                    meta.extractor_version,
                    meta.prompt_version,
                    meta.error_message,
                ),
            )

    def get_document(self, doc_id: str) -> DocumentMeta | None:
        row = self._conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return _row_to_document(row) if row else None

    def set_document_state(self, doc_id: str, state: str, error: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE documents SET extract_state=?, error_message=?, updated_at=datetime('now')"
                " WHERE doc_id=?",
                (state, error[:500], doc_id),
            )

    def documents_by_state(self, state: str, limit: int = 1000) -> list[DocumentMeta]:
        rows = self._conn.execute(
            "SELECT * FROM documents WHERE extract_state=? ORDER BY doc_id LIMIT ?",
            (state, limit),
        ).fetchall()
        return [_row_to_document(r) for r in rows]

    # ── 엔티티 후보 ───────────────────────────────────────────────────────────

    def replace_candidates_for_chunk(
        self, doc_id: str, chunk_id: str, candidates: Iterable[EntityCandidate]
    ) -> int:
        """청크 하나의 후보를 통째로 갈아끼운다.

        재실행 시 중복이 쌓이지 않게 **삭제 후 삽입**한다. 청크 단위로 잘라서 지우므로
        같은 문서의 다른 청크 결과는 보존된다(부분 재처리 가능).
        """
        items = list(candidates)
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM entity_candidates WHERE doc_id=? AND chunk_id=?", (doc_id, chunk_id)
            )
            conn.executemany(
                """
                INSERT INTO entity_candidates(candidate_id, doc_id, chunk_id, page, temp_id,
                    entity_type, surface_form, canonical_name_candidate, description,
                    statement_status, project_relevance, specificity, target_key, evidence,
                    confidence, section_hint, extractor_model, extractor_version, prompt_version,
                    state, reject_reason, doc_entity_id, canonical_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        c.candidate_id,
                        c.doc_id,
                        c.chunk_id,
                        c.page,
                        c.temp_id,
                        c.entity_type,
                        c.surface_form,
                        c.canonical_name_candidate,
                        c.description,
                        c.statement_status,
                        c.project_relevance,
                        c.specificity,
                        c.target_key,
                        c.evidence,
                        c.confidence,
                        c.section_hint,
                        c.extractor_model,
                        c.extractor_version,
                        c.prompt_version,
                        c.state,
                        c.reject_reason,
                        c.doc_entity_id,
                        c.canonical_id,
                    )
                    for c in items
                ],
            )
        return len(items)

    def candidates_for_document(
        self, doc_id: str, states: Iterable[str] | None = None
    ) -> list[EntityCandidate]:
        sql = "SELECT * FROM entity_candidates WHERE doc_id=?"
        params: list[Any] = [doc_id]
        if states:
            state_list = list(states)
            sql += f" AND state IN ({','.join('?' * len(state_list))})"
            params.extend(state_list)
        sql += " ORDER BY chunk_id, candidate_id"
        return [_row_to_candidate(r) for r in self._conn.execute(sql, params).fetchall()]

    def count_candidates(self, doc_id: str | None = None) -> dict[str, int]:
        """상태별 후보 수. 대시보드·평가용."""
        sql = "SELECT state, COUNT(*) AS c FROM entity_candidates"
        params: list[Any] = []
        if doc_id:
            sql += " WHERE doc_id=?"
            params.append(doc_id)
        sql += " GROUP BY state"
        return {r["state"]: r["c"] for r in self._conn.execute(sql, params).fetchall()}

    def set_candidate_states(
        self, candidate_ids: Iterable[str], state: str, reason: str = ""
    ) -> int:
        ids = list(candidate_ids)
        if not ids:
            return 0
        with self._tx() as conn:
            cur = conn.executemany(
                "UPDATE entity_candidates SET state=?, reject_reason=? WHERE candidate_id=?",
                [(state, reason, cid) for cid in ids],
            )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(ids)

    # ── 문서 단위 통합 결과 ───────────────────────────────────────────────────

    def replace_doc_entities(self, doc_id: str, entities: Iterable[DocEntity]) -> int:
        items = list(entities)
        with self._tx() as conn:
            conn.execute("DELETE FROM doc_entities WHERE doc_id=?", (doc_id,))
            conn.executemany(
                """
                INSERT INTO doc_entities(doc_entity_id, doc_id, entity_type,
                    canonical_name_candidate, normalized_name, target_key, aliases_json,
                    statuses_json, source_candidate_ids_json, mention_count, max_confidence,
                    review_required, canonical_id, state)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        e.doc_entity_id,
                        e.doc_id,
                        e.entity_type,
                        e.canonical_name_candidate,
                        e.normalized_name,
                        e.target_key,
                        json.dumps(e.aliases, ensure_ascii=False),
                        json.dumps(e.statuses, ensure_ascii=False),
                        json.dumps(e.source_candidate_ids, ensure_ascii=False),
                        e.mention_count,
                        e.max_confidence,
                        1 if e.review_required else 0,
                        e.canonical_id,
                        e.state,
                    )
                    for e in items
                ],
            )
            # 후보에 소속 doc_entity를 되짚어 둔다 — 근거 역추적용
            for e in items:
                if e.source_candidate_ids:
                    conn.executemany(
                        "UPDATE entity_candidates SET doc_entity_id=? WHERE candidate_id=?",
                        [(e.doc_entity_id, cid) for cid in e.source_candidate_ids],
                    )
        return len(items)

    def doc_entities(
        self, doc_id: str | None = None, states: Iterable[str] | None = None, limit: int = 100000
    ) -> list[DocEntity]:
        sql = "SELECT * FROM doc_entities WHERE 1=1"
        params: list[Any] = []
        if doc_id:
            sql += " AND doc_id=?"
            params.append(doc_id)
        if states:
            sl = list(states)
            sql += f" AND state IN ({','.join('?' * len(sl))})"
            params.extend(sl)
        sql += " ORDER BY doc_id, entity_type, canonical_name_candidate LIMIT ?"
        params.append(limit)
        return [_row_to_doc_entity(r) for r in self._conn.execute(sql, params).fetchall()]

    def link_doc_entity(self, doc_entity_id: str, canonical_id: str, state: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE doc_entities SET canonical_id=?, state=? WHERE doc_entity_id=?",
                (canonical_id, state, doc_entity_id),
            )
            conn.execute(
                "UPDATE entity_candidates SET canonical_id=?, state=? WHERE doc_entity_id=?",
                (canonical_id, "MATCHED" if state == "MATCHED" else state, doc_entity_id),
            )

    # ── 정규 엔티티 ───────────────────────────────────────────────────────────

    def upsert_canonical(self, entity: CanonicalEntity) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO canonical_entities(canonical_id, entity_type, canonical_name,
                    normalized_name, target_key, aliases_json, review_status, mention_count)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    canonical_name=excluded.canonical_name,
                    normalized_name=excluded.normalized_name,
                    target_key=excluded.target_key,
                    aliases_json=excluded.aliases_json,
                    review_status=excluded.review_status,
                    mention_count=excluded.mention_count,
                    updated_at=datetime('now')
                """,
                (
                    entity.canonical_id,
                    entity.entity_type,
                    entity.canonical_name,
                    entity.normalized_name,
                    entity.target_key,
                    json.dumps(entity.aliases, ensure_ascii=False),
                    entity.review_status,
                    entity.mention_count,
                ),
            )

    def find_canonical(self, entity_type: str, normalized_name: str) -> CanonicalEntity | None:
        """유형 안에서만 찾는다 — 다른 유형의 노드는 동일성 판정 후보가 아니다 (스펙 §6)."""
        row = self._conn.execute(
            "SELECT * FROM canonical_entities WHERE entity_type=? AND normalized_name=?",
            (entity_type, normalized_name),
        ).fetchone()
        return _row_to_canonical(row) if row else None

    def canonicals_by_type(self, entity_type: str, limit: int = 100000) -> list[CanonicalEntity]:
        rows = self._conn.execute(
            "SELECT * FROM canonical_entities WHERE entity_type=? ORDER BY canonical_id LIMIT ?",
            (entity_type, limit),
        ).fetchall()
        return [_row_to_canonical(r) for r in rows]

    def all_canonicals(self, limit: int = 100000) -> list[CanonicalEntity]:
        rows = self._conn.execute(
            "SELECT * FROM canonical_entities ORDER BY entity_type, canonical_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_canonical(r) for r in rows]

    # ── 관계 후보 ─────────────────────────────────────────────────────────────

    def replace_relations_for_chunk(
        self, doc_id: str, chunk_id: str, relations: Iterable[RelationCandidate]
    ) -> int:
        items = list(relations)
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM relation_candidates WHERE doc_id=? AND chunk_id=?", (doc_id, chunk_id)
            )
            conn.executemany(
                """
                INSERT INTO relation_candidates(relation_candidate_id, doc_id, chunk_id, page,
                    project_no, source_canonical_id, relation_type, target_canonical_id,
                    statement_status, evidence, confidence, state, reject_reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r.relation_candidate_id,
                        r.doc_id,
                        r.chunk_id,
                        r.page,
                        r.project_no,
                        r.source_canonical_id,
                        r.relation_type,
                        r.target_canonical_id,
                        r.statement_status,
                        r.evidence,
                        r.confidence,
                        r.state,
                        r.reject_reason,
                    )
                    for r in items
                ],
            )
        return len(items)

    def relations_for_document(
        self, doc_id: str, states: Iterable[str] | None = None
    ) -> list[RelationCandidate]:
        sql = "SELECT * FROM relation_candidates WHERE doc_id=?"
        params: list[Any] = [doc_id]
        if states:
            sl = list(states)
            sql += f" AND state IN ({','.join('?' * len(sl))})"
            params.extend(sl)
        return [_row_to_relation(r) for r in self._conn.execute(sql, params).fetchall()]

    # ── 작업 ──────────────────────────────────────────────────────────────────

    def start_job(self, job_id: str, job_type: str, scope: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(job_id, job_type, scope, state, counts_json,"
                " started_at) VALUES(?,?,?,'RUNNING','{}', datetime('now'))",
                (job_id, job_type, scope),
            )

    def finish_job(
        self, job_id: str, state: str, counts: dict[str, Any] | None = None, error: str = ""
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET state=?, counts_json=?, error=?, finished_at=datetime('now')"
                " WHERE job_id=?",
                (state, json.dumps(counts or {}, ensure_ascii=False), error[:500], job_id),
            )

    def jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["counts"] = json.loads(d.pop("counts_json") or "{}")
            out.append(d)
        return out

    # ── 초기화 ────────────────────────────────────────────────────────────────

    def reset(self, keep_documents: bool = False) -> dict[str, int]:
        """후보 저장소를 비운다. 그래프 초기화와 짝을 이룬다.

        keep_documents=True면 문서 메타(폴더·유형·과제번호)는 남겨 재추출만 다시 한다.
        """
        counts: dict[str, int] = {}
        tables = ["relation_candidates", "doc_entities", "canonical_entities", "entity_candidates"]
        if not keep_documents:
            tables.append("documents")
        with self._tx() as conn:
            for t in tables:
                counts[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
                conn.execute(f"DELETE FROM {t}")
            if keep_documents:
                conn.execute("UPDATE documents SET extract_state='PENDING', error_message=''")
        logger.info("KG 후보 저장소 초기화: %s", counts)
        return counts

    def stats(self) -> dict[str, Any]:
        c = self._conn
        return {
            "documents": c.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"],
            "entity_candidates": c.execute(
                "SELECT COUNT(*) AS c FROM entity_candidates"
            ).fetchone()["c"],
            "doc_entities": c.execute("SELECT COUNT(*) AS c FROM doc_entities").fetchone()["c"],
            "canonical_entities": c.execute(
                "SELECT COUNT(*) AS c FROM canonical_entities"
            ).fetchone()["c"],
            "relation_candidates": c.execute(
                "SELECT COUNT(*) AS c FROM relation_candidates"
            ).fetchone()["c"],
            "by_candidate_state": self.count_candidates(),
        }


# ── row → dataclass ──────────────────────────────────────────────────────────


def _row_to_document(row: sqlite3.Row) -> DocumentMeta:
    return DocumentMeta(
        doc_id=row["doc_id"],
        doc_name=row["doc_name"],
        folder_id=row["folder_id"],
        folder_name=row["folder_name"],
        document_type=row["document_type"],
        year=row["year"],
        project_no=row["project_no"],
        rfp_no=row["rfp_no"],
        title=row["title"],
        chunk_count=row["chunk_count"],
        extract_state=row["extract_state"],
        extractor_version=row["extractor_version"],
        prompt_version=row["prompt_version"],
        error_message=row["error_message"],
    )


def _row_to_candidate(row: sqlite3.Row) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=row["candidate_id"],
        doc_id=row["doc_id"],
        chunk_id=row["chunk_id"],
        page=row["page"],
        temp_id=row["temp_id"],
        entity_type=row["entity_type"],
        surface_form=row["surface_form"],
        canonical_name_candidate=row["canonical_name_candidate"],
        description=row["description"],
        statement_status=row["statement_status"],
        project_relevance=row["project_relevance"],
        specificity=row["specificity"],
        target_key=row["target_key"],
        evidence=row["evidence"],
        confidence=row["confidence"],
        section_hint=row["section_hint"],
        extractor_model=row["extractor_model"],
        extractor_version=row["extractor_version"],
        prompt_version=row["prompt_version"],
        state=row["state"],
        reject_reason=row["reject_reason"],
        doc_entity_id=row["doc_entity_id"],
        canonical_id=row["canonical_id"],
    )


def _row_to_doc_entity(row: sqlite3.Row) -> DocEntity:
    return DocEntity(
        doc_entity_id=row["doc_entity_id"],
        doc_id=row["doc_id"],
        entity_type=row["entity_type"],
        canonical_name_candidate=row["canonical_name_candidate"],
        normalized_name=row["normalized_name"],
        target_key=row["target_key"],
        aliases=json.loads(row["aliases_json"] or "[]"),
        statuses=json.loads(row["statuses_json"] or "[]"),
        source_candidate_ids=json.loads(row["source_candidate_ids_json"] or "[]"),
        mention_count=row["mention_count"],
        max_confidence=row["max_confidence"],
        review_required=bool(row["review_required"]),
        canonical_id=row["canonical_id"],
        state=row["state"],
    )


def _row_to_canonical(row: sqlite3.Row) -> CanonicalEntity:
    return CanonicalEntity(
        canonical_id=row["canonical_id"],
        entity_type=row["entity_type"],
        canonical_name=row["canonical_name"],
        normalized_name=row["normalized_name"],
        target_key=row["target_key"],
        aliases=json.loads(row["aliases_json"] or "[]"),
        review_status=row["review_status"],
        mention_count=row["mention_count"],
    )


def _row_to_relation(row: sqlite3.Row) -> RelationCandidate:
    return RelationCandidate(
        relation_candidate_id=row["relation_candidate_id"],
        doc_id=row["doc_id"],
        chunk_id=row["chunk_id"],
        page=row["page"],
        project_no=row["project_no"],
        source_canonical_id=row["source_canonical_id"],
        relation_type=row["relation_type"],
        target_canonical_id=row["target_canonical_id"],
        statement_status=row["statement_status"],
        evidence=row["evidence"],
        confidence=row["confidence"],
        state=row["state"],
        reject_reason=row["reject_reason"],
    )


def as_dict(obj: Any) -> dict[str, Any]:
    """dataclass → dict (로그·평가 출력용)."""
    return asdict(obj)

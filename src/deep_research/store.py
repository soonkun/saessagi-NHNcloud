# src/deep_research/store.py
"""M_20 딥 리서치 프로젝트(방) 저장소 — CR-62.

모드 3개가 코드 상수로 박혀 있던 것을 방(프로젝트) 행으로 옮긴다. 방마다 지침과 검색
설정을 갖고, **지침은 버전으로 쌓인다.**

`src/kg/candidates.py`의 `CandidateStore` 패턴을 그대로 따른다 — 스레드 로컬 연결,
WAL, dataclass ↔ row 변환, 멱등 스키마. 이미 이 프로젝트에서 검증된 방식이고 새 의존성이
없다(sqlite3는 calendar.db·kg_candidates.db가 이미 쓴다).

**지침 이력은 append-only다.** 저장도 새 버전, 되돌리기도 새 버전. 이력을 덮어쓰거나
지우는 경로는 두지 않는다 — 되돌리기가 있는 이유가 "실수로 날린 지침을 되찾는 것"인데
되돌리기 자체가 이력을 지우면 앞뒤가 안 맞는다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# 검색 설정 기본값. 서비스의 옛 전역 상수와 같은 값이라 방을 안 건드리면 동작이 그대로다.
DEFAULT_SUB_QUERIES = 6
DEFAULT_TOP_K = 5
DEFAULT_GAP_ROUNDS = 1
DEFAULT_MAX_EVIDENCE = 24

# 사용자가 설정할 수 있는 범위. 상한이 없으면 근거 예산이 터지고 종합이 컨텍스트를 넘긴다.
LIMITS: dict[str, tuple[int, int]] = {
    "sub_queries": (1, 12),
    "top_k_per_query": (1, 15),
    "gap_rounds": (0, 3),
    "max_evidence_chunks": (5, 40),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    icon            TEXT NOT NULL DEFAULT '',
    planner_hint    TEXT NOT NULL DEFAULT '',
    sub_queries     INTEGER NOT NULL DEFAULT 6,
    top_k_per_query INTEGER NOT NULL DEFAULT 5,
    gap_rounds      INTEGER NOT NULL DEFAULT 1,
    max_evidence_chunks INTEGER NOT NULL DEFAULT 24,
    is_seed         INTEGER NOT NULL DEFAULT 0,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_projects_order ON projects(sort_order, created_at);

CREATE TABLE IF NOT EXISTS instruction_versions (
    version_id      TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    version_no      INTEGER NOT NULL,
    content         TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_iv_project ON instruction_versions(project_id, version_no DESC);

CREATE TABLE IF NOT EXISTS turns (
    turn_id         TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    sources_json    TEXT NOT NULL DEFAULT '[]',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_project ON turns(project_id, created_at);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class ResearchProject:
    """방 하나. `instructions`는 최신 버전에서 채워 넣는다(표에는 없다)."""

    project_id: str
    name: str
    description: str = ""
    icon: str = ""
    planner_hint: str = ""
    sub_queries: int = DEFAULT_SUB_QUERIES
    top_k_per_query: int = DEFAULT_TOP_K
    gap_rounds: int = DEFAULT_GAP_ROUNDS
    max_evidence_chunks: int = DEFAULT_MAX_EVIDENCE
    is_seed: bool = False
    sort_order: int = 0
    instructions: str = ""
    version_no: int = 0
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "planner_hint": self.planner_hint,
            "sub_queries": self.sub_queries,
            "top_k_per_query": self.top_k_per_query,
            "gap_rounds": self.gap_rounds,
            "max_evidence_chunks": self.max_evidence_chunks,
            "is_seed": self.is_seed,
            "instructions": self.instructions,
            "version_no": self.version_no,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class InstructionVersion:
    version_id: str
    project_id: str
    version_no: int
    content: str
    note: str = ""
    created_at: str = ""

    def as_dict(self, preview_chars: int = 160) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version_no": self.version_no,
            "note": self.note,
            "created_at": self.created_at,
            "chars": len(self.content),
            "preview": self.content[:preview_chars],
        }


@dataclass
class Turn:
    turn_id: str
    project_id: str
    role: str  # user | assistant
    content: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "attachments": self.attachments,
            "created_at": self.created_at,
        }


def clamp(field_name: str, value: int) -> int:
    lo, hi = LIMITS[field_name]
    return max(lo, min(int(value), hi))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ResearchProjectStore:
    """방·지침이력·대화 저장소."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with threading.Lock():
            self._ensure_schema()

    # ── 연결 ──────────────────────────────────────────────────────────────────

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
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

    # ── 방 ────────────────────────────────────────────────────────────────────

    def list_projects(self) -> list[ResearchProject]:
        rows = self._conn.execute(
            "SELECT * FROM projects ORDER BY sort_order, created_at"
        ).fetchall()
        return [self._hydrate(_row_to_project(r)) for r in rows]

    def get_project(self, project_id: str) -> ResearchProject | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return self._hydrate(_row_to_project(row)) if row else None

    def _hydrate(self, project: ResearchProject) -> ResearchProject:
        """최신 지침 버전을 얹는다. 지침은 별도 표라 방 조회만으로는 비어 있다."""
        latest = self.latest_version(project.project_id)
        if latest is not None:
            project.instructions = latest.content
            project.version_no = latest.version_no
        return project

    def create_project(
        self,
        name: str,
        instructions: str,
        *,
        project_id: str | None = None,
        description: str = "",
        icon: str = "",
        planner_hint: str = "",
        sub_queries: int = DEFAULT_SUB_QUERIES,
        top_k_per_query: int = DEFAULT_TOP_K,
        gap_rounds: int = DEFAULT_GAP_ROUNDS,
        max_evidence_chunks: int = DEFAULT_MAX_EVIDENCE,
        is_seed: bool = False,
        note: str = "최초 생성",
    ) -> ResearchProject:
        pid = (project_id or _new_id("rp")).strip()
        if not pid:
            raise ValueError("project_id가 비어 있습니다")
        if not name.strip():
            raise ValueError("방 이름이 비어 있습니다")
        with self._tx() as conn:
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM projects"
            ).fetchone()["n"]
            conn.execute(
                "INSERT INTO projects(project_id, name, description, icon, planner_hint,"
                " sub_queries, top_k_per_query, gap_rounds, max_evidence_chunks,"
                " is_seed, sort_order) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    name.strip(),
                    description,
                    icon,
                    planner_hint,
                    clamp("sub_queries", sub_queries),
                    clamp("top_k_per_query", top_k_per_query),
                    clamp("gap_rounds", gap_rounds),
                    clamp("max_evidence_chunks", max_evidence_chunks),
                    1 if is_seed else 0,
                    order,
                ),
            )
        self.save_instructions(pid, instructions, note=note)
        project = self.get_project(pid)
        assert project is not None
        logger.info("딥 리서치 방 생성: %s (%s)", name, pid)
        return project

    def update_project(self, project_id: str, **fields: Any) -> ResearchProject | None:
        """이름·설명·검색설정 갱신. **지침은 여기서 바꾸지 않는다** (버전이 남아야 하므로)."""
        allowed = {
            "name",
            "description",
            "icon",
            "planner_hint",
            "sub_queries",
            "top_k_per_query",
            "gap_rounds",
            "max_evidence_chunks",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            if key in LIMITS:
                value = clamp(key, int(value))
            elif key == "name":
                value = str(value).strip()
                if not value:
                    continue
            sets.append(f"{key}=?")
            params.append(value)
        if not sets:
            return self.get_project(project_id)
        params.append(project_id)
        with self._tx() as conn:
            conn.execute(
                f"UPDATE projects SET {', '.join(sets)}, updated_at=datetime('now')"
                " WHERE project_id=?",
                params,
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> dict[str, int]:
        """방과 그 지침 이력·대화를 함께 지운다.

        방을 지우면 그 안의 것도 같이 가는 게 사용자 기대다. 다만 **다른 방은 절대
        건드리지 않는다** — 전부 project_id로 한정한다.
        """
        with self._tx() as conn:
            counts = {
                "instruction_versions": conn.execute(
                    "SELECT COUNT(*) AS c FROM instruction_versions WHERE project_id=?",
                    (project_id,),
                ).fetchone()["c"],
                "turns": conn.execute(
                    "SELECT COUNT(*) AS c FROM turns WHERE project_id=?", (project_id,)
                ).fetchone()["c"],
            }
            conn.execute("DELETE FROM instruction_versions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM turns WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
        logger.info("딥 리서치 방 삭제: %s %s", project_id, counts)
        return counts

    # ── 지침 버전 ─────────────────────────────────────────────────────────────

    def latest_version(self, project_id: str) -> InstructionVersion | None:
        row = self._conn.execute(
            "SELECT * FROM instruction_versions WHERE project_id=?"
            " ORDER BY version_no DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return _row_to_version(row) if row else None

    def list_versions(self, project_id: str, limit: int = 50) -> list[InstructionVersion]:
        rows = self._conn.execute(
            "SELECT * FROM instruction_versions WHERE project_id=?"
            " ORDER BY version_no DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [_row_to_version(r) for r in rows]

    def get_version(self, project_id: str, version_no: int) -> InstructionVersion | None:
        row = self._conn.execute(
            "SELECT * FROM instruction_versions WHERE project_id=? AND version_no=?",
            (project_id, version_no),
        ).fetchone()
        return _row_to_version(row) if row else None

    def save_instructions(
        self, project_id: str, content: str, note: str = ""
    ) -> InstructionVersion:
        """새 버전을 쌓는다. 같은 내용이면 새로 만들지 않는다.

        같은 내용에도 버전을 쌓으면 저장 버튼을 두 번 눌렀다는 이유로 이력이 지저분해지고,
        정작 되돌릴 지점을 찾기 어려워진다.
        """
        content = content or ""
        latest = self.latest_version(project_id)
        if latest is not None and latest.content == content:
            return latest
        next_no = (latest.version_no if latest else 0) + 1
        version = InstructionVersion(
            version_id=_new_id("iv"),
            project_id=project_id,
            version_no=next_no,
            content=content,
            note=note,
        )
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO instruction_versions(version_id, project_id, version_no,"
                " content, note) VALUES(?,?,?,?,?)",
                (version.version_id, project_id, next_no, content, note),
            )
            conn.execute(
                "UPDATE projects SET updated_at=datetime('now') WHERE project_id=?",
                (project_id,),
            )
        logger.info("딥 리서치 지침 저장: %s v%d (%d자)", project_id, next_no, len(content))
        return version

    def restore_version(self, project_id: str, version_no: int) -> InstructionVersion | None:
        """옛 버전 내용을 **새 버전으로 복원한다.**

        되돌리기가 이력을 자르지 않는다 — 복원 자체도 되돌릴 수 있어야 한다.
        """
        target = self.get_version(project_id, version_no)
        if target is None:
            return None
        return self.save_instructions(project_id, target.content, note=f"v{version_no} 복원")

    # ── 대화 ──────────────────────────────────────────────────────────────────

    def add_turn(
        self,
        project_id: str,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
        attachments: list[str] | None = None,
    ) -> Turn:
        turn = Turn(
            turn_id=_new_id("t"),
            project_id=project_id,
            role=role,
            content=content,
            sources=sources or [],
            attachments=attachments or [],
        )
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO turns(turn_id, project_id, role, content, sources_json,"
                " attachments_json) VALUES(?,?,?,?,?,?)",
                (
                    turn.turn_id,
                    project_id,
                    role,
                    content,
                    json.dumps(turn.sources, ensure_ascii=False),
                    json.dumps(turn.attachments, ensure_ascii=False),
                ),
            )
        return turn

    def list_turns(self, project_id: str, limit: int = 200) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE project_id=? ORDER BY created_at, rowid LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def clear_turns(self, project_id: str) -> int:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM turns WHERE project_id=?", (project_id,))
        return cur.rowcount or 0

    # ── 시드 ──────────────────────────────────────────────────────────────────

    def seed_if_empty(self, seeds: list[dict[str, Any]]) -> int:
        """방이 하나도 없을 때만 예시 방을 만든다.

        **사용자가 전부 지우면 다시 만들지 않는다** — 지운 것을 되살리는 동기화는
        이 프로젝트에서 이미 사고를 낸 적이 있다(E-68). 복구는 명시적 요청으로만.
        """
        row = self._conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()
        if row["c"] > 0:
            return 0
        made = 0
        for s in seeds:
            try:
                self.create_project(is_seed=True, note="예시 방 생성", **s)
                made += 1
            except Exception as exc:
                logger.warning("예시 방 생성 실패 (%s): %s", s.get("project_id"), exc)
        if made:
            logger.info("딥 리서치 예시 방 %d개 생성", made)
        return made

    def stats(self) -> dict[str, int]:
        c = self._conn
        return {
            "projects": c.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"],
            "instruction_versions": c.execute(
                "SELECT COUNT(*) AS c FROM instruction_versions"
            ).fetchone()["c"],
            "turns": c.execute("SELECT COUNT(*) AS c FROM turns").fetchone()["c"],
        }


# ── row → dataclass ──────────────────────────────────────────────────────────


def _row_to_project(row: sqlite3.Row) -> ResearchProject:
    return ResearchProject(
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"],
        icon=row["icon"],
        planner_hint=row["planner_hint"],
        sub_queries=row["sub_queries"],
        top_k_per_query=row["top_k_per_query"],
        gap_rounds=row["gap_rounds"],
        max_evidence_chunks=row["max_evidence_chunks"],
        is_seed=bool(row["is_seed"]),
        sort_order=row["sort_order"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_version(row: sqlite3.Row) -> InstructionVersion:
    return InstructionVersion(
        version_id=row["version_id"],
        project_id=row["project_id"],
        version_no=row["version_no"],
        content=row["content"],
        note=row["note"],
        created_at=row["created_at"],
    )


def _row_to_turn(row: sqlite3.Row) -> Turn:
    return Turn(
        turn_id=row["turn_id"],
        project_id=row["project_id"],
        role=row["role"],
        content=row["content"],
        sources=json.loads(row["sources_json"] or "[]"),
        attachments=json.loads(row["attachments_json"] or "[]"),
        created_at=row["created_at"],
    )


__all__ = [
    "DEFAULT_GAP_ROUNDS",
    "DEFAULT_MAX_EVIDENCE",
    "DEFAULT_SUB_QUERIES",
    "DEFAULT_TOP_K",
    "LIMITS",
    "InstructionVersion",
    "ResearchProject",
    "ResearchProjectStore",
    "Turn",
    "clamp",
]

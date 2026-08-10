# tests/deep_research/test_store.py
"""M_20 딥 리서치 방 저장소 테스트 (CR-62).

무게중심은 **지침 이력이 사라지지 않는 것**이다. 되돌리기가 있는 이유가 "실수로 날린
지침을 되찾는 것"인데, 되돌리기 자체가 이력을 자르면 앞뒤가 안 맞는다. 그래서 복원도
새 버전으로 쌓이는지, 복원한 뒤에도 옛 버전이 그대로 조회되는지를 고정한다.

이 코드베이스에는 이력·롤백 인프라가 여기 말고 어디에도 없다(M_13·M_14·M_09 스펙이
명시적으로 배제). 즉 이 파일이 유일한 안전망이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_research.store import (
    DEFAULT_SUB_QUERIES,
    LIMITS,
    ResearchProjectStore,
    clamp,
)


@pytest.fixture
def store(tmp_path: Path) -> ResearchProjectStore:
    return ResearchProjectStore(tmp_path / "rp.db")


def _make(store: ResearchProjectStore, name: str = "테스트 방", **kw: object) -> str:
    kw.setdefault("instructions", "지침 v1")
    return store.create_project(name=name, **kw).project_id  # type: ignore[arg-type]


# ── 방 CRUD ───────────────────────────────────────────────────────────────────


def test_create_and_get(store: ResearchProjectStore) -> None:
    pid = _make(store, "사내 교육자료 정리")
    p = store.get_project(pid)
    assert p is not None
    assert p.name == "사내 교육자료 정리"
    assert p.instructions == "지침 v1"
    assert p.version_no == 1


def test_create_sets_defaults(store: ResearchProjectStore) -> None:
    p = store.get_project(_make(store))
    assert p is not None
    assert p.sub_queries == DEFAULT_SUB_QUERIES
    assert p.gap_rounds == 1


def test_custom_project_id_is_kept(store: ResearchProjectStore) -> None:
    """시드 방이 옛 mode 값을 그대로 써야 기존 호출이 안 깨진다."""
    pid = _make(store, "중복성 검토", project_id="duplication")
    assert pid == "duplication"
    assert store.get_project("duplication") is not None


def test_delete_removes_only_its_own_rows(store: ResearchProjectStore) -> None:
    """방을 지워도 **다른 방**의 지침·대화는 남아야 한다."""
    keep = _make(store, "남는 방")
    drop = _make(store, "지울 방")
    store.save_instructions(keep, "남는 지침 v2")
    store.add_turn(keep, "user", "남는 질문")
    store.add_turn(drop, "user", "지울 질문")

    store.delete_project(drop)

    assert store.get_project(drop) is None
    assert store.get_project(keep) is not None
    assert len(store.list_versions(keep)) == 2
    assert len(store.list_turns(keep)) == 1
    assert store.list_turns(drop) == []


def test_update_does_not_touch_instructions(store: ResearchProjectStore) -> None:
    """검색설정 갱신이 지침 버전을 건드리면 안 된다."""
    pid = _make(store)
    store.update_project(pid, name="이름만 변경", sub_queries=10)
    p = store.get_project(pid)
    assert p is not None
    assert p.name == "이름만 변경"
    assert p.sub_queries == 10
    assert p.instructions == "지침 v1"
    assert len(store.list_versions(pid)) == 1


# ── 검색 설정 상한 ────────────────────────────────────────────────────────────


def test_search_settings_are_clamped(store: ResearchProjectStore) -> None:
    """상한이 없으면 근거 예산이 터지고 종합이 컨텍스트를 넘긴다."""
    pid = _make(store, sub_queries=999, top_k_per_query=0, gap_rounds=99)
    p = store.get_project(pid)
    assert p is not None
    assert p.sub_queries == LIMITS["sub_queries"][1]
    assert p.top_k_per_query == LIMITS["top_k_per_query"][0]
    assert p.gap_rounds == LIMITS["gap_rounds"][1]


def test_clamp_helper() -> None:
    assert clamp("gap_rounds", -5) == 0
    assert clamp("gap_rounds", 1) == 1
    assert clamp("sub_queries", 100) == 12


# ── 지침 버전관리 (핵심) ──────────────────────────────────────────────────────


def test_each_save_creates_a_version(store: ResearchProjectStore) -> None:
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.save_instructions(pid, "지침 v3")
    versions = store.list_versions(pid)
    assert [v.version_no for v in versions] == [3, 2, 1]
    assert store.get_project(pid).instructions == "지침 v3"  # type: ignore[union-attr]


def test_identical_save_does_not_create_version(store: ResearchProjectStore) -> None:
    """저장 버튼을 두 번 눌렀다고 이력이 지저분해지면 되돌릴 지점을 못 찾는다."""
    pid = _make(store)
    store.save_instructions(pid, "지침 v1")
    store.save_instructions(pid, "지침 v1")
    assert len(store.list_versions(pid)) == 1


def test_restore_moves_pointer_and_keeps_history(store: ResearchProjectStore) -> None:
    """**핵심 회귀** — 되돌리기가 이력을 자르지도, 부풀리지도 않는다 (CR-71 갱신).

    예전 정책은 복원이 **새 버전을 쌓는 것**이었다. 그러면 v1로 되돌릴 때마다 v4·v5가
    생겨 이력이 계속 부풀고 어디로 돌아갈지 찾기 어려웠다(사용자 지적).
    지금은 포인터만 옮긴다 — 옛 버전은 그대로 살아 있고 번호는 늘지 않는다.
    """
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.save_instructions(pid, "지침 v3")

    restored = store.restore_version(pid, 1)

    assert restored is not None
    assert restored.version_no == 1, "복원이 새 버전을 만들었다"
    assert restored.content == "지침 v1"
    # 옛 버전이 전부 살아 있어야 한다
    assert [v.version_no for v in store.list_versions(pid)] == [3, 2, 1]
    assert store.get_version(pid, 3).content == "지침 v3"  # type: ignore[union-attr]
    assert store.get_project(pid).instructions == "지침 v1"  # type: ignore[union-attr]


def test_restore_is_itself_reversible(store: ResearchProjectStore) -> None:
    """복원한 뒤 마음이 바뀌면 되돌아갈 수 있어야 한다.

    포인터 방식이라 오갈 때 버전이 늘지 않는다 (CR-71).
    """
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.restore_version(pid, 1)
    assert store.get_project(pid).instructions == "지침 v1"  # type: ignore[union-attr]
    store.restore_version(pid, 2)
    assert store.get_project(pid).instructions == "지침 v2"  # type: ignore[union-attr]
    assert len(store.list_versions(pid)) == 2, "오갈 때 버전이 늘었다"


def test_restore_unknown_version_returns_none(store: ResearchProjectStore) -> None:
    pid = _make(store)
    assert store.restore_version(pid, 99) is None
    assert len(store.list_versions(pid)) == 1


def test_restore_returns_the_selected_version(store: ResearchProjectStore) -> None:
    """복원하면 **고른 그 버전**을 돌려준다 (CR-71).

    예전에는 복사본을 새로 만들어 `note`에 "v1 복원"을 적었다. 이제 원본을 그대로
    가리키므로 별도 표식이 필요 없다 — 목록에서 "사용 중"으로 보인다.
    """
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    restored = store.restore_version(pid, 1)
    assert restored is not None
    assert restored.version_no == 1 and restored.content == "지침 v1"


# ── 대화 ──────────────────────────────────────────────────────────────────────


def test_turns_roundtrip_with_sources(store: ResearchProjectStore) -> None:
    pid = _make(store)
    store.add_turn(pid, "user", "버섯 스마트팜 검토")
    store.add_turn(pid, "assistant", "보고서 본문", sources=[{"n": 1, "doc_name": "a.pdf"}])
    turns = store.list_turns(pid)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[1].sources[0]["doc_name"] == "a.pdf"


def test_clear_turns_keeps_instructions(store: ResearchProjectStore) -> None:
    """대화를 비워도 지침 이력은 남아야 한다 — 별개의 물건이다."""
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.add_turn(pid, "user", "질문")
    store.clear_turns(pid)
    assert store.list_turns(pid) == []
    assert len(store.list_versions(pid)) == 2


# ── 시드 ──────────────────────────────────────────────────────────────────────


def test_seed_only_when_empty(store: ResearchProjectStore) -> None:
    seeds = [
        {"project_id": "duplication", "name": "중복성 검토", "instructions": "A"},
        {"project_id": "discovery", "name": "신규과제 발굴", "instructions": "B"},
    ]
    assert store.seed_if_empty(seeds) == 2
    assert store.seed_if_empty(seeds) == 0, "이미 방이 있는데 또 만들었다"
    assert len(store.list_projects()) == 2


def test_seed_does_not_resurrect_deleted(store: ResearchProjectStore) -> None:
    """사용자가 지운 방을 되살리면 안 된다 (E-68 교훈 — 지운 것의 부활)."""
    seeds = [{"project_id": "duplication", "name": "중복성 검토", "instructions": "A"}]
    store.seed_if_empty(seeds)
    _make(store, "내가 만든 방")
    store.delete_project("duplication")

    assert store.seed_if_empty(seeds) == 0, "지운 예시 방이 되살아났다"
    assert store.get_project("duplication") is None


def test_seed_projects_are_deletable(store: ResearchProjectStore) -> None:
    """예시 방도 일반 방과 동등하게 지울 수 있어야 한다."""
    store.seed_if_empty([{"project_id": "duplication", "name": "중복성", "instructions": "A"}])
    store.delete_project("duplication")
    assert store.list_projects() == []


# ── 진행 과정 보관 (CR-65) ────────────────────────────────────────────────────


def test_turn_keeps_steps(tmp_path: Path) -> None:
    """진행 과정이 턴과 함께 저장돼 나중에 다시 읽힌다."""
    s = ResearchProjectStore(tmp_path / "r.db")
    p = s.create_project(name="방", instructions="지침")
    s.add_turn(p.project_id, "assistant", "보고서", steps=["계획 수립", "검색 1/3", "종합"])
    got = s.list_turns(p.project_id)[-1]
    assert got.steps == ["계획 수립", "검색 1/3", "종합"]
    assert got.as_dict()["steps"][1] == "검색 1/3"


def test_old_turns_without_steps_still_load(tmp_path: Path) -> None:
    """컬럼 추가 **이전**에 만들어진 DB도 열리고, 옛 턴은 빈 목록이 된다.

    `CREATE TABLE IF NOT EXISTS`는 기존 표를 안 건드리므로 마이그레이션이 없으면
    이 경로에서 `no such column`으로 터진다.
    """
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE turns (turn_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,"
        " role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',"
        " sources_json TEXT NOT NULL DEFAULT '[]',"
        " attachments_json TEXT NOT NULL DEFAULT '[]',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO turns(turn_id, project_id, role, content) VALUES('t1','p1','assistant','옛 보고서');"
    )
    conn.commit()
    conn.close()

    s = ResearchProjectStore(db)  # 여기서 ALTER TABLE이 돌아야 한다
    old = s.list_turns("p1")
    assert len(old) == 1 and old[0].steps == []
    # 새 턴은 정상 저장된다
    s.add_turn("p1", "assistant", "새 보고서", steps=["a"])
    assert s.list_turns("p1")[-1].steps == ["a"]


# ── 리서치 한 건 삭제 (CR-66) ─────────────────────────────────────────────────


def test_delete_run_removes_pair(tmp_path: Path) -> None:
    """보고서를 지우면 짝인 질문도 함께 지운다 — 질문만 남으면 유령이 된다."""
    s = ResearchProjectStore(tmp_path / "r.db")
    p = s.create_project(name="방", instructions="지침")
    s.add_turn(p.project_id, "user", "첫 질문")
    a1 = s.add_turn(p.project_id, "assistant", "첫 보고서")
    s.add_turn(p.project_id, "user", "둘째 질문")
    s.add_turn(p.project_id, "assistant", "둘째 보고서")

    assert s.delete_run(p.project_id, a1.turn_id) == 2
    left = [t.content for t in s.list_turns(p.project_id)]
    assert left == ["둘째 질문", "둘째 보고서"], left


def test_delete_run_by_user_turn(tmp_path: Path) -> None:
    """질문 쪽 id로 지워도 보고서까지 함께 사라진다."""
    s = ResearchProjectStore(tmp_path / "r.db")
    p = s.create_project(name="방", instructions="지침")
    u = s.add_turn(p.project_id, "user", "질문")
    s.add_turn(p.project_id, "assistant", "보고서")
    assert s.delete_run(p.project_id, u.turn_id) == 2
    assert s.list_turns(p.project_id) == []


def test_delete_run_keeps_other_projects(tmp_path: Path) -> None:
    """다른 방 기록은 건드리지 않는다."""
    s = ResearchProjectStore(tmp_path / "r.db")
    a = s.create_project(name="A", instructions="x")
    b = s.create_project(name="B", instructions="x")
    ta = s.add_turn(a.project_id, "assistant", "A 보고서")
    s.add_turn(b.project_id, "assistant", "B 보고서")
    s.delete_run(a.project_id, ta.turn_id)
    assert [t.content for t in s.list_turns(b.project_id)] == ["B 보고서"]


def test_delete_run_unknown_id_is_noop(tmp_path: Path) -> None:
    """없는 id는 0을 돌려준다 — 라우트가 404로 바꾼다."""
    s = ResearchProjectStore(tmp_path / "r.db")
    p = s.create_project(name="방", instructions="지침")
    s.add_turn(p.project_id, "assistant", "보고서")
    assert s.delete_run(p.project_id, "없는id") == 0
    assert len(s.list_turns(p.project_id)) == 1


# ── 지침 버전 포인터·삭제 (CR-71) ─────────────────────────────────────────────


def _room(tmp_path: Path) -> tuple[ResearchProjectStore, str]:
    s = ResearchProjectStore(tmp_path / "ptr.db")
    p = s.create_project(name="방", instructions="v1 내용")
    s.save_instructions(p.project_id, "v2 내용")
    s.save_instructions(p.project_id, "v3 내용")
    return s, p.project_id


def test_restore_does_not_create_new_version(tmp_path: Path) -> None:
    """복원은 포인터만 옮긴다 (CR-71).

    예전에는 내용을 복사해 새 버전을 쌓아서, v2로 되돌리면 v4가 생겼다
    (사용자 지적: "복원만 해도 v4가 생겨버림").
    """
    s, pid = _room(tmp_path)
    assert len(s.list_versions(pid)) == 3
    got = s.restore_version(pid, 2)
    assert got is not None and got.version_no == 2
    assert len(s.list_versions(pid)) == 3, "버전이 늘었다"
    assert s.get_project(pid).version_no == 2
    assert s.get_project(pid).instructions == "v2 내용"


def test_restore_to_v1_works(tmp_path: Path) -> None:
    """v1으로도 돌아갈 수 있다 — 사용자가 "v1으로 갈 수 없다"고 지적한 지점."""
    s, pid = _room(tmp_path)
    assert s.restore_version(pid, 1) is not None
    assert s.get_project(pid).version_no == 1


def test_restore_current_version_is_allowed(tmp_path: Path) -> None:
    """현재 버전으로도 복원된다 — 편집 중인 내용을 버리는 유일한 경로다.

    v1만 있는 방에서는 이것이 없으면 되돌릴 방법이 아예 없다.
    """
    s = ResearchProjectStore(tmp_path / "one.db")
    p = s.create_project(name="한개", instructions="원본")
    got = s.restore_version(p.project_id, 1)
    assert got is not None and got.content == "원본"


def test_save_after_restore_continues_numbering(tmp_path: Path) -> None:
    """v2를 쓰는 중에 저장하면 v4가 된다 — 번호는 이력의 최대에서 이어진다."""
    s, pid = _room(tmp_path)
    s.restore_version(pid, 2)
    v = s.save_instructions(pid, "새 내용")
    assert v.version_no == 4
    assert s.get_project(pid).version_no == 4


def test_delete_version(tmp_path: Path) -> None:
    """이력에서 버전을 지울 수 있다 — 예전에는 지우는 경로가 없었다."""
    s, pid = _room(tmp_path)
    assert s.delete_version(pid, 2) is True
    assert [v.version_no for v in s.list_versions(pid)] == [3, 1]


def test_delete_current_moves_pointer(tmp_path: Path) -> None:
    """쓰는 중인 버전을 지우면 남은 것 중 가장 높은 번호로 옮긴다."""
    s, pid = _room(tmp_path)
    s.restore_version(pid, 2)
    assert s.delete_version(pid, 2) is True
    assert s.get_project(pid).version_no == 3


def test_cannot_delete_last_version(tmp_path: Path) -> None:
    """마지막 한 개는 남긴다 — 지침 0개인 방은 성립하지 않는다."""
    s = ResearchProjectStore(tmp_path / "one.db")
    p = s.create_project(name="한개", instructions="원본")
    assert s.delete_version(p.project_id, 1) is False
    assert len(s.list_versions(p.project_id)) == 1


def test_delete_unknown_version_is_false(tmp_path: Path) -> None:
    s, pid = _room(tmp_path)
    assert s.delete_version(pid, 99) is False


def test_old_db_without_pointer_column_migrates(tmp_path: Path) -> None:
    """포인터 컬럼이 없는 옛 DB도 열리고, 최신 버전을 쓰는 것으로 해석된다."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE projects (project_id TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT '',"
        " planner_hint TEXT NOT NULL DEFAULT '', sub_queries INTEGER NOT NULL DEFAULT 6,"
        " top_k_per_query INTEGER NOT NULL DEFAULT 5, gap_rounds INTEGER NOT NULL DEFAULT 1,"
        " max_evidence_chunks INTEGER NOT NULL DEFAULT 24, is_seed INTEGER NOT NULL DEFAULT 0,"
        " sort_order INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO projects(project_id, name) VALUES('p1','옛 방');"
        "CREATE TABLE instruction_versions (version_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,"
        " version_no INTEGER NOT NULL, content TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(project_id, version_no));"
        "INSERT INTO instruction_versions(version_id, project_id, version_no, content)"
        " VALUES('a','p1',1,'첫'),('b','p1',2,'둘');"
    )
    conn.commit()
    conn.close()

    s = ResearchProjectStore(db)  # 여기서 ALTER TABLE이 돌아야 한다
    got = s.get_project("p1")
    assert got is not None and got.version_no == 2 and got.instructions == "둘"

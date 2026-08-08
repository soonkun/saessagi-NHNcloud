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


def test_restore_appends_instead_of_truncating(store: ResearchProjectStore) -> None:
    """**핵심 회귀** — 되돌리기가 이력을 자르지 않는다."""
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.save_instructions(pid, "지침 v3")

    restored = store.restore_version(pid, 1)

    assert restored is not None
    assert restored.version_no == 4, "복원이 새 버전으로 쌓이지 않았다"
    assert restored.content == "지침 v1"
    # 옛 버전이 전부 살아 있어야 한다 — 복원도 되돌릴 수 있어야 하므로
    assert [v.version_no for v in store.list_versions(pid)] == [4, 3, 2, 1]
    assert store.get_version(pid, 3).content == "지침 v3"  # type: ignore[union-attr]
    assert store.get_project(pid).instructions == "지침 v1"  # type: ignore[union-attr]


def test_restore_is_itself_reversible(store: ResearchProjectStore) -> None:
    """복원한 뒤 마음이 바뀌면 되돌아갈 수 있어야 한다."""
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    store.restore_version(pid, 1)  # v3 = v1 내용
    store.restore_version(pid, 2)  # v4 = v2 내용
    assert store.get_project(pid).instructions == "지침 v2"  # type: ignore[union-attr]
    assert len(store.list_versions(pid)) == 4


def test_restore_unknown_version_returns_none(store: ResearchProjectStore) -> None:
    pid = _make(store)
    assert store.restore_version(pid, 99) is None
    assert len(store.list_versions(pid)) == 1


def test_restore_note_records_origin(store: ResearchProjectStore) -> None:
    """어느 버전에서 복원했는지 남아야 이력을 읽을 수 있다."""
    pid = _make(store)
    store.save_instructions(pid, "지침 v2")
    restored = store.restore_version(pid, 1)
    assert restored is not None
    assert "v1" in restored.note


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

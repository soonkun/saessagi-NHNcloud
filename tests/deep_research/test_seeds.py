# tests/deep_research/test_seeds.py
"""M_20 예시 방 시드·지침 이관 테스트 (CR-62).

**여기서 지키는 것은 이관이다.** 사용자가 설정 화면에서 딥 리서치 지침을 손봐 뒀다면
그건 시간을 들여 만든 물건이고, 방 체계로 옮기는 과정에서 사라지면 안 된다.
conf.yaml `app.agent_prompts.deep_research_*` → 방 지침 버전 1.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deep_research.prompts import SEED_INSTRUCTIONS
from deep_research.seeds import SEED_SPECS, build_seeds
from deep_research.store import ResearchProjectStore


@pytest.fixture
def store(tmp_path: Path) -> ResearchProjectStore:
    return ResearchProjectStore(tmp_path / "rp.db")


def test_seed_ids_match_legacy_modes() -> None:
    """옛 mode 값과 project_id가 같아야 기존 `/run-stream?mode=...` 호출이 안 깨진다."""
    assert {s["project_id"] for s in SEED_SPECS} == {"duplication", "discovery", "proposal"}


def test_build_seeds_uses_code_defaults_without_config() -> None:
    seeds = build_seeds(None)
    assert len(seeds) == 3
    by_id = {s["project_id"]: s for s in seeds}
    assert by_id["duplication"]["instructions"] == SEED_INSTRUCTIONS["duplication"]
    assert "legacy_prompt_key" not in by_id["duplication"], "내부 키가 새어 나갔다"


def test_custom_conf_prompt_is_migrated() -> None:
    """**핵심** — conf.yaml의 커스텀 지침이 방 지침으로 옮겨져야 한다."""
    agent_prompts = SimpleNamespace(
        deep_research_duplication="내가 고쳐 둔 중복성 지침",
        deep_research_discovery="",
        deep_research_proposal="   ",  # 공백만 = 미설정으로 취급
    )
    by_id = {s["project_id"]: s for s in build_seeds(agent_prompts)}

    assert by_id["duplication"]["instructions"] == "내가 고쳐 둔 중복성 지침"
    # 비어 있으면 코드 기본값
    assert by_id["discovery"]["instructions"] == SEED_INSTRUCTIONS["discovery"]
    assert by_id["proposal"]["instructions"] == SEED_INSTRUCTIONS["proposal"]


def test_migrated_prompt_lands_as_version_one(store: ResearchProjectStore) -> None:
    """이관된 지침이 실제로 버전 1로 들어가고 되돌릴 수 있어야 한다."""
    agent_prompts = SimpleNamespace(
        deep_research_duplication="이관된 지침",
        deep_research_discovery="",
        deep_research_proposal="",
    )
    store.seed_if_empty(build_seeds(agent_prompts))

    project = store.get_project("duplication")
    assert project is not None
    assert project.instructions == "이관된 지침"
    assert project.version_no == 1
    versions = store.list_versions("duplication")
    assert len(versions) == 1
    assert versions[0].content == "이관된 지침"


def test_broken_config_object_does_not_block_seeding() -> None:
    """설정 객체 모양이 달라도 예시 방은 만들어져야 한다 — 앱이 못 뜨면 안 된다."""

    class Exploding:
        def __getattr__(self, name: str) -> str:
            raise RuntimeError("설정 조회 실패")

    seeds = build_seeds(Exploding())
    assert len(seeds) == 3
    assert all(s["instructions"] for s in seeds), "폴백 기본값이 비었다"


def test_seeded_projects_carry_planner_hints(store: ResearchProjectStore) -> None:
    """모드별 관점 예시가 방의 planner_hint로 넘어왔는지 (기존 검색 품질 유지)."""
    store.seed_if_empty(build_seeds(None))
    project = store.get_project("duplication")
    assert project is not None
    assert "관점 예시" in project.planner_hint

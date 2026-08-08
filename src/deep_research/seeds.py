# src/deep_research/seeds.py
"""M_20 예시 방 3개 (CR-62).

CR-20의 모드 3개를 방으로 옮긴 것이다. **일반 방과 동등하다** — 수정·삭제 자유이고,
지우면 다시 만들지 않는다(`ResearchProjectStore.seed_if_empty`).

conf.yaml `app.agent_prompts.deep_research_*`에 커스텀 지침이 저장돼 있으면 그것을
버전 1로 넣는다. 사용자가 설정에서 고쳐 놓은 지침을 전환 과정에서 잃으면 안 된다.
"""

from __future__ import annotations

import logging
from typing import Any

from .prompts import SEED_INSTRUCTIONS

logger = logging.getLogger(__name__)

# 옛 mode 값을 project_id로 그대로 쓴다 — 기존 `/run-stream?mode=duplication` 호출이
# 그대로 동작한다(프론트 캐시·북마크·외부 스크립트 배려).
SEED_SPECS: tuple[dict[str, Any], ...] = (
    {
        "project_id": "duplication",
        "name": "과제 중복성 검토",
        "description": "제출 과제와 기존 연구의 중복·차별성을 냉정하게 판정합니다.",
        "icon": "🔍",
        "planner_hint": (
            "관점 예시: 핵심 주제어, 유사 기술/방법, 같은 대상(축종·작물·제도 등), "
            "선행 사업명, 관련 실험 방법."
        ),
        "legacy_prompt_key": "deep_research_duplication",
    },
    {
        "project_id": "discovery",
        "name": "신규과제 발굴",
        "description": "사내 연구 현황에서 공백을 찾아 신규 과제를 제안합니다.",
        "icon": "💡",
        "planner_hint": (
            "관점 예시: 해당 분야 과거 수행 과제, 최근 동향/이슈, 미해결 문제, "
            "인접 분야 기술, 반복 등장하는 조직·사업."
        ),
        "legacy_prompt_key": "deep_research_discovery",
    },
    {
        "project_id": "proposal",
        "name": "과제 계획서 초안",
        "description": "RFP와 사내 자료를 근거로 계획서 초안을 작성합니다.",
        "icon": "📝",
        "planner_hint": (
            "관점 예시: RFP 핵심 요구사항별 관련 연구, 유사 과제의 실험 방법, "
            "대상(축종·작물 등) 관련 축적 자료, 제도·규정."
        ),
        "legacy_prompt_key": "deep_research_proposal",
    },
)


def build_seeds(agent_prompts: Any = None) -> list[dict[str, Any]]:
    """`seed_if_empty()`에 넘길 방 정의 목록.

    `agent_prompts`(conf.yaml의 `app.agent_prompts` 객체)에 커스텀 지침이 있으면
    코드 기본값 대신 그것을 쓴다 — **설정에서 손봐 둔 지침의 이관 경로다.**
    """
    out: list[dict[str, Any]] = []
    for spec in SEED_SPECS:
        spec = dict(spec)
        key = spec.pop("legacy_prompt_key")
        instructions = SEED_INSTRUCTIONS[spec["project_id"]]
        custom = ""
        if agent_prompts is not None:
            try:
                custom = (getattr(agent_prompts, key, "") or "").strip()
            except Exception as exc:  # 설정 객체 모양이 달라도 시드는 되어야 한다
                logger.warning("예시 방 지침 이관 조회 실패 (%s): %s", key, exc)
        if custom:
            instructions = custom
            logger.info("예시 방 '%s' 지침을 conf.yaml 커스텀 값으로 이관", spec["name"])
        spec["instructions"] = instructions
        out.append(spec)
    return out


__all__ = ["SEED_SPECS", "build_seeds"]

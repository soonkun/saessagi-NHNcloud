# tests/graph_rag/test_extractor.py
"""M_19 EntityExtractor 단위 테스트 (스펙 §9)."""

from __future__ import annotations

from typing import Any

import pytest

from graph_rag.extractor import EntityExtractor

from .fakes import FakeCompleteJson

_TEXT = "농림축산식품부가 주관하는 스마트팜 혁신밸리 사업에 대구대학교가 참여한다. " * 2

_GOOD_RESPONSE: dict[str, Any] = {
    "entities": [
        {"name": "농림축산식품부", "type": "조직", "description": "중앙행정기관"},
        {"name": "스마트팜 혁신밸리", "type": "사업", "description": ""},
        {"name": "대구대학교", "type": "조직"},
    ],
    "relations": [
        {"source": "농림축산식품부", "target": "스마트팜 혁신밸리", "type": "주관"},
        {"source": "대구대학교", "target": "스마트팜 혁신밸리", "type": "참여"},
    ],
}


@pytest.mark.asyncio
async def test_extract_normal() -> None:
    """정상: JSON → Entity/Relation 생성, id는 정규화 이름+타입."""
    ext = EntityExtractor(complete_json=FakeCompleteJson({"농림축산식품부": _GOOD_RESPONSE}))
    result = await ext.extract(_TEXT)
    assert len(result.entities) == 3
    ids = {e.id for e in result.entities}
    assert "농림축산식품부:조직" in ids
    assert len(result.relations) == 2
    rel = result.relations[0]
    assert rel.source_id == "농림축산식품부:조직"
    assert rel.target_id == "스마트팜 혁신밸리:사업"


@pytest.mark.asyncio
async def test_extract_unknown_type_demoted() -> None:
    """엣지: 화이트리스트 외 타입은 '기타'로 강등."""
    resp = {"entities": [{"name": "새싹이", "type": "AI비서"}], "relations": []}
    ext = EntityExtractor(complete_json=FakeCompleteJson({"새싹이": resp}))
    result = await ext.extract("새싹이는 사내 AI 비서 프로그램의 마스코트 캐릭터다.")
    assert result.entities[0].type == "기타"


@pytest.mark.asyncio
async def test_extract_orphan_relation_dropped() -> None:
    """적대: relations가 entities에 없는 이름 참조 → 폐기."""
    resp = {
        "entities": [{"name": "A기관", "type": "조직"}],
        "relations": [
            {"source": "A기관", "target": "존재하지않는것", "type": "주관"},
            {"source": "A기관", "target": "A기관", "type": "자기참조"},
        ],
    }
    ext = EntityExtractor(complete_json=FakeCompleteJson({"A기관": resp}))
    result = await ext.extract("A기관 관련 문서 내용이 여기에 충분히 길게 들어간다.")
    assert result.relations == []


@pytest.mark.asyncio
async def test_extract_malformed_items_skipped() -> None:
    """적대: 항목이 dict가 아니거나 name 비어있으면 스킵 (전체 실패 금지)."""
    resp: dict[str, Any] = {
        "entities": ["문자열", {"name": "", "type": "조직"}, {"name": "정상기관", "type": "조직"}],
        "relations": "이상한값",
    }

    class _Fake(FakeCompleteJson):
        async def __call__(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return resp

    ext = EntityExtractor(complete_json=_Fake())
    result = await ext.extract("정상기관에 대한 설명이 여기에 충분히 길게 들어간다.")
    assert [e.name for e in result.entities] == ["정상기관"]
    assert result.relations == []


@pytest.mark.asyncio
async def test_extract_llm_failure_returns_empty() -> None:
    """엣지: LLM 예외 → 빈 결과 (예외 전파 금지)."""
    fake = FakeCompleteJson()
    fake.fail = True
    ext = EntityExtractor(complete_json=fake)
    result = await ext.extract("무엇이든 실패해야 하는 케이스의 본문 텍스트다.")
    assert result.entities == [] and result.relations == []


@pytest.mark.asyncio
async def test_extract_short_text_skipped_without_llm_call() -> None:
    """엣지: 초단문(20자 미만)은 LLM 호출 없이 빈 결과."""
    fake = FakeCompleteJson()
    ext = EntityExtractor(complete_json=fake)
    result = await ext.extract("짧다")
    assert result.entities == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_extract_huge_input_clipped() -> None:
    """적대: 1MB 입력 → 8천자 절단 후 호출."""
    fake = FakeCompleteJson()
    ext = EntityExtractor(complete_json=fake)
    await ext.extract("가" * 1_000_000)
    assert len(fake.calls) == 1
    assert len(fake.calls[0]) <= 8_000


@pytest.mark.asyncio
async def test_extract_duplicate_entities_merged() -> None:
    """정상: 같은 (이름,타입) 중복 → 1건 병합."""
    resp = {
        "entities": [
            {"name": "대구시", "type": "조직", "description": ""},
            {"name": " 대구시 ", "type": "조직", "description": "지자체"},
        ],
        "relations": [],
    }
    ext = EntityExtractor(complete_json=FakeCompleteJson({"대구시": resp}))
    result = await ext.extract("대구시가 추진하는 여러 사업들에 대한 설명 문서다.")
    assert len(result.entities) == 1


@pytest.mark.asyncio
async def test_extract_uses_custom_prompt_from_provider() -> None:
    """M_17 연동: prompt_provider가 커스텀 지침을 주면 그것이 system_prompt로 사용."""

    class _Capturing(FakeCompleteJson):
        def __init__(self) -> None:
            super().__init__({"농림축산식품부": _GOOD_RESPONSE})
            self.system_prompts: list[str] = []

        async def __call__(self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any) -> dict[str, Any]:
            self.system_prompts.append(system_prompt)
            return await super().__call__(system_prompt, user_prompt, json_schema, **kw)

    fake = _Capturing()
    ext = EntityExtractor(complete_json=fake, prompt_provider=lambda: "커스텀 추출 지침")
    await ext.extract(_TEXT)
    assert fake.system_prompts == ["커스텀 추출 지침"]


@pytest.mark.asyncio
async def test_extract_empty_provider_falls_back_to_default() -> None:
    """provider가 빈 문자열이면 기본값(EXTRACT_SYSTEM_PROMPT) 사용."""
    from graph_rag.extractor import EXTRACT_SYSTEM_PROMPT

    class _Capturing(FakeCompleteJson):
        def __init__(self) -> None:
            super().__init__({"농림축산식품부": _GOOD_RESPONSE})
            self.system_prompts: list[str] = []

        async def __call__(self, system_prompt: str, user_prompt: str, json_schema: Any, **kw: Any) -> dict[str, Any]:
            self.system_prompts.append(system_prompt)
            return await super().__call__(system_prompt, user_prompt, json_schema, **kw)

    fake = _Capturing()
    ext = EntityExtractor(complete_json=fake, prompt_provider=lambda: "")
    await ext.extract(_TEXT)
    assert fake.system_prompts == [EXTRACT_SYSTEM_PROMPT]

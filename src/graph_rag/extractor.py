# src/graph_rag/extractor.py
"""M_19 EntityExtractor — LLM 기반 엔티티·관계 추출 (스펙 §3.3).

IntentClassifier와 동일한 complete_json 콜러블을 주입받는다.
E-51 교훈: 소형 로컬 LLM의 출력 형식은 신뢰 불가 — 파싱을 형식 변형에
견고하게 하고, 실패 청크는 조용히 스킵한다(전체 실패 금지).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .types import ENTITY_TYPES, Entity, ExtractionResult, Relation, entity_id

logger = logging.getLogger(__name__)

# 추출 입력 상한 (적대 케이스: 초대형 청크)
_MAX_INPUT_CHARS = 8_000

EXTRACT_SYSTEM_PROMPT = """당신은 한국어 업무 문서에서 지식그래프를 구축하는 정보 추출기입니다.
주어진 텍스트에서 핵심 개체(엔티티)와 개체 간 관계를 추출해 JSON으로만 답하세요.

규칙:
- 엔티티 type은 반드시 다음 중 하나: 인물, 조직, 사업, 제도, 기술, 장소, 기타
- 문서에 실제로 등장하는 고유한 대상만 추출 (일반명사·대명사 제외)
- 관계(relations)의 source/target은 entities에 있는 name과 정확히 일치해야 함
- 관계 type은 짧은 한국어 술어 (예: 주관, 소속, 협력, 적용, 위치, 참여)
- 엔티티 3~10개, 관계 0~8개 수준으로 핵심만

예시 입력: "농림축산식품부가 주관하는 스마트팜 혁신밸리 사업에 A대학이 참여한다."
예시 출력:
{"entities":[{"name":"농림축산식품부","type":"조직","description":"중앙행정기관"},{"name":"스마트팜 혁신밸리","type":"사업","description":"농림축산식품부 주관 사업"},{"name":"A대학","type":"조직","description":""}],"relations":[{"source":"농림축산식품부","target":"스마트팜 혁신밸리","type":"주관","description":""},{"source":"A대학","target":"스마트팜 혁신밸리","type":"참여","description":""}]}"""

EXTRACT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type"],
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["source", "target", "type"],
            },
        },
    },
    "required": ["entities"],
}


class CompleteJsonFn(Protocol):
    """intent_gate.types.CompleteJsonFn과 동일 계약 (모듈 결합 없이 재선언)."""

    async def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]: ...


class EntityExtractor:
    """청크 텍스트 → (entities, relations). 실패 시 빈 결과 반환(예외 전파 금지)."""

    def __init__(self, complete_json: CompleteJsonFn, timeout_seconds: float = 45.0) -> None:
        self._complete_json = complete_json
        self._timeout_seconds = timeout_seconds

    async def extract(self, text: str) -> ExtractionResult:
        clipped = (text or "")[:_MAX_INPUT_CHARS].strip()
        if len(clipped) < 20:  # 의미있는 추출이 불가능한 초단문
            return ExtractionResult()

        try:
            async with asyncio.timeout(self._timeout_seconds + 2.0):
                raw: dict[str, Any] = await self._complete_json(
                    EXTRACT_SYSTEM_PROMPT,
                    clipped,
                    EXTRACT_JSON_SCHEMA,
                    max_tokens=1024,
                    temperature=0.0,
                    timeout_seconds=self._timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("EntityExtractor.extract 실패 (청크 스킵): %s", type(exc).__name__)
            return ExtractionResult()

        return self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> ExtractionResult:
        """LLM 출력 dict → 검증된 ExtractionResult (견고 파싱)."""
        if not isinstance(raw, dict):
            return ExtractionResult()

        entities: dict[str, Entity] = {}
        name_to_id: dict[str, str] = {}

        for item in raw.get("entities") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or len(name) > 100:
                continue
            type_ = str(item.get("type") or "").strip()
            if type_ not in ENTITY_TYPES:
                type_ = "기타"
            desc = str(item.get("description") or "").strip()[:300]
            eid = entity_id(name, type_)
            if eid not in entities:
                entities[eid] = Entity(id=eid, name=name, type=type_, description=desc)
            # 관계 해석용: 원문 이름 → id (타입 무관 첫 등록 우선)
            name_key = " ".join(name.split()).casefold()
            name_to_id.setdefault(name_key, eid)

        relations: list[Relation] = []
        seen_rel: set[tuple[str, str, str]] = set()
        for item in raw.get("relations") or []:
            if not isinstance(item, dict):
                continue
            src_name = " ".join(str(item.get("source") or "").split()).casefold()
            dst_name = " ".join(str(item.get("target") or "").split()).casefold()
            rel_type = str(item.get("type") or "").strip()[:50] or "관련"
            src_id = name_to_id.get(src_name)
            dst_id = name_to_id.get(dst_name)
            # entities에 없는 참조·자기참조는 폐기 (스펙 §3.3)
            if not src_id or not dst_id or src_id == dst_id:
                continue
            key = (src_id, dst_id, rel_type)
            if key in seen_rel:
                continue
            seen_rel.add(key)
            relations.append(
                Relation(
                    source_id=src_id,
                    target_id=dst_id,
                    type=rel_type,
                    description=str(item.get("description") or "").strip()[:300],
                )
            )

        return ExtractionResult(entities=list(entities.values()), relations=relations)

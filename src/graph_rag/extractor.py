# src/graph_rag/extractor.py
"""M_19 EntityExtractor — LLM 기반 엔티티·관계 추출 (스펙 §3.3).

IntentClassifier와 동일한 complete_json 콜러블을 주입받는다.
E-51 교훈: 소형 로컬 LLM의 출력 형식은 신뢰 불가 — 파싱을 형식 변형에
견고하게 하고, 실패 청크는 조용히 스킵한다(전체 실패 금지).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from .types import (
    ENTITY_TYPES,
    KEYWORD_ROLES,
    Entity,
    ExtractionResult,
    KeywordMention,
    ProjectExtraction,
    ProjectInfo,
    Relation,
    entity_id,
)

logger = logging.getLogger(__name__)

# 추출 입력 상한 (적대 케이스: 초대형 청크)
_MAX_INPUT_CHARS = 8_000

EXTRACT_SYSTEM_PROMPT = """당신은 한국어 업무 문서에서 지식그래프를 구축하는 정보 추출기입니다.
주어진 텍스트에서 핵심 개체(엔티티)와 개체 간 관계를 추출해 JSON으로만 답하세요.

규칙:
- 엔티티 type은 반드시 다음 중 하나: 인물, 조직, 사업, 제도, 기술, 장소, 기타
- 문서에 실제로 등장하는 고유한 대상만 추출 (일반명사·대명사 제외)
- 과제번호·RFP번호·사업번호·특허출원번호 같은 공식 식별번호는 가장 중요한 엔티티다.
  반드시 표기 원문 그대로(하이픈·자릿수·접두어 유지) type "사업"으로 추출하라.
  RFP·연구계획서·결과보고서·논문 사사(Acknowledgment)·특허가 같은 번호로 연결되므로
  절대 누락하거나 표기를 바꾸지 말 것
- 식별번호와 그 과제명·사업명이 함께 등장하면 관계로 잇는다 (관계 type: 식별)
- 같은 대상의 다른 표기(정식명칭 vs 약칭)는 문서에 더 자주 쓰인 하나의 name으로 통일
- 관계(relations)의 source/target은 entities에 있는 name과 정확히 일치해야 함
- 관계 type은 짧은 한국어 술어 (예: 주관, 소속, 협력, 적용, 위치, 참여, 식별)
- 엔티티 3~10개, 관계 0~8개 수준으로 핵심만

예시 입력: "본 연구는 농림축산식품부 과제(과제번호 RS-2024-00123456) '스마트팜 혁신밸리'의 지원을 받아 수행되었다."
예시 출력:
{"entities":[{"name":"RS-2024-00123456","type":"사업","description":"과제번호"},{"name":"스마트팜 혁신밸리","type":"사업","description":"농림축산식품부 과제"},{"name":"농림축산식품부","type":"조직","description":"중앙행정기관"}],"relations":[{"source":"RS-2024-00123456","target":"스마트팜 혁신밸리","type":"식별","description":"과제번호-과제명"},{"source":"농림축산식품부","target":"스마트팜 혁신밸리","type":"주관","description":""}]}"""

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


# ── CR-30: Project + 역할 키워드 추출 (문서 단위 1회 호출) ────────────────────

KEYWORD_EXTRACT_SYSTEM_PROMPT = """당신은 연구과제 문서(RFP·계획서·보고서)에서 과제 탐색에 필요한 최소 정보만
추출하는 정보 추출기입니다. JSON으로만 답하세요.

추출 대상 (이것만):
1. title: 과제명 (연구개발과제명). 없으면 ""
2. rfp_no: RFP 번호. 없으면 ""
3. project_no: 과제번호 (예: RS-2024-00123456, 321012-05). 표기 원문 그대로. 없으면 ""
4. keywords: 핵심 키워드 최대 10개. 각 키워드는 {"term", "role", "confidence"}

keywords 규칙:
- 연구대상(research_target), 적용기술(technology), 해결문제(problem),
  연구목적·산출물(outcome)을 나타내는 핵심 명사구만 추출한다
- role은 반드시 research_target | technology | problem | outcome 중 하나
- term은 짧은 명사 또는 명사구 (2~30자). 문장 전체 금지
- 목차, 파일명, 날짜, 번호, 금액, "연구", "개발", "기술" 같은 일반 단독어 금지
- 인물, 조직, 장소, 제도명은 추출하지 않는다
- 키워드를 기술유형코드 등으로 변환하지 말고 원문 표현 그대로 둔다
- confidence: 0.0~1.0 (문서 핵심 주제에 가까울수록 높게)

예시 출력:
{"title":"가루쌀 미강유 기능성분 구명 및 저장기간에 따른 품질변화 구명","rfp_no":"","project_no":"PJ01234567","keywords":[{"term":"가루쌀 미강유","role":"research_target","confidence":0.95},{"term":"기능성분 구명","role":"outcome","confidence":0.85},{"term":"저장기간 품질변화","role":"problem","confidence":0.8}]}"""

KEYWORD_EXTRACT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "rfp_no": {"type": "string"},
        "project_no": {"type": "string"},
        "keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "role": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["term", "role"],
            },
        },
    },
    "required": ["keywords"],
}

_MAX_KEYWORDS = 10
# 숫자·날짜·번호만인 term 배제 (규칙 4)
_NUMERIC_TERM_RE = re.compile(r"^[\d\s.\-_/년월일~()]+$")
# 일반 단독어 금지 목록 (규칙 4)
_GENERIC_TERMS = frozenset(
    {"연구", "개발", "기술", "사업", "과제", "방법", "결과", "목표", "내용", "활용", "분석"}
)


NORMALIZE_SYSTEM_PROMPT = """당신은 지식그래프의 엔티티 목록에서 같은 대상의 표기 변형(정식명칭/약칭/영문 표기/오탈자)을
찾아 병합 그룹을 제안하는 정리기입니다. JSON으로만 답하세요: {"groups": [["대표 표기", "변형1", ...], ...]}

규칙:
- 확실히 같은 실체인 경우만 묶는다. 조금이라도 애매하면 묶지 않는다 (보수적으로)
- 상하위 관계는 절대 묶지 않는다 (예: '경상북도'와 '경상북도 축산과'는 다른 대상)
- 서로 다른 과제번호·식별번호는 절대 묶지 않는다 (한 글자만 달라도 다른 과제일 수 있음)
- 각 그룹의 첫 원소는 가장 정식·완전한 표기로 한다
- 목록에 있는 이름만 사용한다 (새 이름 창작 금지)
- 병합할 것이 없으면 {"groups": []}

예시 입력:
- 농림축산식품부
- 농식품부
- 경상북도 축산과
예시 출력: {"groups": [["농림축산식품부", "농식품부"]]}"""

NORMALIZE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        },
    },
    "required": ["groups"],
}


class PromptProviderFn(Protocol):
    """추출 시스템 프롬프트 lazy 조회 — 빈 문자열이면 기본값(EXTRACT_SYSTEM_PROMPT)."""

    def __call__(self) -> str: ...


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

    def __init__(
        self,
        complete_json: CompleteJsonFn,
        timeout_seconds: float = 45.0,
        prompt_provider: "PromptProviderFn | None" = None,
    ) -> None:
        self._complete_json = complete_json
        self._timeout_seconds = timeout_seconds
        # M_17 연동: 호출 시점 lazy 조회 — 지침 저장 즉시 다음 추출부터 반영
        self._prompt_provider = prompt_provider

    def _system_prompt(self) -> str:
        if self._prompt_provider is not None:
            try:
                custom = (self._prompt_provider() or "").strip()
                if custom:
                    return custom
            except Exception as exc:
                logger.warning("graph_extract 지침 조회 실패 (기본값 사용): %r", exc)
        return EXTRACT_SYSTEM_PROMPT

    async def extract(self, text: str) -> ExtractionResult:
        clipped = (text or "")[:_MAX_INPUT_CHARS].strip()
        if len(clipped) < 20:  # 의미있는 추출이 불가능한 초단문
            return ExtractionResult()

        try:
            async with asyncio.timeout(self._timeout_seconds + 2.0):
                raw: dict[str, Any] = await self._complete_json(
                    self._system_prompt(),
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

    async def extract_project(self, doc_id: str, text: str) -> ProjectExtraction:
        """CR-30: 문서 1건 → 과제 정보(title/rfp_no/project_no) + 역할 키워드(≤10).

        실패 시 빈 키워드의 ProjectExtraction 반환 (예외 전파 금지).
        커스텀 지침(prompt_provider)이 있으면 그것을 시스템 프롬프트로 사용.
        """
        clipped = (text or "")[:_MAX_INPUT_CHARS].strip()
        if len(clipped) < 20:
            return ProjectExtraction(project=ProjectInfo(doc_id=doc_id))

        system = KEYWORD_EXTRACT_SYSTEM_PROMPT
        if self._prompt_provider is not None:
            try:
                custom = (self._prompt_provider() or "").strip()
                if custom:
                    system = custom
            except Exception as exc:
                logger.warning("graph_extract 지침 조회 실패 (기본값 사용): %r", exc)

        try:
            async with asyncio.timeout(self._timeout_seconds + 2.0):
                raw: dict[str, Any] = await self._complete_json(
                    system,
                    clipped,
                    KEYWORD_EXTRACT_JSON_SCHEMA,
                    max_tokens=1024,
                    temperature=0.0,
                    timeout_seconds=self._timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("extract_project 실패 (문서 스킵): %s", type(exc).__name__)
            return ProjectExtraction(project=ProjectInfo(doc_id=doc_id))

        return self._parse_project(doc_id, raw)

    def _parse_project(self, doc_id: str, raw: dict[str, Any]) -> ProjectExtraction:
        """LLM 출력 → 검증된 ProjectExtraction (견고 파싱, 보수적)."""
        if not isinstance(raw, dict):
            return ProjectExtraction(project=ProjectInfo(doc_id=doc_id))

        project = ProjectInfo(
            doc_id=doc_id,
            title=str(raw.get("title") or "").strip()[:200],
            rfp_no=str(raw.get("rfp_no") or "").strip()[:60],
            project_no=str(raw.get("project_no") or "").strip()[:60],
        )

        keywords: list[KeywordMention] = []
        seen: set[str] = set()
        for item in raw.get("keywords") or []:
            if not isinstance(item, dict):
                continue
            term = " ".join(str(item.get("term") or "").split())
            role = str(item.get("role") or "").strip()
            # 검증: 역할 화이트리스트, 길이 2~40, 숫자·날짜뿐 금지, 일반 단독어 금지
            if role not in KEYWORD_ROLES:
                continue
            if not (2 <= len(term) <= 40):
                continue
            if _NUMERIC_TERM_RE.match(term) or term in _GENERIC_TERMS:
                continue
            key = f"{term.casefold()}::{role}"
            if key in seen:
                continue
            seen.add(key)
            try:
                conf = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                conf = 0.0
            keywords.append(
                KeywordMention(doc_id=doc_id, raw_term=term, role=role, confidence=conf)
            )
            if len(keywords) >= _MAX_KEYWORDS:
                break

        return ProjectExtraction(project=project, keywords=keywords)

    async def propose_merges(self, names: list[str]) -> list[list[str]]:
        """CR-22 정규화: 같은 타입 엔티티 이름 목록 → 병합 그룹 제안 (보수적 검증).

        반환 그룹은 첫 원소 = 대표 표기. 실패·무효 시 빈 목록 (예외 전파 금지).
        """
        uniq = [n for n in dict.fromkeys(n.strip() for n in names) if n]
        if len(uniq) < 2:
            return []
        user = "\n".join(f"- {n}" for n in uniq[:300])
        try:
            async with asyncio.timeout(self._timeout_seconds + 2.0):
                raw = await self._complete_json(
                    NORMALIZE_SYSTEM_PROMPT,
                    user,
                    NORMALIZE_JSON_SCHEMA,
                    max_tokens=1024,
                    temperature=0.0,
                    timeout_seconds=self._timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("propose_merges 실패 (정규화 스킵): %s", type(exc).__name__)
            return []

        valid = set(uniq)
        out: list[list[str]] = []
        used: set[str] = set()  # 한 이름이 두 그룹에 들어가는 모순 방지
        for g in (raw.get("groups") if isinstance(raw, dict) else None) or []:
            if not isinstance(g, list):
                continue
            members: list[str] = []
            for x in g:
                m = str(x).strip()
                if m in valid and m not in used and m not in members:
                    members.append(m)
            if len(members) >= 2:
                out.append(members)
                used.update(members)
        return out

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

# src/kg/prompts.py
"""M_23 추출·판정 프롬프트와 JSON 스키마 (지침서 9·10·14.6·16.1장).

프롬프트는 **버전을 붙여** 후보에 함께 저장한다. 프롬프트를 고치면 예전 후보를 골라
다시 뽑을 수 있어야 하기 때문이다(지침서 4.6 재실행 안전성).

지침서는 문맥분류(9장)와 엔티티추출(10장)을 별도 호출로 나누지만, 여기서는 한 번의
호출에서 분류 필드를 함께 받는다(스펙 §4). 전 청크 2패스는 65만 호출이라 현실적이지
않고, 분류 정보 자체는 응답에 그대로 담기므로 잃는 것이 없다. 설정 `two_pass=True`면
지침서 원안대로 나눈다.
"""

from __future__ import annotations

from typing import Any

# 프롬프트를 고칠 때마다 올린다. 후보 행에 저장되어 "어떤 프롬프트로 뽑은 결과인지"를
# 남기고, 선택적 재추출의 기준이 된다.
PROMPT_VERSION = "kg-extract-v2"
EXTRACTOR_VERSION = "entity-v1"

# 지침서 7.3 — 원칙적으로 엔티티로 만들지 않는 표현.
# 이건 작물 목록 같은 도메인 사전이 아니라 **일반명사 불용어**다. 목록에 없는 일반명사가
# 새로 나와도 아래 구조적 규칙(단일 토큰 + 수식어 없음)이 잡는다.
GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "연구",
        "개발",
        "분석",
        "활용",
        "검토",
        "추진",
        "시스템화",
        "효율성",
        "우수성",
        "정확성",
        "활용성",
        "연구팀",
        "본 과제",
        "본 연구",
        "과제",
        "사업",
        "기술",
        "방법",
        "결과",
        "성과",
        "목표",
        "내용",
        "계획",
        "평가",
        "관리",
        "구축",
        "적용",
        "실증",
        "고도화",
    }
)

_SYSTEM = """당신은 농업 연구개발 과제의 중복성·차별성 분석을 위한 지식그래프 정보추출 시스템이다.

현재 과제와 직접 관련된 핵심 연구 엔티티만 추출하라.

[허용 엔티티 유형]
{entity_types}

[핵심 규칙]
1. 원문에 명시된 정보만 추출한다. 원문에 없는 내용을 추론하지 않는다.
2. 모든 명사를 추출하는 것이 아니다. 과제 비교에 필요한 핵심 개념만 추출한다.
3. 원문 근거(evidence)가 없으면 추출하지 않는다. evidence는 청크 본문을 **그대로** 인용한다.
4. 선행연구·인용된 사례를 현재 과제의 기술이나 성과로 귀속하지 않는다.
5. 계획, 실제 수행, 결과, 기대성과를 구분한다.
6. 서로 다른 작물·품종·병해충·지역은 통합하지 않는다. 각각의 대상을 target_terms에 적는다.
7. 기술과 산출물을 구분한다. "○○ 기술"과 "○○ 모형"은 다른 유형이다.
8. 상위개념과 하위개념을 같은 엔티티로 처리하지 않는다.
9. 애매한 후보를 추가하는 것보다 추출하지 않는 것을 우선한다.
10. 새로운 엔티티 유형을 만들지 않는다.
11. 한 청크에서 최대 {max_entities}개까지만 추출한다.
12. JSON 이외의 내용을 출력하지 않는다.

[추출 제외 대상]
연구/개발/분석/활용/검토/추진 같은 일반 행위 명사, 효율성·우수성 같은 추상 명사,
단순한 연도와 숫자, 일반 행정 용어, 원문에 구체화되지 않은 "인공지능 기술",
단순히 참고문헌에서 언급된 기술.

[진술 상태(statement_status)]
REQUIREMENT PLANNED IN_PROGRESS ACTUAL RESULT EXPECTED PRIOR_RESEARCH CITATION_ONLY LIMITATION UNCERTAIN
- 완결보고서라도 계획·예정·필요성 표현은 ACTUAL로 판별하지 않는다.
- **"최종목표", "세부목표", "성과목표", "○○ 개발"처럼 목표를 서술한 문장은 PLANNED다.**
  완결보고서에 적혀 있다는 이유로 ACTUAL로 판별하지 않는다.
- 수행 사실("~하였다", "~을 완료", "~건 확보")이 명시된 경우에만 ACTUAL 또는 RESULT로 판별한다.
- 사업명·과제명 자체는 목표가 아니다. 엔티티로 만들지 않는다.
- 다른 연구·논문·과제·사례는 PRIOR_RESEARCH 또는 CITATION_ONLY로 판별한다.

[현재 과제 관련성(current_project_relevance)]
DIRECT INDIRECT NONE UNCERTAIN
- 불분명하면 UNCERTAIN을 선택한다."""

_USER = """[문서 정보]
문서 ID: {doc_id}
문서명: {doc_name}
문서 유형: {document_type}
문서 연도: {year}
과제번호: {project_no}
과제명: {project_title}
소제목(추정): {section_hint}
청크 ID: {chunk_id}
페이지: {page}

[출력 JSON]
{{
  "chunk_id": "{chunk_id}",
  "primary_statement_type": "이 청크 전체의 진술 상태",
  "current_project_relevance": "DIRECT | INDIRECT | NONE | UNCERTAIN",
  "extraction_priority": "HIGH | MEDIUM | LOW | SKIP",
  "entities": [
    {{
      "temp_id": "E1",
      "type": "허용 유형 중 하나",
      "name": "원문 기반 표현",
      "canonical_name_candidate": "정규화 후보명",
      "description": "현재 과제에서의 의미",
      "status": "진술 상태",
      "current_project_relevance": "DIRECT | INDIRECT | NONE | UNCERTAIN",
      "target_terms": ["작물·품종·병해충·지역 등 이 엔티티가 겨냥한 대상"],
      "specificity": "HIGH | MEDIUM | LOW",
      "evidence": "청크 본문에서 그대로 인용한 근거 문장",
      "confidence": 0.0
    }}
  ]
}}

엔티티가 없으면 entities를 빈 배열로 반환한다.

[청크 본문]
{chunk_text}"""

# 재시도용 축약 프롬프트 (지침서 11.2 2차 실패 대응).
# 출력이 길어 잘리는 경우가 많아 필드를 줄인다.
_USER_RETRY = """[문서 정보] 유형 {document_type} · 과제 {project_no} · 청크 {chunk_id}

아래 본문에서 현재 과제의 핵심 연구 엔티티만 최대 {max_entities}개 뽑아 JSON만 출력하라.
근거 문장은 본문에서 그대로 인용한다. 없으면 빈 배열.

{{"chunk_id":"{chunk_id}","entities":[{{"temp_id":"E1","type":"","name":"","status":"",
"current_project_relevance":"","target_terms":[],"evidence":"","confidence":0.0}}]}}

[청크 본문]
{chunk_text}"""


def extraction_json_schema(entity_types: list[str], max_entities: int) -> dict[str, Any]:
    """구조화 출력 스키마 (지침서 11.1). 유형·상태를 enum으로 강제한다."""
    return {
        "type": "object",
        "properties": {
            "chunk_id": {"type": "string"},
            "primary_statement_type": {"type": "string"},
            "current_project_relevance": {
                "type": "string",
                "enum": ["DIRECT", "INDIRECT", "NONE", "UNCERTAIN"],
            },
            "extraction_priority": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW", "SKIP"],
            },
            "entities": {
                "type": "array",
                "maxItems": max_entities,
                "items": {
                    "type": "object",
                    "properties": {
                        "temp_id": {"type": "string"},
                        "type": {"type": "string", "enum": list(entity_types)},
                        "name": {"type": "string"},
                        "canonical_name_candidate": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                        "current_project_relevance": {
                            "type": "string",
                            "enum": ["DIRECT", "INDIRECT", "NONE", "UNCERTAIN"],
                        },
                        "target_terms": {"type": "array", "items": {"type": "string"}},
                        "specificity": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["type", "name", "evidence", "confidence"],
                },
            },
        },
        "required": ["entities"],
    }


def extraction_prompts(
    *,
    doc_id: str,
    doc_name: str,
    document_type: str,
    year: int | None,
    project_no: str,
    project_title: str,
    section_hint: str,
    chunk_id: str,
    page: int | None,
    chunk_text: str,
    entity_types: list[str],
    max_entities: int,
    retry: bool = False,
) -> tuple[str, str]:
    """(system, user) 프롬프트를 만든다."""
    system = _SYSTEM.format(
        entity_types="\n".join(entity_types),
        max_entities=max_entities,
    )
    if retry:
        user = _USER_RETRY.format(
            document_type=document_type or "UNKNOWN",
            project_no=project_no or "미상",
            chunk_id=chunk_id,
            max_entities=max_entities,
            chunk_text=chunk_text,
        )
    else:
        user = _USER.format(
            doc_id=doc_id,
            doc_name=doc_name or "미상",
            document_type=document_type or "UNKNOWN",
            year=year if year is not None else "미상",
            project_no=project_no or "미상",
            project_title=project_title or "미상",
            section_hint=section_hint or "미상",
            chunk_id=chunk_id,
            page=page if page is not None else "미상",
            chunk_text=chunk_text,
        )
    return system, user

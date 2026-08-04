# M_23 지식그래프 구축 파이프라인 (KG Pipeline)

버전 v1 · 2026-08-02 · 근거: `Claude_Code_Neo4j_Knowledge_Graph_Strategy.md`,
현황 조사 `docs/current-state-analysis.md`

---

## 1. 목적

연구개발 문서(완결보고서·RFP)에서 **과제 중복성·차별성 분석에 쓸 수 있는** 지식그래프를
만든다. 핵심은 노드를 많이 만드는 것이 아니라 **잘못된 노드와 잘못된 병합을 줄이는 것**이다.

답할 수 있어야 하는 질문:
- 신규 과제와 기존 과제의 연구문제·대상·기술·방법·데이터·성과가 겹치는가?
- 과제명이 비슷해도 **작물·품종·병해충·지역이 다른가?**
- 계획한 연구와 실제 수행한 연구가 어떻게 다른가?
- 선행연구의 성과를 후속과제가 활용하는가?
- 답변에 원문 문서·페이지·청크 근거를 제시할 수 있는가?

## 2. 절대 원칙

1. **LLM은 그래프를 만들지 않는다.** LLM은 *후보*를 제안할 뿐이고, 정규 노드는 검증·정규화를
   통과한 것만 만든다. 후보 저장 단계를 건너뛰는 경로는 두지 않는다.
2. **근거 없는 후보는 적재하지 않는다.** 모든 후보는 `doc_id`·`chunk_id`·`page`·`evidence`를
   갖고, evidence는 **원문 대조 검증**을 통과해야 한다.
3. **이름 유사도만으로 병합하지 않는다.** 작물·품종·병해충·지역·유형이 다르면 자동 병합 금지.
4. **계획·실적·결과·선행연구를 구분한다.** 완결보고서의 문장을 전부 실적으로 취급하지 않는다.
5. **기존 파싱·청킹·임베딩을 건드리지 않는다.** 이미 임베딩된 청크를 읽기만 한다.
6. **재실행 안전.** 같은 문서를 다시 처리해도 노드가 중복 생성되지 않는다.

## 3. 파이프라인

```
LanceDB 청크(읽기 전용)
   ↓ (1) 문서 메타 확정      folder→document_type/연도, project_no/rfp_no
   ↓ (2) 청크 선별           문서당 N개 (기본 12)
   ↓ (3) 후보 추출 (LLM)     청크 1개 = 호출 1회
   ↓ (4) 검증                유형·상태 화이트리스트, evidence 원문 대조, 개수 상한
   ↓ (5) 후보 저장 (SQLite)  status=PENDING
   ↓ (6) 문서 단위 통합      같은 문서 안의 표기 변형 묶기 + 별칭
   ↓ (7) 전역 정규화         정확→별칭→문자열→벡터→LLM 판정 (+ 병합 금지 규칙)
   ↓ (8) 관계 추출 (LLM)     **검증된 엔티티 목록 안에서만**
   ↓ (9) Neo4j 적재          배치 UNWIND, canonical_id MERGE, 멱등
   ↓ (10) 평가               골든셋 지표 (Precision·False Merge 우선)
```

각 단계는 독립 실행 가능하고, 실패한 단위만 재실행할 수 있다.

## 4. 지침서에서 준용하지 않는 것과 근거

| 지침서 | 본 스펙 | 근거 |
|--------|---------|------|
| 청크마다 문맥분류 1회 + 추출 1회 | **문서당 12청크, 1패스 통합** | 전 청크 2패스는 65만 호출·약 900시간. 분류 필드는 추출 응답에 함께 담아 정보 손실 없음. `two_pass`로 복원 가능 |
| 여러 청크 묶어 처리 | **청크 1개 = 호출 1회** | 근거 귀속이 섞이면 Evidence Accuracy가 무너진다 — 지침서 최우선 지표 |
| Section 노드 계층 | **1단계 제외** | `section` 컬럼이 325,570건 전부 비어 있음. 재파싱은 전제 위반. 소제목은 Mention 속성으로 남겨 사후 보강 경로 확보 |
| 전 청크 Chunk 노드 | **Mention이 참조하는 청크만** | 32만 노드는 이득 없이 그래프만 무겁게 함. 역추적성 동일 |
| 청크 행에 문서 메타 전파 | **문서 단위 SQLite 표** | LanceDB 스키마 변경 = 재임베딩 위험 |
| Assertion 노드 | **관계 속성 + 복합키** | 기존 저장소 스타일과 일치, 근거 보존은 동일 |
| 2차 엔티티 유형·기술유형코드 | **2단계로 유예** | 지침서 자체 단계 구분(7.2) 따름. 공식 코드표 부재 |

## 5. 데이터 모델

### 5.1 후보 저장소 (SQLite, `data/kg_candidates.db`)

정규 노드가 되기 전 모든 산출물이 여기 머문다. Neo4j와 분리하는 이유는 (a) 실패·재시도·
검토 대기 상태를 그래프에 노출하지 않기 위해, (b) 재실행 시 LLM 호출을 건너뛰기 위해서다.

- `documents` — doc_id PK, doc_name, folder_id/name, document_type, year, project_no,
  rfp_no, title, chunk_count, extract_state, updated_at
- `entity_candidates` — candidate_id PK, doc_id, chunk_id, page, temp_id, entity_type,
  surface_form, canonical_name_candidate, description, statement_status,
  project_relevance, specificity, evidence, confidence, section_hint,
  extractor_model, extractor_version, prompt_version, status, doc_entity_id,
  canonical_id, error_message, created_at
- `doc_entities` — 문서 단위 통합 결과. doc_entity_id PK, doc_id, entity_type,
  canonical_name_candidate, aliases(JSON), statuses(JSON), target_key,
  source_candidate_ids(JSON), review_required, canonical_id
- `canonical_entities` — canonical_id PK, entity_type, canonical_name, normalized_name,
  aliases(JSON), target_key, review_status, created_at, updated_at
- `relation_candidates` — relation_candidate_id PK, doc_id, chunk_id, project_id,
  source_canonical_id, relation_type, target_canonical_id, status(statement),
  evidence, confidence, page, state, created_at
- `jobs` — job_id PK, job_type, scope, state, counts(JSON), started_at, finished_at, error

후보 상태: `PENDING | MATCHED | NEW_ENTITY | REVIEW_REQUIRED | REJECTED | FAILED`

### 5.2 Neo4j (적재 대상)

```
(:Project {project_id, title, normalized_title, start_year, end_year, status})
(:Document {doc_id, document_type, title, year, doc_name})
(:Chunk {chunk_id, page, text_preview})
(:Mention {mention_id, surface_form, entity_type, statement_status,
           project_relevance, confidence, evidence, section_hint,
           extractor_model, extractor_version})
(:CanonicalEntity:<Type> {canonical_id, canonical_name, normalized_name,
                          aliases, target_key, review_status})

(Project)-[:HAS_DOCUMENT]->(Document)-[:HAS_CHUNK]->(Chunk)-[:HAS_MENTION]->(Mention)
(Mention)-[:REFERS_TO]->(CanonicalEntity)
(Project)-[:HAS_PROBLEM|HAS_OBJECTIVE|TARGETS|USES_TECHNOLOGY|USES_METHOD|
           USES_DATASET|PRODUCES {status, confidence, mention_count,
           source_document_ids, evidence}]->(CanonicalEntity)
```

유형 라벨: `ResearchProblem | ResearchObjective | ResearchTarget | Technology |
Method | Dataset | Output` (1차). 공통 라벨 `CanonicalEntity`를 함께 부여한다.

### 5.3 상태 enum

- `statement_status`: `REQUIREMENT | PLANNED | IN_PROGRESS | ACTUAL | RESULT |
  EXPECTED | PRIOR_RESEARCH | CITATION_ONLY | LIMITATION | UNCERTAIN`
- `project_relevance`: `DIRECT | INDIRECT | NONE | UNCERTAIN`
- `NONE`·`CITATION_ONLY`·`PRIOR_RESEARCH`는 **현재 과제의 기술·성과 관계로 만들지 않는다.**

## 6. 병합 금지 규칙 (하드 규칙 — LLM 판정보다 우선)

`target_key`(작물·품종·병해충·지역 등 핵심 대상을 정규화한 키)가 다르면 **자동 병합하지
않는다.** 아래 중 하나라도 해당하면 자동 병합 금지:

- 엔티티 유형이 다름 (기술 vs 산출물)
- 핵심 대상이 다름 (사과 vs 복숭아)
- 상위개념 vs 하위개념 (과수 vs 사과)
- 근거 부족 / 신뢰도 미달

LLM 판정값 `SAME | RELATED | BROADER | NARROWER | DIFFERENT | UNCERTAIN` 중
**SAME만** 병합하고, 나머지는 별도 노드로 두거나 검토 큐로 보낸다.

## 7. 설정 (`app.knowledge_graph`)

임계값·모델·예산은 전부 설정으로 바꿀 수 있다. 코드에 상수로 박지 않는다.
기본값은 `src/kg/config.py` 참조.

## 8. 실행 제어

- 범위: 전체 / 폴더 / 프로젝트 / 문서 / 실패분만
- 상태: `PENDING | RUNNING | PAUSED | COMPLETED | PARTIAL_FAILED | FAILED | CANCELLED`
- 배치·청크 경계마다 취소 플래그를 확인해 **강제 종료 없이** 중단한다.
- 대화가 진행 중이면 배경 추출을 양보한다(`rag_watch.activity.conversation_active`).

## 9. 검증 게이트

**전체 문서 일괄 실행은 아래를 통과해야만 가능하다.**

1. 10문서 소규모 테스트 통과 (완결보고서·RFP 혼합)
2. 골든셋 평가에서 Entity Precision·False Merge Rate 기준 충족
3. 100문서 테스트 통과

게이트는 코드로 강제한다 — 평가 결과 파일이 없거나 기준 미달이면 전체 실행 명령이 거부된다.

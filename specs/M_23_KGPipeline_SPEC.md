# M_23 지식그래프 구축 파이프라인 (KG Pipeline)

버전 v2 · 2026-08-06 · 근거: `Claude_Code_Neo4j_Knowledge_Graph_Strategy.md`,
현황 조사 `docs/current-state-analysis.md`, 후보 216,509건 실측(§4.1)

> **v2 변경 요약** (CR-61) — 1~5단계(추출)가 끝나 후보 216,509건이 쌓인 뒤, 그 데이터를
> 실측하고 6~10단계를 확정했다. 세 가지가 v1에서 바뀌었다.
> 1. **7단계 벡터·LLM 판정을 뺐다.** 규칙 퍼지 병합의 실측 효과가 0.5%였다(§4.1-A).
> 2. **8단계 LLM 관계 추출을 2단계로 유예하고**, `entity_type`에서 관계를 유도한다(§4.1-B).
> 3. **연결성 회복 장치 3종을 넣었다** — `target_key` 승격 · `document_frequency` ·
>    `SHARES_ENTITY`. 실측 결과 엔티티의 91.5%가 단일 문서 전용이라, 그대로 적재하면
>    CR-34의 "별들의 숲"이 174,985개 규모로 재현된다(§4.1-C).

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
   ────────────────────── 여기까지 완료 (216,509건 / 6,121문서) ──────────────────────
   ↓ (6) 문서 단위 통합      같은 문서 안의 표기 변형 묶기 + 별칭
   ↓ (7) 전역 정규화         정확일치 → 규칙 퍼지(대표자 비교) (+ 병합 금지 규칙)
   ↓ (8) 관계 유도 (LLM 없음) entity_type→관계 1:1 사상 + target_key APPLIED_TO
   ↓ (9) Neo4j 적재          배치 UNWIND, build_id 세대 관리, 멱등
   ↓ (10) 산출 리포트         df 분포·검토 큐·연결성 지표
```

각 단계는 독립 실행 가능하고, 실패한 단위만 재실행할 수 있다.
**6~10단계는 LLM을 호출하지 않는다** — 근거는 §4.1.

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
| 7단계 벡터 후보군 + LLM 동일성 판정 | **정확일치 + 규칙 퍼지만** | 규칙 퍼지의 실측 효과가 0.5%. 남는 것은 애초에 병합하면 안 되는 쌍이다 (§4.1-A) |
| 8단계 LLM 관계 추출 | **entity_type에서 유도, LLM 2단계 유예** | 유형 7종이 스펙의 Project 관계 7종과 1:1로 맞는다. 청크 28,976개 재호출(20~30시간) 없이 같은 엣지를 얻는다 (§4.1-B) |
| 10단계 골든셋 평가 | **산출 리포트 + 검토 큐로 대체** | 골든셋은 사람이 라벨링해야 만들어진다. 없는 것을 있는 척하지 않는다. 게이트 재해석은 §9 |

### 4.1 실측 — 위 결정의 근거

후보 216,509건이 쌓인 뒤 `data/kg_candidates.db`로 직접 측정했다(2026-08-06).

**A. 규칙 퍼지 병합은 거의 아무것도 하지 않는다.**
`RESEARCH_TARGET` 39,795개(정확일치 후)에 토큰 역색인 블로킹 + `merge.merge_decision`을
전량 적용 → **39,601개**. 감소 0.5%, 최대 흡수 그룹 2개, 소요 39.6초.
`merge.py`의 R3(차이 토큰이 실질어면 병합 금지)이 설계대로 보수적이라 긴 서술형 이름은
서로 붙지 않는다. **정확일치가 일을 다 한다** (216,509 → 174,985, 3.0초).
따라서 벡터 후보군·LLM 판정을 얹어도 얻을 것이 거의 없고, 오히려 지침서 최우선 지표인
False Merge를 키우는 쪽으로만 위험하다.

**B. `entity_type`이 이미 관계다.** §5.2의 Project 관계 7종은 `DEFAULT_ENTITY_TYPES` 7종과
정확히 1:1이다 (`TECHNOLOGY`→`USES_TECHNOLOGY`, `OUTPUT`→`PRODUCES` …). 관계를 LLM으로
다시 뽑을 이유가 없다. 엔티티↔엔티티 관계(`ADDRESSES`·`IMPROVES`·`USES_PRIOR_OUTPUT`)만
LLM이 필요하고, 그건 2단계로 미룬다.

**C. 진짜 문제는 병합이 아니라 연결성이다.**

```
정확일치 후 엔티티 174,985개
  1개 문서에만 등장 : 160,080 (91.5%)   ← 그래프 연결에 기여 못 함
  2개 이상 문서 공유:  14,905 ( 8.5%)   ← 중복성 분석의 실제 신호
```

연결성은 이름 길이에 반비례한다 — 1토큰 25.2% 공유 / 6토큰+ 4.5% 공유. 짧고 구체적인
명사(`토마토`)는 연결되고 긴 서술형 구절은 그 문서에만 있다. 게다가 **가장 많이 연결된
노드는 전부 행정 상용구**다(`산업재산권 출원` 206문서 · `학술발표` 139 · `논문 게재 SCI` 114).
이대로 적재하면 CR-34의 "별들의 숲"이 6,276개에서 174,985개 규모로 재현된다.

그래서 세 장치를 넣는다 (전부 LLM 불필요):

1. **`target_key` 승격** — 추출 때 작물·병해충·지역을 정규화해 담아뒀으나 지금은 병합 거부권
   (R2)에만 쓰이고 그래프에 오르지 않는다. 32,063개 값, 문서 커버리지 4,753/6,121(78%),
   상위 값이 곧 도메인 허브다(`벼` 343문서 · `콩` 193 · `토마토` 155). 이를 `RESEARCH_TARGET`
   정규 엔티티로 올리고 `APPLIED_TO`로 잇는다. 문서 고유의 긴 엔티티가 공유 허브에 매달려
   별들의 숲이 실제 네트워크가 된다.
2. **`document_frequency`(df)** — 전 엔티티에 계산해 1급 속성으로 둔다. df=1은 연결력 없음,
   df≫1은 변별력 없음. **한 숫자가 두 문제를 다 처리한다.** IDF와 같은 발상이고 M_19가
   `_RELATED_MAX_FANOUT=15`로 이미 쓰던 개념이다. **삭제가 아니라 가중치로 쓴다** — 코퍼스가
   계속 늘어나므로 오늘의 df=1이 내일 허브가 된다.
3. **`SHARES_ENTITY` 문서↔문서 가중 엣지** — 중복성 분석이 실제로 묻는 것. 팬아웃 상한으로
   상용구 허브가 만드는 가짜 유사도를 잘라낸다.

**D. 계획서↔완결보고서 연결은 현재 데이터로 거의 불가능하다.**
`project_no`는 3,035/6,121 문서에만 있고 2,656개로 흩어져 있다(한 과제 최대 3문서).
RFP 1,984건은 `project_no`·`rfp_no`가 **둘 다 비어 있다.** §1의 질문 중 "계획한 연구와
실제 수행한 연구가 어떻게 다른가"는 이번 단계에서 답할 수 없다. 중복성 분석은 문서 대
문서로 돈다. 제목·파일명 기반 매칭은 별도 과제로 남긴다.

**E. `statement_status`가 사실상 비어 있다.** 216,509건 중 210,676건(97.3%)이 `UNCERTAIN`.
프롬프트 JSON 스키마의 `required`에 빠져 모델이 생략했다(`prompts.py`는 `type,name,
evidence,confidence`만 필수). §2의 원칙 4가 데이터로 뒷받침되지 않는 상태다.
재추출(26시간) 대신 **문서유형에서 유도**한다 — §5.3 참조.

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
(:Project {project_id, project_id_source, title, normalized_title, year, document_count})
(:Document {doc_id, document_type, title, year, doc_name, folder_name})
(:Chunk {chunk_id, page, text_preview})
(:Mention {mention_id, surface_form, entity_type, statement_status,
           derived_status, status_source, project_relevance, confidence,
           evidence, page, section_hint, extractor_model, extractor_version})
(:CanonicalEntity:<Type> {canonical_id, canonical_name, normalized_name,
                          aliases, target_key, review_status,
                          document_frequency, mention_count, is_boilerplate,
                          from_target_key})

(Project)-[:HAS_DOCUMENT]->(Document)-[:HAS_CHUNK]->(Chunk)-[:HAS_MENTION]->(Mention)
(Mention)-[:REFERS_TO]->(CanonicalEntity)
(Project)-[:HAS_PROBLEM|HAS_OBJECTIVE|TARGETS|USES_TECHNOLOGY|USES_METHOD|
           USES_DATASET|PRODUCES {derived_status, status_source, confidence,
           mention_count, source_document_ids, evidence}]->(CanonicalEntity)
(CanonicalEntity)-[:APPLIED_TO {mention_count}]->(:CanonicalEntity:ResearchTarget)
(Document)-[:SHARES_ENTITY {weight, shared_count, entity_types}]-(Document)
```

유형 라벨: `ResearchProblem | ResearchObjective | ResearchTarget | Technology |
Method | Dataset | Output` (1차). 공통 라벨 `CanonicalEntity`를 함께 부여한다.

**모든 노드는 `build_id`를 갖는다.** 대표 이름이 바뀌면 `canonical_id` 해시도 바뀌므로,
적재 후 `build_id`가 다른 잔여 노드를 배치 삭제해야 고아 노드가 남지 않는다. 이것이
9단계의 멱등성을 보장하는 장치다.

**`document_frequency`는 조회·시각화의 1급 입력이다.** 그래프 탭 기본 뷰는 df≥2만 그리고
(174,985개를 다 그리면 아무것도 안 보인다), 검색 랭킹은 df 역가중을 쓴다(§4.1-C).
`is_boilerplate`는 df 상위 분위수 + `prompts.GENERIC_TERMS`로 판정하며 **하드코딩 목록을
두지 않는다** — `merge.py` 서두의 원칙(사전은 누락된 항목에서 조용히 실패한다)과 같다.

### 5.3 상태 enum

- `statement_status`: `REQUIREMENT | PLANNED | IN_PROGRESS | ACTUAL | RESULT |
  EXPECTED | PRIOR_RESEARCH | CITATION_ONLY | LIMITATION | UNCERTAIN`
- `project_relevance`: `DIRECT | INDIRECT | NONE | UNCERTAIN`
- `NONE`·`CITATION_ONLY`·`PRIOR_RESEARCH`는 **현재 과제의 기술·성과 관계로 만들지 않는다.**

**`derived_status` — 문서유형 유도 (v2 신설).** 추출된 `statement_status`가 97.3% 비어
있으므로(§4.1-E) 문서유형의 사전확률로 보완한다.

| 조건 | `derived_status` | `status_source` |
|---|---|---|
| 모델이 `UNCERTAIN` 아닌 값을 채움 (5,833건) | 그 값 그대로 | `EXTRACTED` |
| 그 외 · `document_type=RFP` | `REQUIREMENT` | `DERIVED_FROM_DOC_TYPE` |
| 그 외 · `document_type=FINAL_REPORT` | `ACTUAL` | `DERIVED_FROM_DOC_TYPE` |
| 그 외 | `UNCERTAIN` | `UNKNOWN` |

**원본 `statement_status`는 절대 덮어쓰지 않는다.** 유도값임이 그래프에서 드러나야 하고,
나중에 재추출하면 유도값을 버리고 실제값으로 갈아끼울 수 있어야 한다. RFP는 정의상
"앞으로 할 일"이고 완결보고서는 "한 일"이라 이 사전확률 자체는 견고하지만, **완결보고서
안에서 계획(`PLANNED`)과 실적(`ACTUAL`)을 가르지는 못한다** — 그건 재추출로만 얻는다.

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

### 9.1 게이트가 6~9단계에 적용되는 방식 (v2)

위 게이트는 **LLM 추출(3단계)** 을 겨냥해 쓰였다. "테스트 없이 대규모 전체 문서 처리를
시작하지 않는다"의 대상은 되돌릴 수 없고 비싼 작업이다. 6~9단계는 성격이 다르다 —
LLM을 부르지 않고, 입력(`entity_candidates`)을 읽기만 하며, 몇 분이면 끝나고,
언제든 다시 돌릴 수 있다. 그래서 게이트를 다음으로 **재해석**한다.

- **폴더 하나로 먼저 돌린다** (`--folder`). `--dry-run`으로 통계만 보고 실적재는 그 뒤에.
- **적재는 `build_id` 세대 교체**라 잘못돼도 다시 돌리면 정정된다. 되돌릴 수 없는 유일한
  작업은 M_19 Keyword 노드 삭제(`--reset-graph`)이고, 이건 명시적 플래그로만 실행된다.
- **골든셋은 없다.** 사람이 라벨링해야 만들어지는 물건이고, 아직 없다.
  없는 평가를 통과한 척하지 않는다. 대신 10단계 산출 리포트가 대리 지표를 낸다 —
  df 분포, 단일문서 엔티티 비율, 블롭 감시에 걸린 그룹, `REVIEW_REQUIRED` 큐 크기.
  **이 리포트는 품질 증명이 아니라 관찰값이다.** 골든셋 구축은 별도 과제로 남는다.

**3단계(LLM 추출)의 게이트는 그대로 유효하다.** 재추출을 돌릴 때는 원문 §9가 적용된다.

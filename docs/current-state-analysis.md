# 현황 조사 — 연구개발 문서 기반 Neo4j 지식그래프 구축 (지침서 25장 1단계)

조사일 2026-08-02 · 대상 커밋 `2856ae9` · 조사자 Claude Code

지침서(`Claude_Code_Neo4j_Knowledge_Graph_Strategy.md`)의 요구사항을 현재 코드·데이터와
대조한 결과다. **재파싱·재임베딩 없이** 무엇이 가능하고 무엇이 불가능한지를 먼저 못박는다.

---

## 1. 파이프라인 현황

```
RAG 폴더 감시(M_22) → 파싱 → 청킹 → BGE-M3 임베딩 → LanceDB
                                                  ↓
                          (별도) 문서 단위 LLM 1회 → Keyword 노드 → Neo4j
```

| 단계 | 구현 | 위치 |
|------|------|------|
| 수집·감시 | 있음 | `src/rag_watch/service.py` |
| 파싱 | 있음 (PDF/HWPX/DOCX) | `src/doc_ingest/` |
| 청킹 | 있음 | `src/doc_ingest/` |
| 임베딩·저장 | 있음 (BGE-M3, 1024차원) | `src/vector_search/` → LanceDB |
| 엔티티 추출 | **문서 단위 키워드 추출만** | `src/graph_rag/extractor.py` |
| 후보 저장 | **없음** | — |
| 문서 단위 통합 | **없음** | — |
| 정규화 | 부분 (키워드 속성만 갱신) | `service.normalize_entities` |
| 관계 추출 | **없음** | — |
| Neo4j 적재 | 부분 (Document–Keyword) | `src/graph_rag/neo4j_store.py` |

## 2. 데이터 실측

### 2.1 LanceDB 청크

- **325,570 청크 / 9,359 문서** (문서당 평균 34.8 청크)
- 스키마: `doc_id, doc_name, category, page, section, chunk_id, text, bbox, source_path, vector(1024)`

| 지침서 요구 필드 | 현재 | 비고 |
|------------------|------|------|
| `chunk_id`, `text`, `embedding` | ✅ | |
| `page_start/page_end` | △ `page` 단일값 (99.99% 채워짐) | 근거 역추적 가능 |
| `section_title`, `section_id` | ❌ **전체 325,570건 중 0건** | 파서가 채우지 않음 |
| `document_type` | ❌ 컬럼 없음 | **폴더명에서 유도 가능** (아래) |
| `project_id`, `rfp_id` | ❌ | 문서 단위 추출로 확보 가능 |
| `document_year`, `project_start/end_year` | ❌ | 폴더명·본문에서 유도 |
| `chunk_index` | ❌ | 필요하면 doc 내 순번으로 계산 |

### 2.2 문서 유형은 폴더로 판별된다

`category` = folder_id이고, `data/rag_folders.json`이 이름을 갖는다.

| 폴더 | 청크 수 | 유도 문서유형 |
|------|---------|---------------|
| 2020~2025완결보고서 (6개) | 306,362 | `FINAL_REPORT` (+연도) |
| RFP(2008-2009 … 2020-2025) (4개) | 19,208 | `RFP` (+연도구간) |

→ **`document_type`·연도는 재파싱 없이 즉시 확보된다.** 지침서 5.1의 메타데이터 전파를
청크 행에 쓰지 않고 문서 단위 표에 두면 임베딩을 건드리지 않아도 된다.

### 2.3 Neo4j 현재 상태

| 노드 | 수 |
|------|-----|
| Keyword | 6,276 |
| Document | 943 (전체 9,359 중 10%) |
| Entity / Chunk / Note / TechnologyCode | 0 |

| 관계 | 수 |
|------|-----|
| HAS_KEYWORD | 6,276 |

- `Document {doc_id, title, rfp_no, project_no}` — **`project_no`가 실제로 추출되고 있다**
  (예: `PJ013094`). 계획서–완결보고서를 같은 Project로 묶는 축이 이미 확보돼 있다.
- Keyword는 문서 스코프(`doc_id::term::role`)이고 역할은 4종
  (outcome 2,086 / technology 1,805 / research_target 1,584 / problem 801).
- 제약조건: `Document.doc_id`, `Keyword.id`, `Entity.id`, `Chunk.chunk_id`, `Note.slug`,
  `TechnologyCode.code` 유니크.
- **정규 엔티티·Mention·관계·계획/실적 구분은 전혀 없다.**

→ 지침서가 요구하는 그래프와 현재 그래프는 사실상 겹치지 않는다. **초기화 손실이 거의 없다**
(943개 문서의 얕은 키워드뿐이며, 재구축 가능).

### 2.4 LLM 호출 여건

- 추출 모델 `gemma4:26b`(설정 `app.graphrag.extraction_ollama_model`), 로컬 Ollama.
- GPU 1장(B200)을 대화·임베딩·리랭커·TTS와 **공유**한다. 배경 작업은
  `rag_watch.activity.conversation_active()`로 대화에 양보하는 장치가 이미 있다.
- 방금 E-88(리랭커 cuDNN segfault)을 고쳤으므로 장시간 배치의 안정성 전제는 확보됐다.

---

## 3. 지침서 대비 격차 요약

| 지침서 요구 | 상태 | 대응 |
|-------------|------|------|
| 임베딩과 분리된 별도 추출 작업 | 부분 | 유지·확장 |
| LLM 결과 → 후보 저장 → 정규화 → 적재 | **없음** | 신규 구축(핵심) |
| Mention / CanonicalEntity 분리 | **없음** | 신규 |
| 원문 근거 필수(evidence·page·chunk) | **없음** | 신규 |
| 계획/실적/결과/선행연구 구분 | **없음** | 신규 |
| 작목·품종 다르면 병합 금지 | **없음** | 신규(규칙+LLM 판정) |
| 관계 추출 | **없음** | 신규 |
| 재실행 안전성·부분 재구축 | 부분 | 신규(작업 큐·버전) |
| 골든셋 평가 | **없음** | 신규 |
| Section 계층 | **불가(데이터 없음)** | 보류 — 4장 참조 |

---

## 4. 재파싱 없이는 불가능한 것 — 판단과 대안

### 4.1 Section 노드 (지침서 5.2, 6.1)

`section` 컬럼이 **325,570건 전부 비어 있다.** Section 계층을 만들려면 9,359개 문서를 다시
파싱해야 하고, 이는 "기존 파싱·청킹·임베딩 유지"라는 전제와 충돌한다.

→ **1단계에서는 Section을 만들지 않는다.** `Project → Document → Chunk`로 가고, 근거는
페이지 번호(99.99% 확보)로 역추적한다. 지침서도 "가능한 경우 구조를 보존한다"고 했다.
나중에 섹션이 필요하면 추출 시점에 LLM이 본 소제목을 Mention 속성으로 남겨 두고,
재파싱 없이 사후 보강하는 경로를 열어 둔다.

### 4.2 전 청크 LLM 처리 (지침서 8장 문맥분류 + 10장 추출)

지침서대로 **청크마다 분류 1회 + 추출 1회**를 돌리면 325,570 × 2 = **651,140회 호출**이다.
26B 모델로 호출당 5초만 잡아도 **약 900시간**이고, 그동안 대화·임베딩과 GPU를 다툰다.
**현실적으로 불가능하다.**

→ 두 가지로 줄인다.
1. **문서당 청크 예산**(기본 12개)을 두고, 연구목표·연구내용·결과에 해당할 가능성이 높은
   청크만 고른다(제목 패턴 + 대표 질의 벡터 유사도). 설정으로 조절 가능.
2. **분류와 추출을 한 번의 호출로 합친다.** 추출 응답에 청크의 `statement_status`·
   `current_project_relevance`를 함께 담게 하면 분류 정보는 그대로 얻으면서 호출이 반으로
   준다. 분리가 필요하면 설정(`two_pass: true`)으로 되돌릴 수 있게 만든다.

→ 예상 규모: 9,359문서 × 12청크 ÷ 3청크묶음 ≈ **37,000회**. 10문서 테스트에서 실측한
초당 처리량으로 전체 소요를 다시 계산해 보고한다.

### 4.3 청크 노드 325,570개 적재

전 청크를 Neo4j에 넣는 것은 이득 없이 그래프만 무겁게 한다.

→ **Mention이 참조하는 청크만** Chunk 노드로 만든다. 근거 역추적은 동일하게 된다.

---

## 5. 재사용 가능한 것 / 교체할 것

**재사용**
- `vector_search`(청크 조회·임베딩·유사도) — 정규화 후보 검색에 그대로 쓴다.
- `graph_rag/neo4j_store.py`의 드라이버·배치 실행·제약조건 관리 골격.
- `GemmaChatAgent.complete_json`(스키마 강제 JSON) — 추출·판정 공용.
- `rag_watch.activity.conversation_active()` — 대화 우선 양보.
- 설정 분리 패턴(`app.graphrag.extraction_*`) — 그대로 확장.

**교체·신설**
- `graph_rag/extractor.py`의 문서 단위 키워드 추출 → 청크 단위 후보 추출로 대체
  (기존 함수는 남겨 두되 파이프라인에서 분리).
- Keyword 노드 스키마 → Mention + CanonicalEntity로 대체.
- 후보 저장소·정규화·관계추출·작업큐·평가 → 전부 신설.

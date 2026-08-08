# M_20 DeepResearch — GraphRAG 기반 심층 자료 검토·보고서 생성 (CR-20)

버전 v2 · 2026-08-07 · CR-62에서 **모드 고정 → 프로젝트(방) 체계**로 전환

> **v2 변경 요약 (CR-62)** — 3모드 고정을 없애고 사용자가 방을 만들어 쓰는 구조로 바꿨다.
> 전환 전에 코드를 감사한 결과 **파이프라인은 이미 도메인 중립**이었다:
> `_run_pipeline`·`_retrieve`·`_merge_hits`·`_rank_sources`가 `mode`를 받지도 않고,
> 검색에 문서유형·폴더 필터가 없으며, 출력 스키마·파싱도 없다(순수 마크다운).
> 모드가 실제로 바꾸는 것은 **종합 프롬프트 본문 하나**뿐이었다.
> 따라서 이번 작업은 로직 수술이 아니라 **정적 레지스트리를 데이터로 옮기는 일**이다.

## 1. 목적

OpenAI/Gemini Deep Research와 유사한 다단계 심층 조사. 인터넷 검색 대신
**사내 지식 기반(M_19 GraphRAG 하이브리드 + M_07/M_18 벡터 RAG)** 만을 근거로
충분히 자료를 수집·검토한 뒤 보고서를 생성한다. 외부 네트워크 호출 없음.

**분야에 국한하지 않는다.** 무엇을 어떤 관점으로 쓸지는 방(프로젝트)의 지침이 정하고,
코드는 근거 인용 규칙과 출력 형식만 강제한다.

## 2. 프로젝트(방) — v2

모드 상수를 없애고 `data/research_projects.db`(SQLite)의 행으로 대체한다.
방마다 **지침(버전관리)** 과 **검색 설정**을 갖는다.

| 필드 | 뜻 | 기본 |
|---|---|---|
| `instructions` | 종합 시스템 프롬프트. "무엇을 어떤 관점으로 쓰는가" | 범용 기본값 |
| `planner_hint` | 플래너에 덧붙일 관점 예시 한 줄 (선택) | 빈값 |
| `sub_queries` | 플래너가 만들 하위 질의 수 | 6 |
| `top_k_per_query` | 질의당 검색 건수 | 5 |
| `gap_rounds` | 격차분석 반복 라운드 (0이면 생략) | 1 |
| `max_evidence_chunks` | 종합에 넣을 근거 청크 상한 | 24 |

**시드 3개** — `duplication`·`discovery`·`proposal`을 첫 실행 시 만든다. `project_id`를
옛 mode 값과 같게 두어 기존 `/run-stream` 호출이 그대로 동작한다. conf.yaml
`agent_prompts.deep_research_*`에 커스텀 지침이 있으면 **버전 1로 이관**한다.
시드도 일반 방과 동등하게 **수정·삭제 가능**하다.

### 2.1 지침 버전관리

`instruction_versions`는 **append-only**다. 저장할 때마다 새 버전이 쌓이고,
되돌리기도 **복원 내용을 새 버전으로 쌓는다** — 이력을 지우지 않는다.
현재 지침 = 최신 버전.

> M_17 스펙 §"프롬프트 버전 관리·히스토리·롤백 스택 없음"은 **설정의 7개 지침에만**
> 계속 적용된다. 방 지침은 사용자가 실험하며 고쳐 쓰는 물건이라 되돌릴 수단이 필요하다.

### 2.2 코드가 계속 강제하는 것

`EVIDENCE_RULES`(근거 인용 강제)와 `OUTPUT_FORMAT_RULES`(마크다운 형식)는 **방 지침으로
덮을 수 없다.** 환각 억제 안전장치이지 취향이 아니다. 방 지침은 그 앞에 붙는다.

## 3. 파이프라인 (service.run — async generator)

```
run(mode, prompt, attachment_text) → AsyncIterator[event dict]
```

1. **planning** — LLM `complete_json`으로 하위 질의 3~6개 생성 (모드별 플래너 프롬프트).
   실패 시 프롬프트 원문을 단일 질의로 폴백 (전체 실패 금지).
2. **searching** — 하위 질의마다 `graph_rag_service.hybrid_retrieve(q, top_k=5)`.
   graph_rag_service가 None이거나 미가용이면 `rag_service.retrieve` (executor) 벡터-only
   폴백 + 폴백 사실을 이벤트로 알림. chunk_id로 중복 제거하며 근거 풀에 축적.
3. **gap_analysis** — 수집 근거 요약을 LLM에 보여주고 부족한 관점의 추가 질의
   최대 3개 생성 → 1라운드 추가 검색 (실패 시 스킵).
4. **synthesis** — 근거를 `[n] 문서명 (score)` 번호 목록으로 정리해 모드별 시스템
   프롬프트 + 사용자 입력과 함께 `complete_text` 호출. 보고서는 근거 인용 [n] 필수,
   **근거에 없는 내용은 "사내 자료에서 확인 불가"로 명시** (환각 억제).
5. **done** — `{stage:"done", report, sources:[{n, doc_id, doc_name, ...}], sub_queries}`.

각 단계는 `{stage, message, ...}` 이벤트를 yield — 라우트가 SSE로 중계.

### 근거 예산

- 질의당 top_k=5, 전체 근거 풀 상한 24청크, synthesis 주입 텍스트 상한 14,000자
  (초과분은 score 순 절단). 상수는 service.py 모듈 상수로 관리.

## 4. 공개 API

### DeepResearchService (src/deep_research/service.py)

```python
class DeepResearchService:
    def __init__(self, agent, rag_service, graph_rag_service=None): ...
    def run(self, mode: str, prompt: str, attachment_text: str = "")
        -> AsyncIterator[dict[str, Any]]: ...
```

- `agent`: GemmaChatAgent 프로토콜 (complete_json, complete_text) — 회의록과 동일하게
  init_agent에서 주입, agent 재초기화 시 새 인스턴스로 교체.
- 동시 실행 1개 제한 (asyncio.Lock — 진행 중이면 즉시 error 이벤트).

### REST (src/app/deep_research_routes.py)

- `POST /api/deep-research/run-stream` (multipart form: `mode`, `prompt`, `file`(선택))
  → `text/event-stream`. 첨부는 document_ingest `parse_to_meta_segments`로 텍스트만
  추출 (벡터 스토어 등록 안 함). 서비스 미준비 시 error 이벤트.

## 5. 프론트엔드

- `chatTabs.ts`에 `research` 탭 추가 (펫 "리서치" / 데스크톱 "딥 리서치") — 단일 소스라
  두 모드 동시 반영 (E-59).
- `DeepResearchView.tsx`: 모드 선택 카드 3개 + textarea(restoreFocus 필수, E-38) +
  파일 선택 버튼(`<input type="file">` — OS 드래그 불가 제약, FRONTEND_CONSTRAINTS §1) +
  진행 로그(SSE) + 결과 ReactMarkdown 렌더 + 복사 버튼.
- ChatPanel·DesktopView에서 **항상 마운트 + display 토글** (탭 전환 시 진행 상태 보존,
  E-19/E-20).

## 6. 오류·엣지

- LLM planning JSON 파싱 실패 → 원문 단일 질의 폴백.
- 검색 결과 0건 → synthesis 없이 "사내 자료에서 관련 근거를 찾지 못했다" 보고 (환각 금지).
- 그래프 저장소 다운 → 벡터-only + 이벤트 고지 (기능 저하, 오류 아님).
- 첨부 파싱 실패 → error 이벤트 (형식 안내).
- 입력 상한: prompt+attachment 합계 30,000자 초과 시 앞부분만 사용 + 고지.

## 7. 테스트 (tests/deep_research/)

- Fake agent(고정 JSON/텍스트) + Fake retriever로: 이벤트 순서(planning→searching→
  gap_analysis→synthesis→done), 중복 chunk 제거, 그래프 None 폴백, planning 실패 폴백,
  근거 0건 처리, 동시 실행 거부.
- 라우트: 서비스 미준비 시 SSE error 이벤트.

## 8. DoD

- [x] pytest tests/deep_research 통과 (10건) + 기존 회귀 0
- [x] 실서버 E2E (2026-07-16): duplication 모드 SSE 왕복 30초 — 플래너 질의 5+보완 2,
      백엔드 로그 "GraphRAG 하이브리드 융합: 벡터=5+그래프=2" 확인, 보고서 2,249자가
      실제 인덱스 문서(대구경북연구원_보고서.txt, 청정축산_사업개요.txt)를 [1][2]로 인용
- [x] 프론트 빌드 (ELECTRON_BUILD=1) + 탭은 chatTabs.ts 단일 소스로 펫·데스크톱 동시 노출

# M_18 SearchUpgrade — ANN 인덱스 + 리랭커 + 하이브리드 검색 (CR-06)

## 1. 목적

E-40 분석 결과를 반영한 검색 파이프라인 업그레이드. 모두 로컬·오프라인이며 LLM 설정과 무관.

| 항목 | 효과 | 근거 |
|---|---|---|
| IVF-PQ 인덱스 | 벡터 검색 95ms → ~41ms, 코퍼스 증가에도 유지 | nprobes=128 + refine=30에서 recall@8 100% 실측 (E-40) |
| 리랭커 (bge-reranker-v2-m3) | 상위 후보 정밀 재정렬 — 유사 문서 多 코퍼스에서 정밀도 ↑ | cross-encoder가 bi-encoder보다 쌍별 관련도 판단 우수 |
| 하이브리드 (FTS BM25 + RRF) | 고유명사·과제번호 등 정확 키워드 질의 보강 | ngram(2,3) 토크나이저로 한국어 매칭 검증 완료 |

## 2. 검색 파이프라인 (RagService.retrieve)

```
query ─┬─ embed_query → store.search(top=C) ──┐        C = rerank_candidates(기본 30)
       │   (인덱스 있으면 nprobes=128,        │
       │    refine_factor=30 적용)            ├─ RRF 융합(k=60, 중복 chunk_id 제거)
       └─ store.search_text(top=C) [hybrid] ──┘        → 후보 C개
                                                       → reranker.rerank(query, 후보, top_k)
                                                       → hits
```

- **found 판정은 기존과 동일**: 벡터 검색 1위 hit의 cosine score >= min_score.
  (리랭커/FTS는 순서만 바꾸고 found 의미를 바꾸지 않는다 — M_07 §6.3.2 계약 유지)
- SearchHit.score: 벡터 유래 hit은 cosine 점수 유지. FTS 단독 유래 hit은
  `_score/(_score+10)`로 0..1 정규화한 근사값 (downstream에서 미사용 확인됨).
- 각 단계는 graceful degradation: 리랭커 모델 미배치 → 벡터 순서 그대로,
  FTS 인덱스 부재/실패 → 벡터 단독, 인덱스 부재 → 전수 스캔.

## 3. 컴포넌트

### 3.1 vector_search/reranker.py (신규)
- `Reranker(model_dir, device="auto", batch_size=32)` — sentence-transformers CrossEncoder,
  로컬 로드 전용(HF offline env). `RerankerError` (errors.py 추가).
- `rerank(query, hits, top_k) -> list[SearchHit]` — (query, hit.text) 쌍 점수화 후
  내림차순 top_k. 추론 실패 시 경고 로그 + 원래 순서 top_k 반환 (예외 전파 금지).

### 3.2 VectorStore 확장 (store.py)
- `ensure_indices()` — 벡터 인덱스(IVF-PQ, rows>=1000일 때, partitions=√rows 기반
  16~512 클램프) + FTS 인덱스(text, ngram 2..3, stem/불용어 off) 없으면 생성.
  생성/실패 모두 로그. optimize()에서도 호출(임계 도달 시 자동 생성).
- `search()` — 벡터 인덱스 존재 시 `.nprobes(128).refine_factor(30)` 적용.
- `search_text(query, top_k, category, source)` — FTS(BM25) 검색. where 절은
  search()와 동일 로직. FTS 인덱스 없으면 VectorStoreError.

### 3.3 RagService (rag.py)
- 생성자: `reranker: Reranker | None`, `hybrid_enabled: bool`, `rerank_candidates: int`.
- `_rrf_fuse(vec_hits, fts_hits, k=60)` — chunk_id 기준 1/(k+rank) 합산, 벡터 hit 우선 보존.

### 3.4 설정 (config.py AppConfig)
- `rag_rerank_enabled: bool = True`
- `rag_rerank_candidates: int = 30` (8..100)
- `rag_hybrid_enabled: bool = True`
- 리랭커 모델 경로: `assets/models/bge-reranker-v2-m3` 고정 (임베더와 동일 규칙).

### 3.5 배선 (service_context.py)
- RagService 조립 시 Reranker 생성 시도(모델 미배치 → None + 경고 1회).
- 조립 후 `ensure_indices()`를 백그라운드 태스크로 실행 (기동 비차단).

## 4. 수용 기준

- 리랭커 on: 실제 쿼리에서 벡터-only 순서와 다른 재정렬 발생 + top_k 반환 확인.
- 하이브리드 on: '김동현'·'PJ017203' 류 키워드 쿼리에서 해당 청크가 후보에 진입.
- 모델/인덱스 제거 상태에서도 retrieve가 예외 없이 동작 (graceful).
- found/no_match_reason 의미 기존 테스트와 호환 (tests/vector_search 전체 green).
- 검색 총 시간(임베딩+검색+융합+리랭크) < 400ms (GPU 기준, 실측 ~240ms).
  리랭커는 max_length=512 + FP16 (실측: FP32 대비 2.6배 빠르고 top-8 순서 동일).
  참고: 채팅 응답은 LLM 생성(수 초)이 지배해 retrieval 250ms는 체감 무시 수준.

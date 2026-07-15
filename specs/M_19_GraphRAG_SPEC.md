# M_19 GraphRAG — Neo4j 그래프RAG 하이브리드 + 지식그래프 시각화 (CR-18)

분류: **NEW** (`src/graph_rag/`) + 통합 수정 (M_01/M_05/M_06/M_07/M_12/M_15 접점)

## 1. 목적

문서·노트에서 LLM으로 개체(엔티티)·관계를 추출해 그래프 DB(Neo4j, GraphStore 추상화)에
축적하고, 질의 시 **벡터 검색 + 그래프 탐색을 RRF 융합**해 다중 문서 연결 질문의 회수율을
높인다. 답변의 **근거 서브그래프**를 전용 '그래프' 탭에서 시각화한다.

비목표(V1 아님): Microsoft GraphRAG식 커뮤니티 요약/글로벌 서치, 그래프 임베딩,
Kuzu 구현체(인터페이스만 대비).

## 2. 데이터 모델 (그래프 스키마)

```
(:Entity   {id, name, type, description})       # id = normalize(name)+":"+type
(:Chunk    {chunk_id, doc_id})                   # 본문은 LanceDB가 원본 — 그래프엔 키만
(:Document {doc_id, name, category})
(:Note     {slug, title})
(:Entity)-[:REL {type, description, weight}]->(:Entity)   # weight = 동시출현 횟수 누적
(:Entity)-[:MENTIONED_IN]->(:Chunk)
(:Chunk)-[:PART_OF]->(:Document|:Note)
```

- Entity 타입: `인물|조직|사업|제도|기술|장소|기타`
- `normalize(name)`: strip + 내부 공백 단일화 + casefold. 같은 (norm name, type)은 병합,
  description은 최초값 유지(빈 값이면 갱신), REL weight는 누적.
- 유니크 제약: `Entity.id`, `Chunk.chunk_id`, `Document.doc_id`, `Note.slug`.

## 3. 공개 API (src/graph_rag/)

### 3.1 types.py
```python
@dataclass(frozen=True) class Entity:      id: str; name: str; type: str; description: str = ""
@dataclass(frozen=True) class Relation:    source_id: str; target_id: str; type: str; description: str = ""; weight: float = 1.0
@dataclass(frozen=True) class ChunkLink:   entity_id: str; chunk_id: str
@dataclass(frozen=True) class GraphNode:   id: str; label: str; kind: str; type: str = ""   # kind: entity|document|note
@dataclass(frozen=True) class GraphEdge:   source: str; target: str; kind: str; weight: float = 1.0
@dataclass(frozen=True) class GraphSnapshot: nodes: list[GraphNode]; edges: list[GraphEdge]
@dataclass(frozen=True) class EvidenceSubgraph: query: str; created: str; nodes: list[GraphNode]; edges: list[GraphEdge]; chunk_ids: list[str]
@dataclass class IndexStatus: doc_id: str; state: str; total_chunks: int; done_chunks: int; error: str = ""   # state: pending|running|done|failed
```

### 3.2 store.py — GraphStore(ABC)
```python
class GraphStore(ABC):
    def ping(self) -> bool
    def ensure_schema(self) -> None                       # 제약/인덱스 멱등 생성
    def upsert_document(self, doc_id, name, category) -> None
    def upsert_note(self, slug, title) -> None
    def upsert_entities(self, entities: list[Entity]) -> None
    def upsert_relations(self, relations: list[Relation]) -> None
    def link_chunks(self, links: list[ChunkLink], parent_id: str, parent_kind: str) -> None
    def find_entities(self, terms: list[str], limit: int = 20) -> list[Entity]   # normalize 후 CONTAINS 매칭
    def neighbors(self, entity_ids: list[str], hops: int = 1, limit: int = 50) -> list[Entity]
    def chunks_for_entities(self, entity_ids: list[str], limit: int = 30) -> list[tuple[str, int]]  # (chunk_id, 연결 엔티티 수)
    def subgraph(self, entity_ids: list[str], chunk_ids: list[str]) -> GraphSnapshot
    def snapshot(self, limit: int = 500, entity_types: list[str] | None = None) -> GraphSnapshot
    def delete_by_doc_id(self, doc_id: str) -> None       # Document|Note + 소속 Chunk + 고아 Entity 정리
    def stats(self) -> dict[str, int]                     # {entities, relations, chunks, documents, notes}
    def close(self) -> None
```
구현체: `neo4j_store.py` `Neo4jGraphStore(uri, user, password, database="neo4j")` —
드라이버 lazy 생성, 모든 Cypher 파라미터라이즈(문자열 조립 금지), 세션당 짧은 트랜잭션.
`tests/graph_rag/fakes.py` `FakeGraphStore` — 인메모리 dict 기반, 동일 계약.

### 3.3 extractor.py
```python
class EntityExtractor:
    def __init__(self, llm_complete_json: Callable[..., Awaitable[dict]], timeout_s: float = 30.0)
    async def extract(self, text: str) -> tuple[list[Entity], list[Relation]]
```
- IntentGate가 쓰는 LLM JSON 클라이언트(`complete_json` 콜러블)를 주입받는다 (제공자 재사용).
- 프롬프트: 한국어 few-shot 1개 포함, JSON 스키마 강제
  `{"entities":[{"name","type","description"}], "relations":[{"source","target","type","description"}]}`.
- **견고 파싱** (E-51 교훈): 마크다운 펜스 제거, 미지 타입→`기타`, relations의 source/target이
  entities에 없으면 폐기, name 빈 문자열 폐기, 예외/타임아웃 시 `([], [])` 반환(스킵).

### 3.4 service.py
```python
class GraphRagService:
    def __init__(self, graph_store, vector_store, extractor, rag_service,
                 max_hops: int = 2, evidence_buffer: int = 5)
    @property def available(self) -> bool                  # ping 캐시(60s)
    def schedule_index_document(self, doc_id: str) -> None # asyncio.create_task 큐잉
    async def index_document(self, doc_id: str) -> IndexStatus
    async def reindex_all(self) -> None                    # LanceDB 전체 doc_id 백필(순차)
    def index_statuses(self) -> list[IndexStatus]
    async def index_note(self, slug: str, title: str, body: str) -> None
    async def graph_retrieve(self, query: str, top_k: int = 5) -> tuple[list[SearchHit], EvidenceSubgraph | None]
    async def hybrid_retrieve(self, query: str, top_k: int = 5, source: str = "both") -> RetrievalResult
    def latest_evidence(self) -> EvidenceSubgraph | None
```
- `graph_retrieve`: 질의를 공백/조사 경계로 단어 분해(2자 이상) → `find_entities` →
  `neighbors(max_hops)` → `chunks_for_entities` → LanceDB `get_chunks_by_chunk_ids`로 본문 →
  SearchHit(score = 연결 엔티티 수 정규화 0.4~0.9). **LLM 호출 없음** (지연 <300ms 목표).
- `hybrid_retrieve`: `rag_service.retrieve()` + `graph_retrieve()` → 기존 `_rrf_fuse` 재사용 →
  상위 top_k. `found = vector.found OR 그래프 hit 존재` — 벡터가 놓친 질의를 그래프가
  구제할 수 있다(엔티티 정확 매칭은 그 자체로 관련성 근거). evidence를 링버퍼에 보관.
- **폴백**: `available == False`면 `hybrid_retrieve`는 `rag_service.retrieve()` 결과를
  그대로 반환 (예외 전파 금지, warning 로그 1회/분 스로틀).

### 3.5 VectorStore 확장 (src/vector_search/store.py)
```python
def get_chunks_by_chunk_ids(self, chunk_ids: list[str]) -> list[SearchHit]   # 신규, IN 필터
```

## 4. 통합 지점

| 파일 | 변경 |
|------|------|
| `src/app/config.py` | `GraphRagConfig{enabled=False, neo4j_uri="bolt://127.0.0.1:7687", neo4j_user="neo4j", neo4j_password="", max_hops=2}` — AppConfig.graphrag |
| `src/app/url_guard.py` | bolt:// URI도 검증 대상 (127.0.0.1/localhost/사내 IP만) |
| `src/app/service_context.py` | load_app_services에서 enabled 시 Neo4jGraphStore+GraphRagService 생성(비치명 try/except), init_agent로 전달 |
| `src/agent/upstream_adapter.py` | `_augment_with_rag`: `self._graph_rag`가 있고 available이면 `hybrid_retrieve` 사용, 아니면 기존 `rag_service.retrieve` |
| `src/app/rag_routes.py` | 업로드 성공 후 `schedule_index_document(doc_id)`, 문서 삭제 시 `graph_store.delete_by_doc_id` |
| `src/knowledge/service.py` | save/update 후 `index_note` 스케줄 (graphrag 주입 시) |
| `src/app/graphrag_routes.py` | 신규 라우터 (§5) + server.py include_router |

## 5. REST API (`/api/graphrag`)

| 엔드포인트 | 응답 |
|-----------|------|
| `GET /graph?limit=500&types=조직,사업` | `{nodes:[{id,label,kind,type}], edges:[{source,target,kind,weight}], stats}` |
| `GET /status` | `{enabled, connected, stats, indexing:[IndexStatus]}` |
| `POST /reindex` body `{doc_id?: str}` | `{scheduled: true}` — doc_id 없으면 전체 백필 |
| `GET /evidence/latest` | EvidenceSubgraph 또는 404 |

graphrag 비활성/미연결 시: `/status`는 200(`connected:false`), 나머지는 503.

## 6. 프론트엔드

- `ChatTab`에 `"graph"` 추가, TABS에 {id:"graph", label:"그래프", Icon:Network}.
- `GraphRagView.tsx`: NotesGraph 캔버스 페인터 패턴 재사용.
  - 노드: entity(타입별 팔레트 7색, 원), document(사각), note(기존 스타일). 차수 기반 크기.
  - 컨트롤 바: 타입 필터 칩, 노드 검색, 재인덱싱 버튼(+진행률 폴링 3s), stats 표시.
  - **근거 모드**: evidence의 노드·엣지만 발광(정상 알파), 나머지 alpha 0.15. "전체 보기" 버튼으로 해제.
  - 클릭: document→문서 탭, note→노트 탭 편집, entity→우측 미니 패널(이름·타입·설명·연결 문서 목록).
- ChatContent: RAG 근거 칩이 있는 답변에 "근거 그래프" 버튼 → `setChatTab("graph")` + evidence 하이라이트 트리거(store에 `graphEvidenceRequested` 플래그).

## 7. 오류 정책

- Neo4j 미설치/다운: 서비스 available=False → 벡터-only 폴백, 앱 기동·기존 기능 무영향 (기본 enabled=false).
- 추출 LLM 실패/타임아웃/JSON 파싱 불가: 해당 청크 스킵 + done_chunks 진행, 문서 상태는 done(partial 카운트 로그).
- 인덱싱 중 문서 삭제: delete_by_doc_id가 우선, 인덱싱 태스크는 upsert 시 doc 부재 무시.
- Cypher 인젝션: 전 쿼리 파라미터라이즈 + 식별자 화이트리스트.

## 8. 성능 목표

- graph_retrieve p50 < 300ms (10만 엔티티 규모), hybrid 총합 p50 < 800ms (리랭커 포함).
- 인덱싱: 청크당 LLM 1콜(기본 gemma4 로컬) — 백그라운드 순차, 채팅 경로 블로킹 금지.
- /graph 스냅샷 limit 기본 500노드 (프론트 렌더 보호).

## 9. 테스트 케이스

정상(≥5): ① 추출 JSON 정상 파싱→Entity/Relation 생성 ② 같은 엔티티 재추출 시 병합(weight 누적)
③ graph_retrieve 1홉/2홉 확장 결과 ④ hybrid RRF: 벡터 상위+그래프 상위 융합 순위
⑤ evidence 서브그래프에 매칭 엔티티·경유 관계·청크 포함 ⑥ 문서 삭제 시 청크·고아 엔티티 정리.
엣지(≥5): ① 빈 질의/1자 단어만 ② 매칭 엔티티 0건→벡터-only와 동일 결과 ③ LanceDB에 없는
chunk_id(고아 링크) 무시 ④ 추출 결과 0건 문서 ⑤ 인덱싱 중복 스케줄(같은 doc_id) 직렬화.
적대(≥3): ① LLM이 펜스/잡담 섞인 JSON → 견고 파싱 ② 엔티티 이름에 Cypher 특수문자
(`' " \ { }`) → 파라미터라이즈로 안전 ③ relations가 미존재 엔티티 참조 → 폐기 ④ 1MB 청크
텍스트 → 추출 입력 8k자 절단.

## 10. DoD

- [ ] ruff/mypy/pytest 전체 그린 (기존 942개 회귀 0)
- [ ] FakeGraphStore 단위 테스트 + Neo4j 통합 테스트(미접속 시 skip)
- [ ] 실데이터 E2E: 문서 업로드→인덱싱 완료→Neo4j 엔티티 수 확인→ws_test 질의에
      하이브리드 주입 로그→그래프 탭 CDP 스크린샷 (CLAUDE.md "구현된 척 금지" 준수)
- [ ] Neo4j 다운 상태에서 기존 RAG 회귀 없음 확인
- [ ] MODULES.md M_19 행, install.md Neo4j 절차, conf.example.yaml 갱신

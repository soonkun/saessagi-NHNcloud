# M_15 — KnowledgeNotes SPEC (Phase 1)

## 목적

공무직 사용자가 본인이 처리한 업무 사례(상황·자료·해결 절차)를 누적해두고, 후임자/미래의 본인이 비슷한 업무를 할 때 절차와 자료 위치를 함께 찾을 수 있게 한다. 옵시디언 스타일의 **로컬 markdown 노트 + 위키링크 + 지식 그래프** 시스템.

Phase 1은 CRUD + 그래프 + 전용 탭 UI까지. 채팅 명령("지금 한 일 노트로 저장")으로 자동 작성하는 트리거는 Phase 2.

## 비목표

- 채팅 대화에서 노트 자동 생성 (Phase 2)
- 노트 간 양방향 backlink 자동 업데이트 (Phase 2 — 위키링크 추출은 하되 backlink는 그래프에서만 시각화)
- 외부 옵시디언 vault 호환 (frontmatter 형식만 호환되도록 노력하되 보장 안 함)

## 데이터 모델

### 저장 위치
```
{SAESSAGI_ROOT}/data/knowledge/{slug}.md
```

### Slug 규칙
- 사용자 입력 title을 슬러그화: 공백 → `-`, 한글/숫자/영문/`-`만 보존, 영문은 lowercase
- 슬러그화 결과가 비면 `note-{8자 uuid}` 사용
- 충돌 시 `-2`, `-3` suffix

### frontmatter
```yaml
---
title: 출장비 정산
slug: 출장비-정산
created: 2026-06-06T10:30:00
updated: 2026-06-06T10:30:00
tags: [회계, 출장]
related_docs: ["출장비 가이드.hwpx_a1b2c3d4"]
---
```

`related_docs`는 `RagDocument.id` (= `filename_uuid8`) 목록. 본문 안 위키링크 `[[doc:<doc_id>]]` 또는 `[[<other_slug>]]`로 참조.

## API (`/api/knowledge`)

### GET `/notes`
```json
[{"slug": "출장비-정산", "title": "출장비 정산", "tags": ["회계"], "created": "...", "updated": "...", "related_docs": ["..."]}]
```

### GET `/notes/{slug}`
```json
{"slug": "...", "title": "...", "tags": [...], "created": "...", "updated": "...", "related_docs": [...], "content": "본문 markdown"}
```

### POST `/notes`
요청: `{"title": "...", "content": "본문", "tags": [...], "related_docs": [...]}`  
응답: 생성된 노트 단건. 서버가 slug 생성, frontmatter 합성, RAG 임베딩 트리거.

### PATCH `/notes/{slug}`
요청: `{"title"?, "content"?, "tags"?, "related_docs"?}` 부분 수정. updated 갱신, RAG 재임베딩(기존 chunks 삭제 후 재삽입).

### DELETE `/notes/{slug}`
md 파일 삭제 + RAG `delete_by_doc_id(f"__knowledge__:{slug}")` 호출.

### GET `/graph`
```json
{
  "nodes": [{"slug": "...", "title": "...", "tags": [...]}],
  "edges": [{"source": "slug-a", "target": "slug-b", "kind": "wikilink|tag|doc"}]
}
```
엣지 종류:
- `wikilink`: 본문 `[[other-slug]]` 발견 시
- `tag`: 같은 태그를 공유하는 노트 쌍
- `doc`: `related_docs`에 공통 `doc_id` 있는 노트 쌍

## 모듈 구조

```
src/knowledge/
  __init__.py         # KnowledgeService 노출
  parser.py           # frontmatter/위키링크 파싱
  service.py          # CRUD + 그래프 빌드 + RAG 임베딩
```

```
src/app/
  knowledge_routes.py  # FastAPI 라우터
  main.py              # include_router 추가 (또는 server.py)
```

`service_context`에 `knowledge_service` 주입.

## RAG 통합

- 노트 본문을 `_chunk_text` 로 청킹 (기존 `rag_routes._chunk_text` 재사용 — 함수 export 또는 `_helpers.py` 분리)
- 각 청크에 `[출처: 업무노트/<title>]` prefix
- `DocumentChunk`:
  - `doc_id = f"__knowledge__:{slug}"`
  - `doc_name = title`
  - `category = "__knowledge__"`
  - `page = None`

`rag_routes.list_documents`에서 `category == "__knowledge__"`인 doc은 응답에서 **제외** (문서 탭 오염 방지).

`upstream_adapter._augment_with_rag`는 변경 없음 — RAG 검색에서 노트도 자연스럽게 hit (질문 답변에서 노트도 함께 활용됨).

## 프론트엔드

### 타입 (`web/src/types.ts`)
```ts
export type ChatTab = "chat" | "calendar" | "documents" | "meeting" | "notes" | "settings";

export interface KnowledgeNoteMeta {
  slug: string;
  title: string;
  tags: string[];
  related_docs: string[];
  created: string;
  updated: string;
}
export interface KnowledgeNote extends KnowledgeNoteMeta {
  content: string;
}
export interface KnowledgeGraph {
  nodes: { slug: string; title: string; tags: string[] }[];
  edges: { source: string; target: string; kind: "wikilink" | "tag" | "doc" }[];
}
```

### API (`web/src/services/api.ts`)
- `fetchNotes()`, `fetchNote(slug)`, `createNote(...)`, `updateNote(slug,...)`, `deleteNote(slug)`, `fetchKnowledgeGraph()`

### `web/src/components/ChatPanel.tsx`
TABS에 `{id:"notes", label:"노트", Icon: BookOpen}` 추가. NotesView 항상 마운트(편집 buffer 보존).

### `web/src/components/NotesView.tsx` (신규)
좌측: 노트 목록 (제목·태그·날짜, 검색창)  
우측: 선택된 노트의 편집 영역 — 상단 sub-탭 `편집 | 미리보기 | 그래프`
- 편집: `<input>` 제목, `<input>` 태그(쉼표 구분), `<textarea>` 본문, 저장/삭제 버튼
- 미리보기: ReactMarkdown, `[[slug]]` 클릭 → 해당 노트로 점프
- 그래프: lazy-load `react-force-graph-2d`, 노드 클릭 → 노트 점프

### 그래프
- `react-force-graph-2d` 동적 import — 노트 탭 진입 후 그래프 sub-탭 첫 클릭 시 로드
- 노드 색상: 첫 태그 기준 해시 색상
- 엣지 스타일: wikilink 실선, tag 점선, doc 파란선

## 회귀 위험

- `list_documents`에 `__knowledge__` 카테고리 제외 — 기존 카테고리 컬럼 부재(구 스키마) 데이터에는 영향 없어야 함
- 노트 삭제 시 RAG 청크 삭제 실패해도 md는 이미 삭제됨 → 청크 삭제부터 시도하고 성공 시 md 삭제, 청크 삭제 실패 시 503 응답
- frontmatter 파싱 에러 → 깨진 노트는 목록에서 제외(skip)하고 로그 남김 (전체 listing이 깨지지 않게)

## 검증

1. 노트 3개 생성·수정·삭제 → md 파일 확인 + RAG 청크 확인
2. `__knowledge__` 카테고리 doc이 `list_documents`에 안 나옴
3. `__knowledge__` 카테고리 doc이 RAG 검색에는 hit
4. 그래프 빌드 — 위키링크/태그 공유 시 엣지 생성
5. 프론트 빌드 OK, 노트 탭 진입·CRUD UI 동작
6. 회귀: 기능 A 정상, 기능 A 인용 칩에 `__knowledge__` 카테고리 hit이 매칭되지 않도록 `getDocumentDownloadUrl` 사용 경로에서 부재 시 404 (정상)

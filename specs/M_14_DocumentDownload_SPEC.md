# M_14 — DocumentDownload SPEC

## 목적

RAG가 답변 시 인용한 원본 문서를 사용자가 즉시 다운로드받을 수 있게 한다.

현재는 청크와 벡터만 LanceDB에 저장되고 원본 파일은 보관하지 않는다. 사용자는 답변에서 파일명을 듣고 별도로 파일을 찾아야 한다. 이 모듈은:

1. 업로드된 원본 파일을 `data/rag_originals/<doc_id>/<filename>` 에 보관
2. `GET /api/rag/documents/{doc_id}/download` 엔드포인트 제공
3. 문서 목록(DocumentsView)과 AI 답변 둘 다에서 다운로드 버튼/링크 제공
4. WebSocket으로 답변에 사용된 `doc_id` 리스트를 별도 메시지(`rag-hits`)로 송신, 프론트가 다음 AI 메시지에 인용 칩으로 표시

## 비목표

- hwpx → PDF 변환은 도입하지 않는다. hwpx는 기존처럼 페이지 정보 없이 임베딩, 원본 그대로 보관·제공.
- 원본 파일의 버전 관리는 도입하지 않는다. 동일 `doc_id`에 재업로드는 발생하지 않는 구조(`doc_id`에 uuid8 suffix).

## 데이터 모델

```
data/
  rag_originals/
    <doc_id>/
      <filename>      # 1 파일 (현재 1개로 충분)
```

`doc_id = f"{filename}_{uuid8}"` 는 기존 `upload_document`에서 이미 부여. 디렉토리 분리로 한글 파일명·동일 파일명 다중 업로드 안전.

## API

### POST /api/rag/documents (변경)
업로드 성공 시 원본을 `data/rag_originals/{doc_id}/{filename}`에 저장. 청크 upsert **실패 시 저장 안 함**. 응답 스키마는 기존과 동일.

### GET /api/rag/documents/{doc_id}/download (신규)
- 200: `FileResponse`, `Content-Disposition: attachment; filename=<원본 파일명>`
- 404: 원본 디렉토리·파일 없음

### DELETE /api/rag/documents/{doc_id} (변경)
청크 삭제 후 `data/rag_originals/{doc_id}/` 디렉토리도 제거. 디렉토리 부재 시 무시.

## WebSocket 확장

### `rag-hits` (신규, 백→프론트)
```json
{ "type": "rag-hits", "doc_ids": ["문서.pdf_a1b2c3d4", "..."] }
```
AI 답변 시작 직전(또는 첫 텍스트 청크 직후)에 송신. 프론트는 다음 AI 메시지(`Message` 객체)에 인용 메타로 첨부한다.

### 백엔드 변경
- `src/agent/upstream_adapter.py _augment_with_rag`: 인용 청크에서 `doc_id` 수집해 인스턴스 변수 `self._last_rag_doc_ids: list[str]` 에 저장
- `src/app/ws_handler.py` 또는 `ws_route.py` (응답 직전 코드 경로): adapter에서 `_last_rag_doc_ids`를 pop해 ws로 송신

## 프론트엔드 변경

### `web/src/types.ts`
- `WsRagHitsMessage` 추가
- `WsIncomingMessage` union에 추가
- `Message` 인터페이스에 `citedDocs?: {id: string; filename: string}[]` 옵셔널 필드

### `web/src/services/api.ts`
- `getDocumentDownloadUrl(docId: string): string`

### `web/src/services/websocket.ts`
- 모듈 변수 `pendingCitationDocIds: string[] | null`
- `rag-hits` 수신 시 `pendingCitationDocIds = msg.doc_ids`
- AI `message` 수신 시 `pendingCitationDocIds`를 메시지에 첨부 후 `null` 리셋
- 필요 시 `fetchDocuments()`로 doc_id → filename 매핑 (캐시)

### `web/src/components/DocumentsView.tsx`
- 각 문서 행에 `Download` 아이콘 버튼 → `window.open(getDocumentDownloadUrl(doc.id))` (또는 Electron save dialog 처리 — `will-download`는 이미 main에서 가로채는 중이므로 OK)

### `web/src/components/ChatPanel.tsx ChatContent`
- AI 메시지 본문 아래 `citedDocs`가 있으면 칩 목록 렌더, 각 칩 클릭 시 다운로드

## 회귀 위험

- `delete_document` 호출 흐름에서 원본 디렉토리 제거 — 디렉토리 부재여도 예외가 발생하지 않도록 `shutil.rmtree(..., ignore_errors=True)` 사용
- `rag-hits` 송신 시점이 AI `message` 송신보다 늦으면 인용이 안 붙음 → 송신 순서 보장 필요. `_augment_with_rag`에서 저장만 하고 ws_route에서 응답 직전 송신
- 인용 칩 클릭은 click-through가 해제된 상태에서만 동작 — 이미 ChatPanel `pointer-events: auto` 적용되어 있어 추가 작업 불필요

## 검증

1. 백엔드 단독 실행 → curl로 업로드·다운로드 라운드트립
2. 업로드 후 `data/rag_originals/<doc_id>/<filename>` 존재 확인
3. 삭제 후 디렉토리 제거 확인
4. UI: 문서 탭 다운로드 → OS save dialog
5. UI: 채팅 질문 → AI 답변 칩 → 다운로드
6. 회귀: pet 모드 click-through, window 모드 토글, 일반 RAG 검색 모두 정상

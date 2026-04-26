# ERROR_HISTORY.md — 과거 오류 및 교훈

Claude Code가 이 프로젝트 작업 시 반드시 참고해야 할 오류 이력.
같은 실수를 반복하지 않기 위해 작성.

---

## E-01: dragLock이 바탕화면을 완전히 차단하는 문제

**날짜**: 2026-04-25  
**증상**: 문서 탭을 열면 바탕화면/다른 앱 클릭이 전혀 안 됨.  
**원인**: `setDragLock(true)` → `setIgnoreMouseEvents(false)` 호출 → 창이 전체 마우스 이벤트를 흡수함.  
**잘못된 수정**: App.tsx에 `chatTab === "documents"` 일 때 dragLock=true 설정 + mousedown 핸들러에서 body/documentElement 클릭 무시.  
**교훈**:
- `setIgnoreMouseEvents(false)`는 모든 바탕화면 상호작용을 차단함. 제한적 사용만 가능 (드래그 중에만).
- OS 파일 드래그(Finder/Explorer drag)는 `setIgnoreMouseEvents(true, {forward:true})`로는 절대 받을 수 없음. forward:true는 mousemove만 전달. OS 드래그는 NSDraggingDestination 프로토콜.
- 해결 방법: "파일 선택" 버튼만 사용하거나, main.ts에서 `win.webContents.on('will-navigate',...)` 같은 Electron native API 사용.

---

## E-02: 폴더 생성 409 오류 + 중복 추가

**날짜**: 2026-04-25  
**증상**: 폴더 생성 시 "이미 존재합니다" 경고가 뜨면서도 폴더가 추가됨.  
**원인**: 이중 제출(double submit) — 사용자가 Enter 2회 누름. 첫 번째 요청 성공, 두 번째에서 409.  
**수정**: `isCreating` 플래그로 중복 제출 방지.  
**추가 원인**: 테스트 중 curl로 같은 이름 폴더 생성. `data/rag_folders.json`에 중복 존재.  
**교훈**: 폼 제출 핸들러에는 항상 재진입 방지 플래그를 추가할 것.

---

## E-03: App.tsx의 `chatTab` 상태 제거 후 남은 임포트

**날짜**: 2026-04-25  
**증상**: App.tsx에서 `chatTab`을 store에서 읽었지만 setDragLock 제거 후 사용처가 없어짐.  
**수정**: `chatTab` import 및 사용 코드 완전 제거. `chatOpen`만 유지.

---

## E-04: 시작 인사 TTS가 묵음인 문제

**날짜**: 2026-04-25  
**증상**: 앱 시작 시 새싹이가 텍스트는 표시하지만 음성이 안 나옴.  
**원인**: 백엔드 포트는 열리지만 MeloTTS 모델 로드까지 추가 8초 소요. 이전 1초 딜레이 불충분.  
**수정**: `/api/tts/speak` 503 응답 시 2초 간격으로 최대 5회 재시도.

---

## E-05: 시작 인사에서 오늘 일정이 없다고 하는 문제

**날짜**: 2026-04-25  
**증상**: 달력에 오늘 일정이 있는데 "오늘은 일정이 없는 날이에요"라고 인사.  
**원인 1**: 일정이 앱 시작 이후에 추가됨 (시작 당시에는 실제로 없었음). 버그 아님.  
**원인 2**: `new Date().toISOString()` 이 UTC 기준이라 KST 자정~09:00 사이에 날짜가 하루 뒤틀림.  
**수정**: KST 오프셋(+9h) 적용 후 날짜 비교.

---

## E-06: 브라우저에서 앱 실행 (Electron 아닌 웹브라우저)

**날짜**: 2026-04-25  
**증상**: `npm run dev` 실행 후 브라우저에서 앱이 열림. 새싹이가 바탕화면을 자유롭게 돌아다니지 못함.  
**올바른 명령**: `npm run electron:dev` (Electron 앱으로 실행).  
**교훈**: 이 프로젝트는 Electron 데스크톱 앱임. 테스트는 항상 Electron으로.

---

## E-07: 코드 작성 전 검토 없이 변경 → 연쇄 회귀

**날짜**: 2026-04-25  
**경위**: 드래그 드롭을 구현하려다 clickthrough 메커니즘을 충분히 이해하지 않고 setDragLock 추가.  
**결과**: 문서 탭 열면 바탕화면 클릭 불가 + 채팅 패널 닫기 불가 두 가지 회귀 동시 발생.  
**교훈**: 변경 전에 `clickthrough.ts`, `main.ts` 읽고 `setIgnoreMouseEvents` 동작 이해 필수.
새로운 기능 추가 시 docs/FRONTEND_CONSTRAINTS.md 먼저 읽을 것.

---

## E-08: setDragLock(chatOpen)으로 파일 클릭 시 채팅 패널 닫힘

**날짜**: 2026-04-25  
**증상**: Finder에서 파일을 클릭하는 순간 채팅 패널이 사라짐.  
**원인**: `setDragLock(chatOpen=true)` → `setIgnoreMouseEvents(false)` → 화면 전체 마우스 이벤트 흡수 → Finder 클릭이 우리 창에 먼저 도달 → `document.body`가 target → mousedown 핸들러가 `setChatOpen(false)` 호출.  
**잘못된 수정**: App.tsx에 `setDragLock(chatOpen)` 효과 추가 (Finder 드래그 수신 목적).  
**교훈**:
- **Finder → 투명창 드래그는 근본적으로 불가능.** `setIgnoreMouseEvents(false)`는 바탕화면 전체를 차단하기 때문에 채팅 열기/닫기와 공존 불가.
- 파일 업로드는 반드시 `<input type="file">` 버튼으로만 구현할 것.
- `setDragLock`은 오직 사용자가 마우스를 누른 채 드래그하는 동안만 사용. 패널 열림 상태 유지 목적 절대 금지.

---

## E-09: macOS에서 입력창 사용 불가 (setIgnoreMouseEvents forward 누락)

**날짜**: 2026-04-25  
**증상**: 메시지 입력창을 클릭해도 포커스가 안 잡히고 타이핑 불가.  
**원인**: `window-manager.ts`의 macOS 경로에서 `setIgnoreMouseEvents(true)` 호출 시 `{forward: true}` 생략.  
`forward:true` 없으면 macOS는 mousemove를 렌더러에 전달하지 않음 → `clickthrough.ts`의 `evaluate()`가 절대 실행되지 않음 → 창이 영구 클릭스루 상태로 고착.  
**수정**: `window-manager.ts`에서 플랫폼 분기 제거, `ignore=true` 시 항상 `{forward: true}` 사용.  
**교훈**: `setIgnoreMouseEvents(true, {forward:true})` — macOS 포함 모든 플랫폼에서 동일하게 적용해야 한다. 플랫폼별 분기는 하지 말 것. `FRONTEND_CONSTRAINTS.md §1` 업데이트 완료.

---

## E-10: RAG 업로드 시 "file 필드가 없습니다" 오류 (FastAPI vs Starlette UploadFile isinstance 버그)

**날짜**: 2026-04-25  
**증상**: UI에서 파일을 업로드하면 항상 422 "file 필드가 없습니다" 반환. curl로 직접 업로드하면 성공.  
**원인**: `rag_routes.py`에서 `request.form()`으로 받은 파일을 `isinstance(file, fastapi.UploadFile)`로 체크.  
`request.form()`은 `starlette.datastructures.UploadFile`을 반환하는데, `fastapi.UploadFile`은 Starlette의 **서브클래스**다.  
따라서 `isinstance(starlette_upload_file, fastapi.UploadFile)` → 항상 `False` → 파일이 있어도 없다고 판정.  
**수정**: `from starlette.datastructures import UploadFile as StarletteUploadFile` import 추가 후 해당 클래스로 체크.  
**교훈**: FastAPI 라우트에서 `request.form()`을 직접 호출할 때는 반드시 `starlette.datastructures.UploadFile`로 isinstance 체크할 것. `fastapi.UploadFile`은 Starlette의 서브클래스이므로 방향이 반대로 적용된다.

---

## E-11: 중복 벡터 스토어 경로 생성 (data/vector_store, data/rag_store, data/lancedb)

**날짜**: 2026-04-25  
**증상**: `data/` 아래 벡터 스토어 경로가 3개 존재(`vector_store`, `rag_store`, `lancedb`). 어느 경로를 쓰는지 모호.  
**원인**: 작업 전 `conf.yaml`과 `service_context.py`를 읽지 않고 코드를 수정하다 경로를 중복 생성.  
**올바른 경로**: `conf.yaml`의 `vector_store_dir: "data/vector_store"` — 이것이 유일한 벡터 스토어.  
**수정**: `data/rag_store`(빈 테이블), `data/lancedb`(빈 DB) 삭제.  
**교훈**: 데이터 경로 관련 작업 전 반드시 `conf.yaml`과 `service_context.py`에서 실제 사용 경로 확인 후 작업할 것. 새 경로를 만들기 전에 기존 경로가 있는지 확인 필수.

---

## E-12: RAG 트리거 정규식 누락으로 RAG가 실행 안 되는 문제

**날짜**: 2026-04-25  
**증상**: "연구개발과 뭐가 있어", "복무규정이 있어" 같은 질문에 RAG가 트리거되지 않음.  
**원인**: `upstream_adapter.py`의 `_RAG_TRIGGER_RE`에 `뭐가`, `있어`(물음표 없이), `어딨`, `규정`, `절차`, `서식` 등 실제 사용 패턴 미포함.  
**수정**: 패턴 추가 — `뭐가|뭐를|뭔지`, `있어|있나|있니|있어요|있나요`(? 없이도 매칭), `어딨`, `규정|절차|기준|서식`.  
**교훈**: RAG 트리거 패턴 수정 시 실제 사용자 발화 예시로 반드시 단위 테스트 돌릴 것. `있어\?`처럼 물음표를 강제하면 구어체 질문의 절반이 누락된다.

---

## E-14: web/dist 절대경로로 빌드 → Electron에서 JS/CSS 미로드, 창 모드 고착

**날짜**: 2026-04-25  
**증상**: 앱을 실행하면 새싹이가 투명 오버레이가 아닌 900×670 흰 박스(창)로 표시됨. petMode가 전혀 활성화되지 않음.  
**원인**: `web/` 디렉토리를 `ELECTRON_BUILD=1` 플래그 없이 `npm run build`로 빌드하면 Vite `base: "/"` 기본값이 적용돼 `index.html`의 asset 경로가 `/assets/index-*.js` (절대경로)로 생성됨. Electron이 `loadFile()`로 `file://` 프로토콜로 로드할 때 `/assets/...`는 파일시스템 루트(`file:///assets/...`)로 해석되어 JS·CSS가 전혀 로드되지 않음. 결과적으로 렌더러는 빈 흰 화면이 되고 `petMode.enable()`이 호출되지 않아 창 모드로 남음.  
**수정**: `cd web && ELECTRON_BUILD=1 npm run build` 로 재빌드. `index.html`의 경로가 `./assets/...` (상대경로)로 바뀌어 `file://` 프로토콜에서 정상 동작.  
**교훈**: `web/` 빌드는 반드시 `ELECTRON_BUILD=1 npm run build`로 실행할 것. `ELECTRON_BUILD` 없이 빌드하면 Electron에서 동작하지 않는 절대경로 빌드가 생성됨. 런처 스크립트(`새싹이.app`)는 이미 `ELECTRON_BUILD=1`을 사용하지만, 개발 중 수동 빌드 시에도 반드시 적용해야 한다.

---

## E-15: 회의록 생성 시 LLM이 빈 JSON 반환 (녹취록 길이 초과)

**날짜**: 2026-04-26  
**증상**: 회의록 생성 시 "LLM이 유효한 JSON을 반환하지 않았습니다 (max_retries 소진): LLM 응답이 유효한 JSON이 아닙니다: Expecting value: line 1 column 1 (char 0)" 오류.  
**원인**: 녹취록 전체를 LLM 컨텍스트에 한 번에 넣으면 로컬 모델(Gemma 등)의 컨텍스트 윈도우를 초과해 빈 응답 반환.  
**수정**: `generator.py`에 청크 분할 로직 추가. 2500자 초과 시 2000자 단위로 분할 → 각 청크를 글머리 요약 → 합산 후 최종 회의록 생성. 오디오 STT 결과·직접 입력 텍스트 두 케이스 모두 동일하게 처리됨.  
**교훈**: 로컬 LLM은 컨텍스트 윈도우가 작다. 긴 입력을 한 번에 넣으면 빈 응답이 반환될 수 있음. 입력 길이 체크 후 청크 요약 파이프라인을 거쳐야 한다.

---

## E-16: 회의록 오디오 라우트 STT 항상 None (속성명·메서드 이중 오류)

**날짜**: 2026-04-26  
**증상**: 오디오 파일 업로드 시 "transcript가 비어 있고 STT도 실패했습니다" 오류 발생. transcript를 직접 입력해도 동일.  
**원인**: `meeting_minutes_routes.py`에 버그 2개 중첩.  
1. `getattr(ctx, "asr_service", None)` → 실제 속성명은 `asr_engine`. 항상 None 반환.  
2. `stt_service.transcribe(Path(tmp_path))` → ASRInterface 실제 메서드는 `async_transcribe_np(np.ndarray)`. 파일 경로가 아닌 numpy 배열 필요.  
**수정**: 속성명 `asr_service` → `asr_engine`, `transcribe(Path)` → `_decode_audio(bytes, suffix)`로 WAV/FLAC/OGG를 16kHz float32 numpy 배열로 변환 후 `async_transcribe_np()` 호출.  
**교훈**: upstream `ServiceContext`의 속성명을 임의로 추정하지 말 것. `service_context.py` 또는 upstream 코드에서 실제 속성명을 확인하고, ASR 인터페이스 (`asr_interface.py`)의 메서드 시그니처를 반드시 확인할 것.

## E-19: ChatPanel 조건부 렌더로 패널 닫힐 때마다 MeetingView 작업 state 소실

**날짜**: 2026-04-26  
**증상**: 새싹이 드래그 시 패널이 닫히고, 다시 열면 회의록 작업(전사 결과, 회의록 내용 등) 전체 초기화.  
**원인**: `App.tsx`에서 `{chatOpen && <ChatPanel />}` — chatOpen=false 시 ChatPanel 언마운트 → MeetingView의 모든 useState 초기화.  
**수정**: `<div style={{ display: chatOpen ? undefined : "none", pointerEvents: chatOpen ? undefined : "none" }}><ChatPanel /></div>` — 항상 마운트하고 CSS로만 숨김으로써 React state 보존.  
**교훈**: 패널처럼 상태가 중요한 컴포넌트는 조건부 렌더 대신 CSS display:none으로 숨길 것.

---

## E-17: transcribe-stream에서 UploadFile 미리 읽지 않아 "read of closed file" 오류

**날짜**: 2026-04-26  
**증상**: `/api/meeting-minutes/transcribe-stream`에 M4A 업로드 시 `{"stage":"error","message":"read of closed file"}` 반환.  
**원인**: `UploadFile`을 `StreamingResponse` async generator 내부에서 `await audio_file.read()` 호출. FastAPI/Starlette는 `StreamingResponse` generator가 소비될 때 이미 업로드 파일이 닫혀 있을 수 있음.  
**수정**: 라우트 핸들러 본문(generator 반환 전)에서 `audio_bytes = await audio_file.read()`, `suffix = ...` 를 미리 읽어두고 generator는 클로저로 캡처한 변수만 사용.  
**교훈**: FastAPI에서 `UploadFile`과 `StreamingResponse`를 함께 쓸 때는 반드시 generator 밖에서 파일을 먼저 읽을 것. `generate-stream` 엔드포인트(asyncio.Queue 패턴)는 이미 올바르게 처리하고 있었으나 `transcribe-stream`은 직접 generator를 사용해서 누락됨.

---

## E-18: LLM이 날짜 미존재 시 "YYYY.MM.DD." 리터럴 반환 → JSON Schema 위반

**날짜**: 2026-04-26  
**증상**: 날짜 정보가 없는 녹취록으로 Step 3 실행 시 `JSON Schema 위반 at date: 'YYYY.MM.DD.' does not match '^\d{4}\.\d{2}\.\d{2}\.$'` 오류.  
**원인**: `USER_PROMPT_TEMPLATE`에서 date 예시를 `"YYYY.MM.DD."` 문자열로만 제시함. 녹취록에 날짜가 없으면 LLM이 예시 그대로 반환.  
**수정**: `generator.py`에서 오늘 날짜(`datetime.date.today()`)를 `today_date` 변수로 계산하여 프롬프트 템플릿에 주입. 프롬프트에 "날짜를 알 수 없으면 오늘 날짜({today_date})를 사용하세요"와 date 기본값을 실제 날짜로 변경.  
**교훈**: LLM에게 형식 예시를 줄 때 플레이스홀더(`YYYY.MM.DD.`)가 아닌 실제 값 또는 명확한 폴백 지시를 함께 제공할 것.

---

## E-20: ChatPanel 탭 전환 시 MeetingView 작업 state 소실

**날짜**: 2026-04-26  
**증상**: 회의록 탭에서 전사/회의록 작업 중 다른 탭으로 전환했다가 돌아오면 모든 진행 내용(전사 텍스트, 회의록 텍스트, 단계 상태, 다운로드 URL)이 사라짐.  
**원인**: `ChatPanel.tsx`의 컨텐츠 영역이 `{chatTab === "meeting" && <MeetingView />}` 조건부 렌더링을 사용했기 때문. 탭 전환 시 MeetingView가 언마운트되어 모든 React state가 초기화됨. App.tsx의 E-19 fix(패널 닫힘 시 state 보존)는 적용되어 있었지만, 탭 전환 레벨의 동일 문제는 미처 수정되지 않은 상태였음.  
**수정**: `ChatPanel.tsx`에서 MeetingView를 항상 마운트하고 `display: chatTab === "meeting" ? "flex" : "none"` CSS로만 표시/숨김 처리.  
**교훈**: 다단계 작업(wizard flow)을 갖는 컴포넌트는 조건부 렌더링이 아닌 CSS display:none으로 숨겨야 함. 패널 오픈/클로즈뿐 아니라 탭 전환도 동일하게 적용해야 한다.

---

## E-13: 문서 탭 업로드 위치 드롭다운을 두 번 클릭해야 선택 적용되는 문제

**날짜**: 2026-04-25  
**증상**: 업로드 위치 드롭다운에서 폴더를 선택해도 첫 번째 선택은 적용되지 않고 두 번 선택해야 함.  
**원인**: native `<select>` 요소를 사용했기 때문. pet 모드 Electron 투명창에서 native select의 드롭다운 팝업은 OS가 렌더링하므로, 팝업 옵션 클릭 시 `mousedown` 이벤트 target이 `document.body` 등 패널 외부 요소로 올라올 수 있음. App.tsx의 `onMouseDown` 핸들러가 이를 패널 외부 클릭으로 판정해 패널을 닫거나 이벤트가 유실되는 상호작용이 발생.  
**수정**: native `<select>` → 완전히 DOM 안에 포함된 커스텀 드롭다운(`<div>` + `<button>`)으로 교체. 옵션 클릭을 `onMouseDown` + `e.stopPropagation()`으로 처리해 App.tsx 핸들러에 전파되지 않도록 함.  
**교훈**: pet 모드 Electron 투명창에서는 native `<select>`, native date picker 등 OS가 렌더링하는 팝업을 사용하지 말 것. 모든 인터랙티브 요소는 DOM 안에 완전히 포함된 커스텀 컴포넌트로 구현해야 한다.

---

## E-21: 문서 탭 사용 후 채팅 입력 클릭이 바탕화면으로 통과하는 문제

**날짜**: 2026-04-26  
**증상**: 문서 탭에서 파일 업로드 후 채팅 탭으로 전환하면 메시지 입력 클릭이 바탕화면으로 통과(click-through 활성화).  
**원인**: `clickthrough.ts`의 `evaluate()` 함수가 `elementFromPoint`로 "비대화형 영역인지" 판정하는데, React re-render·파일 선택 다이얼로그 반환 등 일시적 DOM 상태에서 패널 내부임에도 `body`/`documentElement`가 반환되어 `setIgnoreMouseEvents(true)` 호출. 이 상태에서 마우스가 이미 패널 내부에 있으면 `onMouseEnter` fast path도 발화하지 않아 복구 불가.  
**수정 2가지** (window-manager.ts 수정은 pet mode 회귀로 인해 적용하지 않음):  
1. `clickthrough.ts` `evaluate()`: `setIgnoreMouseEvents(true)`를 호출하기 전 `#chat-panel`과 `#char-widget`의 bounding box를 확인 — 커서가 위젯 내부에 있으면 click-through 활성화 차단.  
2. `ChatPanel.tsx`: `onMouseEnter`에 더해 `onMouseMove`도 `setIgnoreMouseEvents(false)` 호출 — 패널 내부에서 마우스가 움직이면 즉시 복구.  
**교훈**: `elementFromPoint`는 DOM 과도기 상태에서 일시적으로 `body`를 반환할 수 있다. click-through를 활성화하기 전에 항상 bounding box로 2차 검증해야 한다. `onMouseEnter` fast path만으로는 "마우스가 이미 안에 있을 때 click-through 재활성화" 시나리오를 커버하지 못한다.

---

## E-22: web/dist/ 재빌드 시 ELECTRON_BUILD=1 누락으로 흰 화면 발생

**날짜**: 2026-04-26  
**증상**: 앱 실행 시 흰 화면만 표시, 새싹이 캐릭터 미표시. React가 전혀 마운트되지 않음.  
**원인**: `web/dist/`를 `ELECTRON_BUILD=1` 없이 빌드하면 Vite의 `base`가 `"/"` (기본값)로 설정되어 HTML에 절대 경로 `/assets/index-xxx.js`가 생성됨. Electron이 `loadFile()`로 `file://` 프로토콜로 로드할 때 절대 경로는 `file:///assets/...`로 해석되어 파일이 존재하지 않음 → JavaScript 로드 실패 → 흰 화면.  
**수정**: `ELECTRON_BUILD=1 npm run build`로 재빌드 → `base: "./"` → `./assets/index-xxx.js` 상대 경로 생성 → 정상 로드.  
**교훈**: `web/dist/`를 수동으로 재빌드할 때는 반드시 `ELECTRON_BUILD=1` 환경변수를 설정해야 함. `새싹이.command`의 자동 빌드는 이미 설정되어 있지만, Claude Code가 수동으로 빌드할 때 누락 가능. **빌드 후 반드시 `web/dist/index.html`의 script src가 `./assets/...` (상대 경로)인지 확인할 것.**

---

## E-23: macOS pet 모드에서 파일 피커 이후 키보드 입력 불가

**날짜**: 2026-04-26  
**증상**: 문서 탭에서 파일 업로드(파일 선택 다이얼로그)를 마친 후 채팅 탭에서 메시지 입력이 안 됨. 클릭은 정상이지만 타이핑이 무시됨.  
**원인**: macOS pet 모드에서 `continueSetWindowModePet()`이 `setFocusable(false)`를 설정한다. `setFocusable(false)`는 `canBecomeKeyWindow = NO`를 의미하므로, 네이티브 파일 피커(NSOpenPanel)가 닫힐 때 Electron 창이 key window 지위를 회복하지 못함. 결과적으로 키보드 이벤트가 Electron 창에 전달되지 않아 입력창이 시각적으로는 정상이지만 타이핑이 불가능해 보임.  
**수정**: `window-manager.ts`에 `restoreFocus()` 메서드 추가. `setFocusable(true)` + `win.focus()`로 일시적으로 key window 지위를 회복한 뒤 300ms 후 `setFocusable(false)` 복원. `DocumentsView.tsx`의 `onFileInputChange`(파일 선택 직후)와 `handleFiles finally`(업로드 완료 후) 두 시점에 호출.  
**교훈**: macOS pet 모드에서 네이티브 다이얼로그(file picker, save dialog 등)를 사용한 직후에는 반드시 `restoreFocus()`를 호출해야 한다. `setFocusable(false)` 상태에서는 다이얼로그 종료 후 창이 자동으로 key window 지위를 회복하지 못한다. `restoreFocus()`는 pet 모드에서만 동작하므로 window 모드에서의 회귀 없음.

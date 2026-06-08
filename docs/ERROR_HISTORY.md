# ERROR_HISTORY.md — 과거 오류 및 교훈

Claude Code가 이 프로젝트 작업 시 반드시 참고해야 할 오류 이력.
같은 실수를 반복하지 않기 위해 작성.

---

## E-25: OpenAI 설정 후 대화가 여전히 Ollama로 가는 문제 (2026-05-02)

**증상**: 설정에서 ChatGPT(OpenAI)로 전환·저장했는데 응답이 달라지지 않음.  
**원인**: 3단계 버그 복합.  
1. `gemma_chat_agent.py::_validate_params()`에서 `enforce_private_url()` 호출 — OpenAI URL(`https://api.openai.com/v1`)이 사설망이 아니므로 `AgentInitError` 발생, agent 재초기화 실패.  
2. `gemma_chat_agent.py::create()`에서 Ollama 헬스체크(`probe_ollama()`) 무조건 수행 — OpenAI엔 `/api/version`, `/api/tags` 엔드포인트가 없어 `AgentBackendError` 발생.  
3. `builder.py::build_chat_agent()`가 항상 `ollama_config.base_url/model`만 사용 — `app_config.llm_provider`를 무시하고 Ollama로 고정.  
**수정**:  
- `_validate_params()`에 `is_external: bool` 파라미터 추가 — True이면 `enforce_private_url` 건너뜀.  
- `create()`에 `is_external: bool` 파라미터 추가 — True이면 Ollama 헬스체크 건너뜀.  
- `__init__()`에 `llm_api_key: str`, `is_external: bool` 추가 — external이면 `NoThinkLLM` 대신 `AsyncLLM`에 api_key 전달.  
- `builder.py`: `app_config.llm_provider == LlmProviderKind.OPENAI`일 때 OpenAI base_url/model/api_key + `is_external=True` 사용.  
**교훈**: 새 LLM 공급자를 추가할 때는 반드시 (1) URL 화이트리스트 검증, (2) 헬스체크, (3) LLM 인스턴스 생성 세 곳 모두를 확인해야 한다. `build_chat_agent()`는 `ollama_config`만 받는 것처럼 보이지만 실제로는 `app_config.llm_provider`를 먼저 확인해야 한다.

---

## E-26: OpenAI API 호출 시 "Invalid project ID 'z'" 400 오류 (2026-05-02)

**증상**: OpenAI 설정 후 대화 시 `Error code: 400 — Invalid project ID 'z'` 오류.  
**원인**: upstream `AsyncLLM.__init__`의 `organization_id`와 `project_id` 기본값이 `"z"`. Ollama는 이 값을 무시하지만, 공식 OpenAI API는 `project="z"`를 HTTP 헤더로 전송하면 "Invalid project ID" 400 에러를 반환한다.  
**수정**: `gemma_chat_agent.py::__init__`에서 `is_external=True`일 때 `OpenAICompatibleAsyncLLM`을 생성 시 `organization_id=None, project_id=None`을 명시적으로 전달.  
**교훈**: upstream `AsyncLLM`의 `organization_id="z"` / `project_id="z"` 기본값은 Ollama 전용 더미값이다. 공식 OpenAI API / 기타 외부 API 사용 시 반드시 `None`으로 재설정해야 한다.

---

## E-27: 백엔드 재시작 후 메시지 입력창 키보드 입력 불가 (2026-05-02)

**증상**: `새싹이.command`로 백엔드를 재시작한 후 채팅 메시지 입력창을 클릭해도 키보드 입력이 안 됨. 클릭·탭 전환 등 마우스 동작은 정상.  
**원인**: E-23과 동일한 macOS pet 모드 `setFocusable(false)` 문제. `새싹이.command`(터미널)가 실행되는 동안 터미널이 키보드 포커스(key window 지위)를 가져간다. 터미널이 닫히거나 백그라운드로 가도 Electron 창은 `setFocusable(false)` 상태이므로 key window 지위를 회복하지 못해 키보드 이벤트가 전달되지 않는다.  
**수정**: `ChatPanel.tsx` 메시지 입력창(`<input>`)에 `onClick={() => window.electronAPI?.restoreFocus()}` 추가. 클릭 시 일시적으로 `setFocusable(true)` + `win.focus()`를 호출해 key window 지위를 회복하고, 300ms 후 `setFocusable(false)` 복원.  
**교훈**: macOS pet 모드에서 외부 창(터미널, 다이얼로그 등)이 포커스를 가져간 후 Electron 창으로 돌아올 때는 항상 `restoreFocus()` 호출이 필요하다. 파일 피커뿐 아니라 터미널 실행 후에도 동일한 문제가 발생한다. 채팅 입력창처럼 자주 사용하는 UI 요소에는 `onClick`으로 `restoreFocus()`를 미리 걸어두는 것이 좋다.

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

## E-24: CalendarView 이벤트 추가 모달 — DatePicker 추가 후 모든 입력 필드 비활성화 회귀

**날짜**: 2026-05-02  
**증상**: `AddEventModal`에 커스텀 `DatePicker` 컴포넌트를 추가한 후, 제목 input·시간 select·설명 textarea 등 모든 필드가 클릭·입력 불가해짐.  
**원인**: CSS 스태킹 컨텍스트 충돌. `#chat-panel`은 `position: fixed, z-index: 999`로 자체 스태킹 컨텍스트를 생성한다. 모달 오버레이는 `#chat-panel`의 DOM 자식이므로 `z-index: 2000`이 해당 컨텍스트 안에서만 유효하다. 즉 문서 레벨에서 모달은 `#chat-panel`의 z-index(999)와 동일한 층에 속한다. CharacterWidget은 문서 레벨 `z-index: 1000`으로 CharacterWidget이 모달보다 위에 렌더링된다. 또한 DatePicker wrapper의 `position: relative`가 #chat-panel 스태킹 컨텍스트 내 페인팅 순서에 영향을 주어 입력 필드를 덮는 현상이 발생했다.  
**수정**: `createPortal`로 DatePicker 팝업을 `document.body`에 렌더링하고 `position: fixed, zIndex: 9999`를 사용. 팝업이 문서 레벨 z-index에서 경쟁하게 되어 CharacterWidget(1000) 및 #chat-panel(999) 위에 올바르게 표시됨. DatePicker wrapper에서 `position: relative` 제거. 팝업 컨테이너에 `onMouseDown stopPropagation` 추가하여 App.tsx의 chat-close 핸들러가 팝업 클릭 시 채팅 패널을 닫지 않도록 방지.  
**교훈**: `position: fixed, z-index` 가 있는 컨테이너는 자체 스태킹 컨텍스트를 생성한다. 그 안에 렌더링된 `position: fixed` 자식(모달 등)의 z-index는 해당 컨텍스트 내에서만 유효하다. 문서 레벨에서 다른 요소(예: CharacterWidget)보다 위에 표시되어야 하는 팝업·모달은 `createPortal`로 `document.body`에 렌더링해야 한다. `position: relative` wrapper가 없어도 팝업 위치는 `getBoundingClientRect()` + `position: fixed`로 계산할 수 있다.

---

## E-23: macOS pet 모드에서 파일 피커 이후 키보드 입력 불가

**날짜**: 2026-04-26  
**증상**: 문서 탭에서 파일 업로드(파일 선택 다이얼로그)를 마친 후 채팅 탭에서 메시지 입력이 안 됨. 클릭은 정상이지만 타이핑이 무시됨.  
**원인**: macOS pet 모드에서 `continueSetWindowModePet()`이 `setFocusable(false)`를 설정한다. `setFocusable(false)`는 `canBecomeKeyWindow = NO`를 의미하므로, 네이티브 파일 피커(NSOpenPanel)가 닫힐 때 Electron 창이 key window 지위를 회복하지 못함. 결과적으로 키보드 이벤트가 Electron 창에 전달되지 않아 입력창이 시각적으로는 정상이지만 타이핑이 불가능해 보임.  
**수정**: `window-manager.ts`에 `restoreFocus()` 메서드 추가. `setFocusable(true)` + `win.focus()`로 일시적으로 key window 지위를 회복한 뒤 300ms 후 `setFocusable(false)` 복원. `DocumentsView.tsx`의 `onFileInputChange`(파일 선택 직후)와 `handleFiles finally`(업로드 완료 후) 두 시점에 호출.  
**교훈**: macOS pet 모드에서 네이티브 다이얼로그(file picker, save dialog 등)를 사용한 직후에는 반드시 `restoreFocus()`를 호출해야 한다. `setFocusable(false)` 상태에서는 다이얼로그 종료 후 창이 자동으로 key window 지위를 회복하지 못한다. `restoreFocus()`는 pet 모드에서만 동작하므로 window 모드에서의 회귀 없음.

---

## E-25: RAG 다운로드 칩 미표시 — stripEmotionTags가 `[[doc:...]]` 마커를 먹어버림

**날짜**: 2026-06-07  
**증상**: RAG 근거로 답변할 때 인용 문서 다운로드 칩이 안 보이고, 답변 본문 끝에 stray `]` 한 글자가 남음 (예: "…에 있어요. ]"). LLM은 `[[doc:doc_id]]` 마커를 정상적으로 출력하고 있었음에도 칩이 생성되지 않음.  
**원인**: 답변은 `audio` 메시지의 `display_text`로 도착하는데, `websocket.ts`의 `stripEmotionTags`가 감정 태그 `[joy]`를 제거하려고 **단일 대괄호** 정규식 `/\[([^\]]+)\]/g`을 사용했다. 이게 `attachCitationsToMessage`보다 **먼저** 실행되면서 `[[doc:doc_id]]`에 적용 → 첫 `[`부터 첫 `]`까지인 `[[doc:doc_id]`를 매치해 제거 → 뒤에 `]` 하나가 남음(stray `]`의 정체). 마커가 이미 파괴된 텍스트로 인용 추출이 돌아가니 `[[doc:...]]` 매칭 0건 → 칩 생성 실패. (`message` 경로는 stripEmotionTags를 안 거쳐 마커가 살지만, 실제 TTS 흐름은 audio 경로라 항상 깨졌다.)  
추가 취약점: doc_id가 `회의결과보고서_1.hwpx_5b1cea6e`, `1. 농촌지원정책과 업무편람(2025).hwpx_8f9e28c0`처럼 공백·괄호·점이 섞인 긴 문자열이라, LLM이 대괄호 안에 정확히 복사하지 못해 마커가 깨질 위험이 구조적으로 존재.  
**수정**:  
1. (핵심) `stripEmotionTags` 정규식을 `/(?<!\[)\[([^[\]]+)\](?!\])/g`로 변경 — 앞뒤가 `[`/`]`가 아닌 단일 대괄호 + 내부 무대괄호 태그만 매치해 `[[...]]` 이중괄호 마커를 절대 건드리지 않음. 감정 태그가 아닌 임의 `[표현]`은 원문 보존.  
2. (견고화) 백엔드 `upstream_adapter.py`가 실제 주입한 RAG 문서의 doc_id로 **권위 있는 마커를 직접** `display_text`에 부착(`_last_cited_markers`), LLM이 낸 마커는 `_strip_llm_markers`로 제거. 이로써 LLM의 마커 출력 정확도에 의존하지 않음. doc_id 바이트가 그대로 전달되므로 macOS NFC/NFD 정규화 드리프트도 없음. 마커는 `display_text`에만 붙이고 `tts_text`에선 제외(음성에서 안 읽힘).  
3. (방어) `ChatPanel.tsx`의 `stripNoteMarkers`를 `/\[\[(?:note|doc):[^[\]]*\]{0,2}/g`로 강화 — 닫는 괄호 0~2개의 깨진 부분 마커 잔재도 제거.  
**검증**: 가짜 agent/rag 결정적 테스트로 백엔드 마커 부착·LLM 마커 제거·tts 분리 확인 → 프론트 정규식 파이프라인(node)으로 칩 2개 생성·본문 깨끗 확인 → 실제 백엔드로 인용 doc_id가 문서목록에 존재하고 `/download`가 153KB hwpx(PK 시그니처) 반환함을 확인.  
**교훈**: **정규식으로 텍스트 일부를 제거할 때는 더 구체적인 패턴(`[[...]]`)이 더 일반적인 패턴(`[...]`)의 부분집합으로 잡혀 깨지지 않는지 반드시 확인할 것.** 단일 대괄호 매처는 이중 대괄호 마커를 망가뜨린다. 그리고 **LLM이 긴 opaque ID를 정확히 echo하길 기대하는 설계는 취약**하다 — 백엔드가 이미 알고 있는 권위 데이터(주입한 doc_id)로 마커를 직접 생성하는 편이 견고하다. 마지막으로 **여러 변환 단계(감정태그 제거 → 인용 추출 → 본문 렌더)가 같은 텍스트를 순차 처리할 때는 앞 단계가 뒤 단계가 의존하는 토큰을 파괴하지 않는지 순서를 추적할 것.**

---

## E-26: LLM 공급자(ChatGPT) 설정이 설정탭 재진입 시 Ollama로 표시됨 — (str, Enum) 직렬화 함정

**날짜**: 2026-06-07  
**증상**: 설정에서 LLM을 ChatGPT(openai)로 바꿔 저장하고 대화창에 갔다가 다시 설정으로 돌아오면 공급자가 Ollama gemma4로 표시됨. 채팅 헤더 모델 칩도 Ollama로 보임. 실제 백엔드는 openai로 정상 동작 중이었으나 **화면만** 뒤집힘.  
**원인**: `GET /api/settings/llm-provider`가 `{"provider": str(provider)}`를 반환하는데, `provider`는 `LlmProviderKind(str, Enum)` 멤버다. Python에서 `class X(str, Enum)`의 `str(member)`는 값(`"openai"`)이 아니라 **`"LlmProviderKind.OPENAI"`**를 반환한다(잘 알려진 함정). 프론트(`SettingsView.tsx`)는 `s.provider === "openai" ? "openai" : "ollama"`로 비교하므로 `"LlmProviderKind.OPENAI" !== "openai"` → 무조건 `"ollama"`로 강등. SettingsView는 마운트될 때마다 이 GET을 다시 불러 store(localStorage 포함)를 덮어쓰므로, 설정탭에 재진입하면 사용자가 저장했던 openai 선택이 화면에서 ollama로 되돌아간다. 백엔드 POST·agent 재초기화는 정상 동작하고 있었다(모델은 실제로 openai였음).  
**수정**: `settings_routes.py` GET 핸들러에서 `provider_str = getattr(provider, "value", provider)`로 enum의 `.value`("openai"/"ollama")를 내보내도록 변경. `app_config`가 없을 때의 문자열 fallback("ollama")도 그대로 통과.  
**검증**: 라이브 백엔드로 수정 전 GET이 `"provider":"LlmProviderKind.OPENAI"` 반환을 재현 → 수정 후 `"provider":"openai"` 확인 → POST ollama↔openai 양방향 전환 후 GET 반영 확인 → conf.yaml에 키·provider·meeting_minutes_prompt 온전함 확인.  
**교훈**: **`(str, Enum)` 멤버를 JSON·API로 내보낼 때는 절대 `str(member)`를 쓰지 말 것 — 반드시 `.value`를 쓴다.** (`str(member)`는 "Class.MEMBER"가 됨. Python 3.11+ `enum.StrEnum`은 이 문제가 없지만 본 프로젝트는 `(str, Enum)` 사용.) 그리고 **프론트에서 `x === "openai" ? A : B` 식의 엄격 동등 비교는 백엔드 직렬화가 조금만 달라져도 조용히 잘못된 기본값으로 빠진다** — enum/문자열 경계에서는 정확한 값 계약을 양쪽에서 확인할 것.

---

## E-27: 어댑터(stdlib logging) 로그가 loguru 파일/stderr 싱크에 유실됨

**날짜**: 2026-06-08  
**증상**: `src/agent/upstream_adapter.py`·`gemma_chat_agent.py` 등 `logging.getLogger(__name__)`를 쓰는 모듈의 INFO 로그(예: "RAG 컨텍스트 주입", "IntentGate: intent=...")가 백엔드 로그 파일에 **전혀 남지 않음**. loguru를 쓰는 모듈(app.*)만 보임. 이 때문에 RAG 주입 여부·의도 분류 결과를 로그로 확인할 수 없어, M_16 게이트가 실제로 동작하는지 데이터로 검증하는 것이 막혔다(그리고 과거 RAG 디버깅이 어려웠던 잠재 원인).  
**원인**: `app/logging.py`의 `init_logging`이 loguru sink만 구성하고 **표준 logging → loguru 브리지(InterceptHandler)를 설치하지 않음**. loguru가 stderr를 점유한 뒤 stdlib 루트 로거는 기본 lastResort(WARNING+ only, 핸들러 없음) 상태라 INFO 레코드가 드롭됨. 결과적으로 stdlib 로거 사용 모듈의 관측 로그가 통째로 사라짐.  
**수정**: `app/logging.py`에 표준 `InterceptHandler`를 추가하고 `logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)`로 루트 로거를 loguru에 연결. loguru 권장 통합 패턴. 이후 어댑터의 RAG·IntentGate 로그가 파일에 정상 기록됨.  
**검증**: 브리지 추가 후 백엔드 재기동 → 실제 WS 대화에서 `IntentGate: intent=calendar_add conf=0.97 inject_rag=False`, `IntentGate: intent=doc_query ... rag_source=docs`, `RAG 컨텍스트 주입: ... hits=5` 등이 로그 파일에 찍히는 것을 직접 확인.  
**교훈**: **loguru를 쓰는 앱에서 일부 모듈이 표준 logging을 쓰면 반드시 InterceptHandler 브리지를 설치할 것.** 안 그러면 그 모듈 로그가 조용히 유실되어 "로그로 검증"이라는 원칙 자체가 무력화된다. 로깅 초기화 시 "모든 로거가 같은 싱크로 모이는가"를 확인할 것.

---

## E-28: 문서화되지 않은 upstream 직접 수정 3건 — 패치 관리 체계로 전환

**날짜**: 2026-06-08  
**증상**: `tests/app/test_upstream_integrity.py`가 `conversations/{conversation_utils,single_conversation,tts_manager}.py` 3개가 baseline 해시와 불일치한다며 실패. CLAUDE.md/ARCHITECTURE의 "upstream 참조 전용·직접 수정 금지" 위반.  
**원인**: 이전 작업에서 대화 종료 시 TTS 동기화 버그(프론트 재생완료 신호 무응답 시 무한 대기, 오디오 순서)를 고치려고 upstream 대화 루프 함수를 **직접 수정**했음. 해당 코드가 모듈 레벨 함수라 외부 override 후크가 없어 EXTEND로 풀기 어려웠던 것으로 보이며, 문서화 없이 남았다. 게다가 `scripts/bootstrap.py`가 설치 시 upstream을 `git clone`으로 새로 받으므로, 이 패치들은 **재clone 시 조용히 유실**되어 버그가 재발할 수 있는 상태였다(USB 배포는 rsync라 보존).  
**수정**: 직접 수정을 **정식 패치 관리 체계**로 전환.  
1. 3개 변경을 `patches/0001-conversations-tts-robustness.patch`로 추출, `patches/README.md`에 파일별 변경·사유·revert 위험 문서화.  
2. `scripts/bootstrap.py`: upstream clone을 `UPSTREAM_PINNED_COMMIT`(19b58b1)에 고정 + `apply_upstream_patches()`로 `patches/*.patch`를 멱등 적용(이미 적용 시 skip). 재clone 후에도 패치 보존.  
3. `tests/app/upstream_baseline.json`을 **패치 적용 후 상태**로 재생성. 무결성 테스트는 이제 "관리되는 패치 외의 추가 변조"를 잡는 의미로 동작(docstring 갱신).  
**검증**: 패치 파일이 현재 upstream과 정확히 일치함을 `git apply --reverse --check`로 확인. baseline 재생성 후 무결성 테스트 통과. 패치는 되돌리지 않고 보존(기능 회귀 방지).  
**교훈**: **upstream을 부득이 수정해야 하면(외부 후크 부재 등) 반드시 patches/로 관리하고 bootstrap에 적용 단계를 넣을 것.** 직접 수정은 재clone·재설치 때 조용히 사라져 "내 머신에선 되는데" 버그를 만든다. 무결성 테스트의 baseline은 "관리되는 패치 적용 후 상태"를 기준으로 두어, 정식 패치는 통과시키되 비관리 변조는 계속 차단한다.

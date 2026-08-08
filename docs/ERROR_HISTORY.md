# ERROR_HISTORY.md — 과거 오류 및 교훈

Claude Code가 이 프로젝트 작업 시 반드시 참고해야 할 오류 이력.
같은 실수를 반복하지 않기 위해 작성.

---

## E-57: WSL 한글 입력 불가 — fcitx5가 WSLg Weston의 Wayland input_method 거부로 침묵 종료 (2026-07-15)

**증상**: WSL에서 한/영 전환 불가(입력기 부재). fcitx5 + fcitx5-hangul 설치·환경변수 설정 후에도 여전히 불가 — fcitx5 데몬이 시작 직후 **exit 0으로 조용히 종료**되어 있었음.
**원인**: WSLg의 Weston이 Wayland `zwp_input_method_v1` 바인딩을 거부(`permission to bind input_method denied`) → fcitx5의 wayland 애드온이 연결 에러(71)로 전체 종료. `WAYLAND_DISPLAY`를 안 줘도 `$XDG_RUNTIME_DIR`의 소켓을 자동 탐지해 붙으므로 env 제거로는 부족.
**수정**: fcitx5를 **X11 전용**으로 실행 — `fcitx5 --disable=wayland,waylandim`. systemd 유저 서비스(`~/.config/systemd/user/fcitx5.service`, Restart=on-failure)로 상주화. 프로파일에 hangul 엔진 + 전환키(Ctrl+Space·Hangul) 설정. 새싹이.sh가 `GTK_IM_MODULE=fcitx` 등 export 후 서비스 기동. 상세 절차는 install.md §6.
**검증**: 미니 Electron 창 + XTEST 자동 시나리오로 Ctrl+Space → 'gks' → **"한 " 조합·커밋 확인**. 실제 앱에서도 사용자 확인(앱은 fcitx5 상주 "이후" 시작해야 IME가 붙음 — 재시작 필요했음).
**교훈**: (1) **WSLg에서 IME는 X11 경로만 가능** — Wayland 애드온은 명시적으로 꺼야 하며, 데몬이 exit 0으로 죽으니 journal을 봐야 원인이 보인다. (2) WSLg I/O 제약 목록(install.md §7): 텍스트 클립보드만 동기화, 이미지·파일 클립보드·파일 드래그는 Windows↔WSLg 간 불가 — "앱 버그"로 오인하기 쉬움. (3) IME·클립보드류는 미니 Electron 창 + XTEST 리그로 사용자 손 없이 자동 검증 가능.

---

## E-56: 테스트 순서 의존 PyO3 ImportError — patch.dict(sys.modules) 안에서 처음 import된 Rust 확장이 증발 (2026-07-15)

**증상**: 전체 스위트에서만 `tests/e2e/test_e2e_08_citation_links`가 `ImportError: PyO3 modules compiled for CPython 3.8 or older may only be initialized once per interpreter process`로 실패. 단독·대부분의 부분 조합에서는 통과 (3자 이상 상호작용).
**원인**: `tests/app/test_service_context.py`가 `patch.dict(sys.modules, {...})` 컨텍스트 **안에서** `load_app_services()`를 호출 → 그 안에서 sentence_transformers→transformers→safetensors(Rust/PyO3) 체인이 프로세스 **최초로** import됨 → patch.dict는 종료 시 "컨텍스트 중 추가된 키"를 전부 제거하므로 이 모듈들이 sys.modules에서 증발 → PyO3 확장은 프로세스당 1회만 초기화 가능하므로 이후 e2e_08의 재import가 폭발. (이 체인이 컨텍스트 밖에서 먼저 import된 적이 있으면 스냅샷에 포함되어 안 터짐 — 순서 의존의 정체.)
**수정**: `test_service_context.py` 모듈 레벨에서 `import sentence_transformers`를 선행(try/except) — patch.dict 스냅샷에 체인이 항상 포함되도록 고정.
**검증**: 최소 재현 조합(test_service_context + e2e_08)이 수정 전 실패 → 수정 후 통과. 전체 942개 통과.
**교훈**: **`patch.dict(sys.modules, ...)` 안에서 실 코드를 실행하면, 그 코드가 처음 import한 모든 모듈이 컨텍스트 종료와 함께 제거된다.** 순수 Python 모듈은 재import되지만 PyO3/Rust 확장은 프로세스당 1회 제약으로 죽는다. sys.modules를 패치하는 테스트에서 무거운 실 코드를 부르려면 해당 import 체인을 컨텍스트 밖에서 선행시킬 것. 그리고 **순서 의존 플레이크는 pytest-timeout(--timeout)으로 돌리면 행 대신 스택을 얻는다.**

---

## E-55: pii_mask 정규식이 초대형 로그 메시지에서 O(n²) 백트래킹 — 테스트/프로세스가 수십 분 멈춤 (2026-07-15)

**증상**: 전체 pytest가 75% 지점에서 100% CPU로 20분 이상 무진행. pytest-timeout 스택으로 `src/app/logging.py pii_mask`에서 스핀 확인. 발화 조건: 앞선 테스트가 `init_logging()`으로 PII 필터를 설치한 상태에서 `test_a3_search_docs_query_1mb`(1MB 쿼리 거부 검사)가 dispatch 로그에 1MB 문자열을 실음.
**원인**: `_EMAIL_RE = r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"` — @ 없는 1MB 문자열에서 각 위치마다 `[\w.+-]+`가 끝까지 먹고 실패·백트래킹을 반복 → O(n²) (10¹² 스텝급). **실서비스에서도 사용자가 초대형 텍스트를 입력하면 로깅 경로에서 백엔드가 얼어붙는 실제 버그** (단독 테스트에서는 로깅 미초기화라 필터가 없어 재현 안 됐던 것).
**수정**: `_pii_filter`에서 마스킹 전 메시지를 10,000자로 절단(`... (truncated N chars)` 표시). 마스킹은 절단 후 본문에만 적용되므로 PII 유출 없음. 1MB짜리 로그 라인 자체가 비정상이므로 관측성 손실도 없음.
**검증**: 수정 후 해당 테스트 포함 전체 942개가 77초에 통과 (이전: 무한 대기).
**교훈**: (1) **로그 필터/포매터에 정규식을 쓸 때는 입력 길이 상한부터** — 사용자 유래 문자열이 로그로 흘러들면 정규식 백트래킹이 DoS가 된다. (2) `[\w.+-]+@` 류의 "구분자 앞 탐욕 반복"은 구분자가 없는 입력에서 파국적이다. (3) **"테스트가 안 끝난다" = 행이 아니라 매우 느린 계산일 수 있다** — ps로 CPU 100%인지 (스핀) 0%인지 (대기) 먼저 볼 것.

---

## E-54: 새 PC(WSL) 이식 시 "아무것도 안 됨" — conf.yaml 템플릿 초기화 + TTS 자산 부재가 LLM까지 전멸시킴 (2026-07-14)

**증상**: Windows→WSL로 프로젝트 이식 후 캐릭터는 뜨는데 대화·TTS 전부 무반응. WS 테스트 시 `Error processing agent response: 'NoneType' object has no attribute 'chat'`, `/api/tts/speak` 무한 503.
**원인** (3중 복합 — 모두 "새 머신에 git 미추적 자산이 없어서"):
1. `conf.yaml`은 git 미추적이라 `start.sh`가 `conf.example.yaml`을 복사해 새로 만들었는데, 템플릿 기본값이 `openai_llm`(+placeholder 키)이라 원래 쓰던 Ollama 설정이 소실됨. 모델명도 템플릿은 `gemma4:e4b`인데 설치된 것은 `gemma4:latest`.
2. `assets/models/melotts-ko`(git 미추적)가 없어 melo가 HF 자동 다운로드 폴백 → `_set_offline_env_vars()`가 HF 오프라인을 강제하므로 연결 실패 → **TTS 초기화 실패가 `load_from_config` 전체를 죽여 LLM/agent까지 None**이 됨. TTS 하나 죽었는데 대화 전체가 전멸.
3. melo는 `cleaner.py` import 시 **모든 언어 모듈**을 로드하며 각 언어 BERT 토크나이저를 HF 캐시에서 찾음(한국어만 써도 ja/en/fr/es/multilingual 토크나이저 필요). 새 머신은 HF 캐시가 비어 있어 실패.
**수정**: conf.yaml provider를 ollama(+`gemma4:latest`)로 정정. `myshell-ai/MeloTTS-Korean` → `assets/models/melotts-ko`, `kykim/bert-kor-base` + melo 요구 토크나이저 5종(tohoku-nlp/bert-base-japanese-v3, bert-base-uncased, bert-base-multilingual-uncased, dbmdz/bert-base-french-europeana-cased, dccuchile/bert-base-spanish-wwm-uncased) → HF 캐시 다운로드.
**검증**: MeloTTSEngine 단독 init + 합성(280KB wav, cuda) 성공 → 백엔드 재기동 시 ERROR 0건 → `scripts/ws_test.py`로 실제 대화: LLM 응답 + 오디오 payload(1.1MB) + conversation-chain-end 수신.
**교훈**: (1) **머신 이식 체크리스트가 필요하다**: git 미추적 요소 = `conf.yaml`(설정 원본!), `upstream/`, `assets/models/*`, HF 캐시. bootstrap.py가 upstream·BGE-M3는 챙기지만 melotts-ko·BERT 토크나이저는 안 챙긴다(추가 후보). (2) **TTS 초기화 실패가 대화 전체를 죽이는 결합은 위험** — load_from_config의 부분 실패 격리를 검토할 것. (3) 템플릿 복사로 생성된 conf.yaml은 "원래 설정"이 아니다 — 이식 후 provider·모델명부터 확인.

---

## E-53: 리눅스(WSLg) 펫 모드 영구 클릭스루 — setIgnoreMouseEvents의 forward는 darwin/win32 전용 (2026-07-14)

**증상**: WSL에서 캐릭터는 표시되나 클릭·호버 전부 무반응(클릭이 바탕화면으로 통과).
**원인**: click-through 해제는 렌더러 `clickthrough.ts`의 mousemove 기반 `evaluate()`가 담당하는데, `setIgnoreMouseEvents(true, {forward:true})`의 `forward` 옵션이 **`@platform darwin,win32`** (electron.d.ts로 확정) — 리눅스에서는 클릭스루 중 mousemove가 렌더러에 전달되지 않아 evaluate()가 영영 실행되지 않고 창이 영구 클릭스루로 고착 (E-09의 리눅스판, 단 forward를 줘도 소용없음).
**수정 1차(실패)**: `window-manager.ts`에 커서 폴러 추가 — 80ms 간격 `screen.getCursorScreenPoint()` → `webContents.sendInputEvent({type:'mouseMove'})` 합성 주입. 그러나 **WSLg에서 `getCursorScreenPoint()`가 실제 마우스를 따라가지 않는 고착값을 반환**해 폴러가 헛돌았다 (X 서버 `QueryPointer`와 수 백 px 어긋난 채 몇 분간 불변인 것을 CDP+x11로 확인).
**수정 2차(최종)**: 폴러가 **X 서버에 직접 `QueryPointer`** (frontend에 `x11` 패키지 추가, 순수 JS)로 커서를 읽도록 변경. X 연결 실패 시 `getCursorScreenPoint()` 폴백. pet 모드 진입 시 시작, window 모드 전환 시 중지. (사고 2의 setFocusable/setIgnoreMouseEvents 경로는 불변경 — 독립 추가.)
**검증**: 전자동 E2E 구축 — X `WarpPointer`로 캐릭터 위 이동(X QueryPointer 기반 폴러는 이를 실입력처럼 추적) → 렌더러 수신 좌표 일치 확인 → XTEST 실클릭 → `char-widget`에 mousedown/click 도달 + **채팅 패널 실제 열림**(display:flex, 580×660) 확인.
**교훈**: (1) **Electron 창 API는 플랫폼 주석(`@platform`)부터 확인할 것** — `forward`(darwin,win32), `setOpacity`(linux 미지원) 등 리눅스 구멍이 많다. (2) **WSLg에서는 Electron의 커서/화면 API(getCursorScreenPoint·desktopCapturer)를 신뢰하지 말 것** — 입력의 진실은 X 서버(QueryPointer)에 있다. (3) X `WarpPointer`+XTEST+CDP 조합이면 사용자 손 없이 hover/클릭 E2E를 자동화할 수 있다.

---

## E-52: WSLg에서 투명창 렌더링 실패(GPU 프로세스 사망) + WSLg 표시 계층 고착 (2026-07-14)

**증상**: WSL에서 Electron 실행 시 `Exiting GPU process due to errors during initialization` 후 창이 화면에 안 보임. GPU 에러를 고쳐도 안 보이는 상태 지속.
**원인**: 2중 — (1) WSLg에서 Electron GPU 프로세스 초기화 실패 → 투명 프레임리스 창 렌더링 불가. (2) 별개로 **WSLg 표시 계층 자체가 고착**된 상태였음: X11 map_state=Viewable, 렌더러 페이지 캡처에 캐릭터 정상, Weston이 RDP RAIL에 창 등록까지 확인됐는데도 Windows 화면에 아무것도 안 나옴(빨간색 전면 칠 테스트로 확정). WSL 세션 7시간 경과 후 발생 — 알려진 WSLg 버그.
**수정**: (1) `frontend/src/main/index.ts`에 리눅스 한정 `app.disableHardwareAcceleration()` + `enable-transparent-visuals` 스위치 (app ready 이전 호출, Win/mac 무영향). (2)는 코드로 불가 — **Windows PowerShell에서 `wsl --shutdown` 후 재시작**으로 해결(사용자 확인: 캐릭터 표시됨).
**검증**: 수정 후 GPU 에러 로그 소멸, WSL 재시작 후 캐릭터 표시 확인(사용자). 진단 도구: X11 map_state 조회(x11 npm), CDP `Page.captureScreenshot`(렌더러 캡처), 전면 색칠 테스트 — WSLg에서 desktopCapturer 화면 캡처는 전부 검정(사용 불가).
**교훈**: "창이 안 보인다"는 (a) 렌더링 실패 (b) 창 미표시 (c) 표시 계층 고장의 3층 문제다. **렌더러 캡처(CDP) → X11 map 상태 → 컴포지터 로그** 순으로 층을 분리해 진단하면 코드 탓인지 환경 탓인지 가려진다. WSLg에서 GUI가 안 보이면 우선 `wsl --shutdown` 재시작부터 의심할 것.

---

## E-51: AI 답변 본문에 HTML 태그(`<span>`)·단일괄호 인용 마커가 찌꺼기로 노출 (2026-06-13)

**증상**: 답변 본문에 `<span style="font-family: serif;">[doc:3. ...업무편람.hwpx_72a81791]</span>` 같은 **HTML 태그 + 인용 마커**가 그대로 보임. 파일명도 `<span style=...>파일명.hwpx</span>`로 감싸져 노출.
**원인**: 정상 인용 마커 규약은 `[[doc:<doc_id>]]`(이중괄호)이고 프론트가 이를 칩으로 변환·본문에서 제거한다. 그러나 **gemma4(8B)가 형식을 못 지키고** 단일괄호 `[doc:id]` + 임의의 HTML `<span style="font-family: serif;">`로 출력했다(코드엔 `font-family: serif`가 전혀 없음 → 순수 모델 환각). `ChatPanel.stripNoteMarkers`는 **이중괄호만** 제거하므로 단일괄호 마커와 HTML이 누출됐다. react-markdown은 기본적으로 raw HTML을 렌더하지 않아 태그가 텍스트 찌꺼기로 표시됨.
**수정**: `stripNoteMarkers`에 (1) 단일괄호 `[doc:id]`/`[note:slug]` 제거, (2) HTML 태그(`<...>`) 제거를 추가. 모델이 형식을 어떻게 틀리든 프론트에서 **결정론적으로** 정리.
- 검증: 실제 찌꺼기 패턴(span+단일마커+평문 파일명)에 적용 → 태그·마커 제거, 파일명 평문 유지, 마크다운 블록(제목·표·`\n`)은 보존. `tsc`·`vite build` 성공.
**교훈**: **작은 로컬 LLM(8B)의 출력 형식은 신뢰할 수 없다** — 인용 마커/HTML 같은 구조적 토큰의 정리는 프롬프트가 아니라 **프론트에서 형식 변형에 견고하게** 처리해야 한다. react-markdown 기본은 raw HTML 미렌더이므로, 모델이 뱉은 HTML은 strip하지 않으면 그대로 노출된다.

---

## E-50: AI 답변의 마크다운(제목·표)이 렌더링 안 되고 한 줄로 붙음 — 줄바꿈을 공백으로 뭉갬 (2026-06-13)

**증상**: 채팅 답변에 모델이 마크다운(`##`, 표 `| |`, 목록)을 정상적으로 출력하는데, 화면에는 `##`·`|`가 **날것 텍스트**로 보이고 전체가 **한 줄로 붙어** 표시됨. 인라인인 `**굵게**`만 렌더링됨.
**원인** (WS 원본 캡처로 확정): 백엔드 `display_text`에는 `\n\n`(마크다운 블록 구분 줄바꿈)이 **정상 포함**돼 옴(데이터는 멀쩡). 문제는 프론트 `ChatPanel.stripNoteMarkers()`의 `.replace(/\s{2,}/g, " ")` — `\s`가 **줄바꿈까지 포함**하므로 `\n\n`이 공백 1개로 뭉개짐. 그 결과 ReactMarkdown이 받는 텍스트가 한 줄이 되어 제목·표·목록을 **블록으로 인식하지 못함**(인라인 bold만 살아남음). 원래 의도는 노트 마커(`[[note:slug]]`) 제거 후 남는 중복 공백 정리였는데, 줄바꿈까지 죽인 것.
**수정**: `ChatPanel.stripNoteMarkers`의 정규식을 `/\s{2,}/` → `/[ \t]{2,}/`로 변경 — **가로 공백(스페이스·탭)만 정리하고 줄바꿈(`\n`)은 보존**.
- 검증: 정규식 비교(샘플 `요지\n\n## 정리\n| 표 |`)에서 OLD는 `\n\n`을 공백으로 뭉갬, NEW는 보존 확인. `tsc`·`vite build` 성공.
**교훈**: **마크다운으로 렌더링할 텍스트에는 `\s` 기반 공백 정리를 절대 쓰지 말 것.** `\n`은 마크다운의 블록 구분자다(제목·표·목록·문단). 중복 공백을 줄이려면 `[ \t]`로 가로 공백만 건드려라. 데이터(백엔드)가 멀쩡한데 화면만 깨지면, 렌더 직전 프론트 텍스트 변환(strip/replace)을 의심할 것.

---

## E-49: 펫↔데스크톱 모드 전환·창 최소화 시 회의록 작업이 전부 사라짐 (2026-06-13)

**증상**: 음성 전사·회의록 작성 중에 펫 모드↔데스크톱 모드를 전환하거나 펫 모드에서 창을 내렸다 올리면, 진행 중이던 작업(올린 음성·전사 결과·작성 중인 회의록)이 전부 초기화됨. "어제 작업이 다 날아갔다 / 기능이 퇴화했다"로 보고. (git 확인 결과 커밋된 작업/기능 손실은 0 — 라이브 세션 상태만 파괴됨.)
**원인** (`App.tsx`·`MeetingView.tsx`로 확정):
- `App.tsx`는 `windowMode === "window"`면 `<DesktopView>`, 펫 모드면 `<CharacterWidget/> + <ChatPanel/>`를 렌더한다 — **모드마다 완전히 다른 컴포넌트 트리**. 모드 전환 시 한쪽 트리가 통째로 언마운트된다.
- `MeetingView`는 `DesktopView`와 `ChatPanel` 양쪽에서 각각 마운트되고, 3단계 작업 상태(audioFile·transcript·meetingNotes·각 step status 등 17개)가 전부 컴포넌트 지역 `useState`였다. 언마운트 → 지역 state 파괴 → 작업 증발. (펫 모드 *내부* 채팅 닫기는 167줄 display:none으로 보존했지만 모드 전환은 못 막음.)
**수정** (상태를 전역 store로 승격):
- `store.ts`에 `MeetingSlice` 추가: `meeting: MeetingWork`(작업 상태 단일 소스) + `patchMeeting(partial)` + `resetMeeting()`. 전역 store는 모듈 싱글톤이라 컴포넌트 언마운트·모드 전환과 무관하게 살아남고, 펫/데스크톱 두 MeetingView 인스턴스가 같은 상태를 공유한다.
- `MeetingView`의 17개 `useState`를 store 구독 + 동일 시그니처 setter 래퍼로 교체(`setStepXSteps`는 `useStore.getState()`로 최신값 읽어 함수형 업데이트 지원). 렌더·핸들러 본문은 거의 무변경. `handleReset`은 `resetMeeting()`로 단순화.
- 음성 File 객체는 메모리에만 유지(앱 재시작 시엔 재선택 필요) — 사용자 합의 범위(모드 전환·최소화 유지)에 맞춤.
- 검증: `tsc --noEmit` 0 오류(누락 참조·미사용 변수 없음), `vite build` 성공, 프론트 테스트 13건 통과.
**교훈**:
1. **모드/탭에 따라 컴포넌트 트리를 분기(`if mode === ... return <A/> else <B/>`)하면, 그 안의 지역 state는 전환 시 전부 파괴된다.** 진행 중 작업처럼 보존돼야 하는 상태는 컴포넌트 지역(useState)이 아니라 **전역 store(모듈 싱글톤)** 에 둘 것.
2. "기능이 퇴화/소실됐다"는 보고는 (a) 실제 코드 회귀 (b) **라이브 세션 상태 파괴**를 구분해야 한다. git으로 커밋 손실 여부부터 확인하면 (b)임을 빨리 가려낼 수 있다 (이번 건은 커밋 손실 0).

---

## E-48: 큰 PDF 업로드 시 백엔드 전체가 죽음 — 네이티브 파서 크래시가 프로세스를 급사시킴 (2026-06-13)

**증상**: 큰 PDF를 업로드하면 화면에 "Network error during upload"가 뜨고, 이후 새싹이 대화·RAG·일정이 전부 무응답이 됨. "또 다 깨졌다"로 반복 보고. 같은 PDF가 어떤 때는 성공(457청크)하고 어떤 때는 실패 — 비결정적.
**원인** (로그·재현으로 확정):
- "Network error during upload"는 프론트 `xhr.onerror`(연결 끊김) — 서버가 보낸 에러가 아니라 **업로드 도중 백엔드가 죽어 연결이 끊긴 것**(E-36과 동일 표면 증상, 원인은 다름).
- 백엔드가 죽은 직접 원인: 업로드 파싱이 `pypdfium2`(네이티브 C++)로 PDF를 읽다가 특정 페이지에서 `[WinError -1073741795] 0xc000001d`(ILLEGAL_INSTRUCTION)로 크래시. 파싱은 `asyncio.to_thread` 워커 **스레드**에서 돌았는데, **네이티브 크래시는 파이썬 try/except로 못 막고 같은 프로세스의 스레드라 백엔드 전체가 급사**. 비결정성은 연속 업로드 시 자원(메모리/VRAM) 압박으로 추정.
**수정** (프로세스 격리):
- `document_ingest/subprocess_parse.py` 신설 — 파싱 정본 로직을 경량 모듈로 분리(무거운 의존성 모듈레벨 import 금지, 파서는 함수 내 지연 import).
- `rag_routes._parse_isolated()` 추가: 파싱을 `ProcessPoolExecutor(max_workers=1, mp_context=spawn)` **별도 프로세스**에서 수행. 워커가 급사하면 `BrokenProcessPool`을 잡아 해당 파일만 **HTTP 422**로 실패 처리 → 백엔드 메인 프로세스는 생존. 업로드 경로(`upload_document`)가 이 격리 파서를 호출하도록 교체. `_parse_to_meta_segments`는 정본 모듈로 위임하는 얇은 래퍼로 유지.
- 매 업로드마다 새 프로세스 → 연속 업로드 시 자원 누적/스파이크 방지. fork가 아닌 **spawn** → 맥/리눅스에서 CUDA/torch 상태 상속으로 인한 2차 크래시 방지.
- 검증: (A) 22MB 실제 PDF(과거 크래시 유발한 152쪽 포함) 격리 파싱 → 233 세그먼트 정상, (B) 워커 강제 급사 → BrokenProcessPool 포착, (C) 크래시 후 부모 생존 + 재파싱 성공. 라이브 백엔드에 실제 PDF 업로드 → 201/95청크 성공 + 업로드 직후 백엔드 HTTP 200 생존 확인. 회귀 테스트 `tests/document_ingest/test_subprocess_parse.py` 5건 추가.
**교훈**:
1. **네이티브 라이브러리(pypdfium2 등)의 크래시는 try/except로 못 막는다.** 한 입력이 전체 서버를 죽일 수 있는 작업(네이티브 파서)은 **별도 프로세스로 격리**해야 백엔드가 한 파일 때문에 통째로 죽지 않는다.
2. "Network error during upload"는 코드 버그가 아니라 **연결 끊김** 신호 — 먼저 백엔드 생존(프로세스/포트)과 직전 로그(크래시 흔적)부터 확인할 것.
3. 멀티프로세스 격리는 **spawn** 컨텍스트로(맥/리눅스 기본 fork는 CUDA 초기화 상태를 상속해 2차 크래시 유발).

---

## E-47: pull 후 프론트엔드 변경 미반영(옛 dist) + 빌드의 bash 의존성 (2026-06-13)

**증상**: 다른 머신에서 작업해 push한 프론트엔드 변경(RAG 업로드 시 캐릭터 동영상 재생)을 `git pull`로 받아왔으나, 앱에는 옛날 그림만 나오고 동영상이 안 나옴. "기능이 다 깨졌다"로 보고됨. 또한 PowerShell/cmd에서 `npm run build` 시 `'bash' is not recognized`로 빌드 실패.
**원인**:
1. **옛 dist 재사용**: `새싹이.cmd`·`start.cmd`가 프론트엔드를 **`web\dist\index.html`이 없을 때만** 빌드. 이미 빌드된 적 있는 머신에서는 pull로 소스(`web/src`, `web/public`)가 새것이 돼도 재빌드를 안 해, Electron이 옛 dist를 로드 → 새 기능 미반영. (소스만 새것, 화면은 옛것.)
2. **빌드의 bash 의존성**: `package.json`의 `prebuild`→`sync-assets`가 `bash scripts/sync-character-assets.sh` 호출. Git Bash가 PATH에 없는 일반 cmd/PowerShell에서는 prebuild가 실패해 빌드 자체가 중단. "사용자가 어디에 설치하든 동일하게 동작" 원칙 위배.
**수정**:
1. **bash 제거(크로스플랫폼)**: `web/scripts/sync-character-assets.mjs`(Node 포팅) 추가, `package.json`의 `sync-assets`를 `node scripts/sync-character-assets.mjs`로 변경 → Windows/macOS 어디서든 `npm run build` 동작.
2. **자동 재빌드**: `web/scripts/check-rebuild.mjs` 추가 — `web/dist/index.html` 부재 또는 `web/src`·`web/public`·핵심 설정파일이 dist보다 최신이면 exit 1(재빌드 필요), 아니면 exit 0. `새싹이.cmd`·`start.cmd`의 "dist 없을 때만 빌드" 블록을 `node check-rebuild.mjs` + `if errorlevel 1` 기반 자동 빌드로 교체. 이제 pull 후 아이콘만 더블클릭하면 변경이 자동 반영됨.
- 검증: PowerShell·Git Bash 양쪽에서 `npm run build` 성공(`✓ built`), dist에 `avatars/uploading.webm` 포함 확인, check-rebuild가 빌드 직후 exit 0 / 소스 변경 시 exit 1 반환 확인.
**교훈**:
1. **빌드 트리거는 "존재 여부"가 아니라 "최신 여부"로** — `if not exist dist`는 최초 1회만 빌드하므로 업데이트가 영원히 반영 안 된다. 소스 mtime > 산출물 mtime 비교가 정답.
2. **빌드 스크립트에 bash/rsync 등 POSIX 전용 의존성을 넣지 말 것** — 멀티 OS 프로젝트는 Node로 작성하면 어디서든 동작한다.
3. "기능이 깨졌다"는 보고는 (a) 백엔드 미기동 (b) dist 미빌드 두 가지가 흔한 원인. 코드 회귀로 단정 말고 런타임 상태부터 확인.
4. **`.cmd`(batch)의 실행 줄(echo 등)에 한글·em대시(—) 등 비ASCII를 절대 넣지 말 것.** 수정 중 `echo [화면 빌드] ...` 한글 echo를 넣었더니 cmd.exe가 멀티바이트 바이트를 오독해 그 뒤 줄 파싱이 통째로 깨졌다(`'OOT'`, `'exist'`, `'ffline'` 같은 단어 파편 에러 + 백엔드 미기동). `chcp 65001`이 있어도 실행 줄의 비ASCII는 위험. echo는 영어로, 한글이 꼭 필요하면 `::` 주석에만(주석은 cmd가 건너뜀). **`.cmd` 수정 후에는 백엔드 실행 직전까지 잘라 실행하는 하니스로 파싱을 반드시 검수.** (줄바꿈은 원래도 LF였고 무관 — 범인은 비ASCII 실행 줄.)

---

## E-46: 연속 노트 작성 시 누락 — 도구 호출은 했지만 title 누락으로 검증 거부, 폴백 사각지대 (2026-06-12)

**증상**: 업무노트를 연속으로 작성시키면 하나가 누락됨. 다른 질문을 한 뒤 다시 시키면 동작.
**원인** (로그 20:44:22로 확정): gemma가 save_knowledge_note를 **호출은 했지만 필수 `title` 필드를 누락** → `invalid_arguments at <root>: 'title' is a required property`로 거부돼 저장 실패. 그런데 E-45 강제 저장 폴백은 `note_saved`를 **ToolCallStart 시점에 True**로 만들기 때문에 "호출했지만 실패"한 턴은 폴백 대상에서 빠졌다 — 미호출(E-45)과 호출-실패(E-46) 사이의 사각지대. 연속 작성 시 직전 성공 메시지를 흉내 내는 8B 모델의 비결정성이 트리거.
**수정** (2겹 결정론 보장):
1. `ToolRouter.dispatch`: save_knowledge_note의 title 누락/공백 시 **summary 첫 줄에서 제목 유도**(40자 클램프, 마크다운 기호 제거) — 사소한 형식 결함으로 저장이 증발하지 않게 입구에서 보정.
2. `upstream_adapter`: `note_saved`를 **ToolCallResult(ok=True) 기준**으로 추적 — 고신뢰 note_save 턴에서 "성공한 저장"이 없으면(미호출이든 호출-실패든) E-45 폴백 발동.
- 회귀 테스트 5건 추가 (호출-실패→폴백, 성공→폴백 미발동, title 보정 2건, 의도 안내).
**교훈**:
1. "도구를 호출했다"와 "도구가 성공했다"는 다르다 — 보장 로직의 추적 기준은 항상 **결과(ok)**여야 한다. Start 시점 플래그는 실패를 성공으로 위장시킨다.
2. LLM 출력의 필수 필드는 거부보다 **유도 가능한 값이면 보정**이 낫다 (제목⊂요약).
3. "더 좋은 LLM이면 해결되나?"의 답: 빈도는 줄지만 비결정성은 남는다 — 백엔드 결정론적 보장이 정답이고, 그러면 작은 모델로도 안전하다.

---

## E-45: "노트로 저장했어요"라고 말만 하고 실제 저장 안 함 — 도구 호출 환각 (2026-06-12)

**증상**: 채팅에서 첨부 자료 요약과 "노트로 저장해 두었어요!" + "[생성된 노트 요약]" 블록까지 출력했는데, 업무 노트 탭에 해당 노트가 없음. `data/knowledge/`에 파일 자체가 생성되지 않음.
**원인** (로그로 확정): 20:06 턴에서 IntentGate는 `intent=note_save conf=0.95`로 정확히 분류했고 첨부 청크 주입(chunks=2)도 정상. 그러나 **gemma4가 save_knowledge_note 도구를 호출하지 않고**(Complete tool calls 로그 부재, LLM 호출 1회뿐) 직전 턴(20:05)의 진짜 저장 메시지 형식을 흉내 낸 답변만 생성 — 전형적인 8B 모델의 도구 호출 환각. 같은 패턴 메시지여도 호출 여부가 비결정적(20:05는 호출, 20:06은 미호출).
**수정** (결정론적 보장):
- `BasicMemoryAgentAdapter`에 `tool_router` 주입 + 턴별 `_last_intent` 추적.
- chat() 종료 시 **게이트가 고신뢰(autonomous=False) note_save로 분류했는데 save_knowledge_note 호출이 없었으면** `_force_save_note()` 폴백 실행: 이번 턴 답변(첨부 내용이 이미 반영됨)을 complete_json으로 노트 JSON화 → `ToolRouter.dispatch("save_knowledge_note", ...)` 직접 호출 → 저장 완료 메시지 + 권위 `[[note:slug]]` 마커 yield.
- related_docs는 첨부 prefix에서 추출(E-44 수정 사용). 폴백 실패는 경고 로그 후 무시(응답 흐름 보존).
- 회귀 테스트 5건 (tests/agent/test_note_save_fallback.py): 강제 저장 발동·chat 의도 미발동·실제 호출 시 중복 방지·저신뢰 미발동·실패 무해성.
**교훈**:
1. **LLM의 "했어요"는 증거가 아니다** — 도구 실행은 반드시 ToolCallStart/Result 이벤트나 핸들러 로그로 확인. 모델은 대화 히스토리의 성공 메시지 형식을 그대로 흉내 낸다.
2. 의도 게이트가 이미 고신뢰 판단을 내린 턴의 핵심 액션은 모델의 자율 도구 호출에 맡기지 말고 **백엔드가 결정론적으로 보장**할 것.

---

## E-44: 공백 포함 파일명 첨부 시 노트가 내용 없이 작성됨 — doc_id 추출이 공백에서 절단 (2026-06-12)

**증상**: pptx를 채팅에 첨부하고 업무 보고하면 노트가 생성되고 임베딩도 정상으로 보이는데, 노트 본문이 자료 내용을 전혀 반영하지 못함 ("(자료 내용에서 핵심 추출하여 작성 예정)" 같은 자리표시자까지 등장). "pptx를 못 읽는 건가?"로 보고됨.
**원인** (로그 `data/logs/app-2026-06-12.log` 1571줄로 확정):
1. pptx 파싱·임베딩은 정상 — 24세그먼트/8,393자 추출, 스토어에 25청크 존재.
2. `upstream_adapter._extract_attached_doc_ids()`의 정규식 `[^\s\)\];,]+`가 **공백에서 캡처를 중단** → `AI 이삭이 서비스 고도화...pptx_d340e9cd`가 `'AI'`로 잘림 → `get_chunks_by_doc_id('AI')` 0건 → LLM이 첨부 내용 없이 노트 작성. 공백 없는 파일명(회의결과보고서_xxx.hwpx)은 정상 주입(chunks=2)된 것이 대조 증거.
3. (부수) gemma가 tool 인자 JSON에서 `\n`을 이중 이스케이프 → 노트 본문에 literal `\n`·`\_`·말미 `\"` 잔재.
**수정**:
- doc_id 추출을 모듈 레벨 함수로 분리하고 정규식을 `doc_id:\s*([^)\]]+)`로 교체 — 닫는 괄호까지 캡처 (프론트 prefix 형식 `filename (doc_id: xxx); ...` 기준).
- `_normalize_llm_text()` 신설 — save_knowledge_note summary의 literal `\n`(실제 줄바꿈 부재 시)·`\_`·말미 `\"` 복원.
- 회귀 테스트 9건 추가 (tests/agent/test_attached_doc_ids.py, tests/tool_router/test_normalize_llm_text.py).
**검증**: 사고 당시 메시지 형식 그대로 추출 → 전체 doc_id 반환, 실제 LanceDB에서 24청크 조회 성공 (기존 0건).
**교훈**:
1. ID에 사용자 유래 문자열(파일명)을 쓰면 **공백·괄호·한글이 들어온다** — 구분자 기반 파싱은 헛점이 생기니 구조적 경계(닫는 괄호)로 캡처할 것. E-33(파일명 `#` URL 절단)과 같은 계열.
2. "임베딩 됐고 노트도 생성됐다"는 겉보기 성공이 내용 전달을 보장하지 않는다 — 첨부 주입은 반드시 `첨부 청크 자동 주입: chunks=N` 로그의 **N>0**으로 확인할 것.

---

## E-43: 결과보고서 생성 하드 실패 — subs maxItems=2 스키마 과잉 제약 (2026-06-12)

**증상**: 3단계 결과보고서 생성이 "LLM 응답이 JSON Schema를 위반했습니다 (max_retries 소진): JSON Schema 위반 at detail_items/0/subs: [...] is too long" 오류로 실패.
**원인**: MEETING_DRAFT_SCHEMA가 ○ 항목당 subs(- 부연)를 `maxItems: 2`로 제한. 회의가 선택지 3개(공통기반/PTU/SCP 클라우드 방식)를 비교하는 내용이라 LLM이 한 ○ 아래 - 를 3개 만드는 것이 자연스럽고 **정확한** 구조인데, 스키마가 거부 → 재시도해도 같은 구조 → 하드 실패. "○당 최대 2개"는 분량 권장 사항이지 유효성 조건이 아니었다.
**수정**:
- 스키마 `subs.maxItems` 2 → 4 (공문서 권장은 프롬프트 문구로만 유도).
- `_normalize_raw_draft()`: 4개 초과 subs는 경고 로그 후 절단 — 어떤 경우에도 이 사유로 하드 실패하지 않음.
**검증**: 실패한 실제 payload(subs 3개) 그대로 재현 스크립트 실행 — 재시도 없이 1회 통과, HWPX에 - 3줄/* 3줄 무손실 출력. tests 57 passed.
**교훈**: LLM 출력 스키마에서 **분량·스타일 권장을 validation 제약으로 넣지 말 것**. 입력(회의 내용)이 제약과 충돌하면 모델이 아무리 재시도해도 통과할 수 없다 — 스타일은 프롬프트로, 스키마는 구조 무결성만.

---

## E-42: 회의 결과보고서에 '주요내용' 카테고리 헤더 누락 + 위계 기호 중복 (2026-06-12)

**증상**: 생성된 HWPX 보고서에 □ 개요와 □ 향후계획 헤더는 있는데 □ 주요내용 헤더가 없음. 내용(○/-/*)은 전부 개요 밑에 붙어 출력.
**원인**:
1. **템플릿에 '주요내용' 헤더가 애초에 없었음** — 구조가 `□ 개요(일시·장소/참석자/{{SUMMARY_*}}) → □ 세부내용({{DETAIL_*}}) → □ 향후계획`. LLM이 모든 ○를 summary_items에 몰아넣고 detail_items를 비우면, E-41에서 추가한 고아 헤더 제거 로직이 "세부내용" 헤더를 삭제 → 결과적으로 본문 카테고리 헤더가 통째로 사라짐.
2. (수정 중 발견) USER_PROMPT의 JSON 예시 text에 `"○ ..."` 기호가 포함돼 있어 LLM이 기호를 text에 따라 씀 → HwpxWriter가 자체 prefix를 또 붙여 `○ ○`, `- -`, `* *` 중복.
**수정**:
- 템플릿 헤더 텍스트 `세부내용` → `주요내용` 교체 (□ 도형은 별도 rect 객체라 보존). 변경 전 파일은 `.bak`으로 백업.
- prompts: 3대 카테고리(개요=summary_items 목적·배경 / 주요내용=detail_items 본문, **비우기 금지** / 향후계획=next_steps) 매핑을 SYSTEM/USER 프롬프트·분량 가이드에 명시. JSON 예시에서 위계 기호 제거.
- `_normalize_raw_draft()`: detail_items가 비고 summary_items가 2개 이상이면 첫 항목(목적)만 개요에 남기고 나머지를 detail_items로 이동 — 헤더 소실 구조적 차단.
- `_strip_marker()`: text/sub/detail/next_steps 선두의 ○/-/* 등 위계 기호 제거 (중복 방지).
- 2단계 텍스트 레이아웃에 `[개요]` 구획 + 목적 줄 추가 — 미리보기·최종 보고서 구조 일치.
**검증**: 신규 백엔드(12399)로 실제 gemma4 E2E — 생성 HWPX 단락 덤프에서 □ 개요/주요내용/향후계획 3개 헤더 + 주요내용 아래 ○ 3건 + 기호 중복 없음 확인. tests 57 passed.
**교훈**:
1. "헤더가 빠진다"는 보고는 LLM 출력이 아니라 **템플릿에 그 헤더가 실재하는지**부터 확인할 것 — 빈 섹션 정리 로직과 결합하면 헤더가 조용히 사라진다.
2. 프롬프트의 JSON 예시는 곧 모델의 출력 형판이다 — 예시 값에 금지 기호를 넣으면 금지 규칙보다 예시가 이긴다.
3. 필수 섹션은 프롬프트 지시만으로 보장하지 말고 **후처리 정규화로 구조적으로 보장**할 것.

---

## E-41: 회의록 HWPX 품질 불량 — 5중 결함 (GPT-5 비호환·지침 미적용·재요약 손실·템플릿 주석·빈 섹션) (2026-06-11)

**증상**: 전사→회의록 텍스트는 괜찮은데 한글 파일 결과물이 "구리고", 설정 탭의 회의록 지침(개조식 -음/-함 종결 등)이 동작하지 않는 느낌.
**원인** (오프라인 E2E 재현으로 확인):
1. **GPT-5에서 생성 API가 통째로 400 실패**: `complete_json`/`complete_text`가 `max_tokens`+`temperature` 전달 — GPT-5는 `max_completion_tokens` 요구·temperature 1.0만 허용. 그래서 회의록은 사실상 Ollama(gemma)로만 생성됐고, gemma의 지시 이행력 한계가 "기준 미적용" 체감의 주원인.
2. 파라미터를 고쳐도 **GPT-5 응답이 빈 문자열**: 추론 모델이라 추론 토큰이 출력 예산을 잠식 → `reasoning_effort=low` + 예산 헤드룸 필요.
3. **2단계(summarize_to_text)가 커스텀 지침을 미사용** — 하드코딩 서술형 프롬프트. 사용자가 보고 수정하는 텍스트가 최종 보고서와 다른 모양.
4. **3단계가 2단계 결과를 "녹취록"으로 재요약** — 정보 손실.
5. **템플릿 결함**: 양식 안내 주석(`[HY헤드라인M 16]`)이 본문 텍스트로 잔존해 모든 보고서에 출력. detail_items가 비면 "세부내용" 헤더가 고아로 남음. LLM이 *를 여러 개 달려고 빈 text sub를 복제하는 패턴(스키마 위반/중복 단락).
**수정**:
- `gemma_chat_agent._completion_params()`: gpt-5→`max_completion_tokens`(+4096 헤드룸)+`reasoning_effort=low`, o-시리즈→`max_completion_tokens`, 그 외 기존 유지.
- 2단계: 커스텀 지침(M_17) 적용 + 출력 형식을 최종 보고서 레이아웃(제목/일시·장소/참석자/[주요내용] ○-*/[향후계획])으로 오버라이드 — 미리보기=최종.
- 3단계 USER_PROMPT: "정리된 회의록 입력 시 재요약 금지, 항목·수치·날짜·담당자 보존" 명시.
- `_normalize_raw_draft()`: 빈 text sub의 detail을 직전 sub의 추가 *로 승격, 빈 항목 제거 — 스키마 검증 전 정규화.
- HwpxWriter: 빈 detail_items/next_steps 시 섹션 헤더 제거, 연속 동일 `-` 텍스트 병합(중복 단락 방지), 빈 일시→날짜 폴백·빈 참석자→"-".
- 템플릿: `[HY헤드라인M 16]` 주석 3건 제거 (재가공).
**검증**: 가상 녹취록으로 전체 파이프라인(2단계→3단계→HWPX) GPT-5 실행 — 개조식 명사형 종결, 수치(94%·280만원·1.2억)·담당자·일정 보존, 빈 섹션 없음, 주석 없음 확인. tests 146 passed.
**교훈**:
1. 새 LLM 모델 추가 시 스트리밍 chat 경로만이 아니라 **completion 계열 헬퍼(complete_json/complete_text)의 파라미터 호환**도 반드시 확인 — 추론 모델은 토큰 예산 의미도 다르다.
2. "지침이 안 먹는다"는 보고는 지침 내용보다 **지침이 그 경로에 배선됐는지**부터 확인.
3. 다단계 LLM 파이프라인에서 중간 산출물을 다음 단계가 재요약하면 정보가 깎인다 — 입력 성격을 프롬프트에 명시할 것.

---

## E-40: RAG 검색 지연 분석 — 파편화 가설 기각, 실제 병목은 무인덱스 엔진 오버헤드 (2026-06-11)

**증상**: 문서가 늘면서 검색 체감 지연. 실측: 쿼리 임베딩 22ms(GPU) + 벡터 검색 ~90ms.
**조사 결과** (가설 검증 과정 기록):
1. 스토어 상태: 14,104 청크가 343개 프래그먼트(조각당 평균 41행), 테이블 버전 788 누적. → 파편화가 주범이라 가설.
2. `tbl.optimize()` 컴팩션 실행: 343조각 → 1조각, top-8 결과 비트 동일. **그러나 검색 시간 99→100ms로 불변 — 가설 기각.**
3. numpy로 동일한 14k×1024 코사인 전수 계산: **3.2ms**. LanceDB 무인덱스 검색 ~90ms의 96%는 수학이 아니라 엔진 오버헤드(쿼리당 디스크 스캔+플래닝).
4. 사본에 IVF-PQ 인덱스(partitions=128, sub_vectors=64) 빌드(6.4초) 후 실제 한국어 쿼리 10개로 recall 측정:
   - nprobes=32, refine=10: 88.8% recall, 29ms
   - **nprobes=128, refine=30: 100.0% recall, 41ms** ← 균형점
**조치**: ① 라이브 스토어 컴팩션 실행(디스크·목록 스캔·파편화 누적 방지 목적), ② `VectorStore.optimize()` 추가 + 업로드/문서삭제/폴더삭제 후 60초 디바운스 자동 컴팩션(`rag_routes._schedule_store_optimize`). ③ 인덱스 도입은 사용자 결정 대기 (recall 100% 파라미터 확보됨).
**교훈**:
1. "파편화 때문에 느리다"는 직관은 측정으로 확인하기 전까지 가설일 뿐이다. 컴팩션 전후 동일 쿼리 벤치마크가 가설을 즉시 기각해줬다.
2. LanceDB 무인덱스 KNN은 데이터가 작아도 쿼리당 수십 ms의 고정 오버헤드가 있다. 수 ms대가 필요하면 인덱스가 유일한 길이다.
3. ANN 도입 시 recall은 기본 파라미터로 가정하지 말고 실제 쿼리로 측정할 것 — 기본값(nprobes=32, refine=10)은 88.8%였고, 전수 프로브+리파인(128/30)으로 100%를 확보했다.

---

## E-39: 일괄 업로드 UI — 완료 후에도 미완처럼 보이는 상태 설계 (2026-06-11)

**증상**: 다수 파일 업로드 시 (1) 전체 진행 상황을 알 수 없음, (2) 목록에 스크롤이 없어 화면 밖 파일의 성공/실패 확인 불가, (3) 새싹이가 neutral로 돌아와(=완료) 사용자는 끝났다고 인지하는데 화면에는 100% 아닌 행들이 남아 혼란.
**원인**: 리프레시 문제가 아니라 **상태 설계 문제**. 성공한 파일 행은 목록에서 즉시 제거되고 실패 행만 영구히 남는 구조 → 완료 시점에 남아있는 건 실패/잔재 행들뿐이라 "전부 100%가 안 된" 것처럼 보였다. 또 XHR 진행률은 전송 진행률이라, 전송 100% 후 서버 파싱·임베딩(응답 대기) 동안 숫자가 멈춰 보였다.
**수정** (`DocumentsView.tsx`):
- UploadItem에 `status: waiting|uploading|processing|done|error` 도입. 성공 행도 "완료 ✓"(녹색)로 **목록에 유지** — 파일별 성공/실패 확인 가능.
- 전송 100% 후 응답 대기 구간은 "분석·임베딩 중…"으로 표시 (멈춘 % 대신).
- 전체 진행 헤더 추가: "업로드 중 — N/M (실패 K)" + 집계 진행 바.
- 파일별 목록에 `maxHeight: 180, overflowY: auto` 스크롤.
- 전부 끝나면 "목록 지우기" 버튼 노출 (수동 정리).
**교훈**: 일괄 작업 UI에서 "완료 항목 즉시 제거"는 잔여 항목만 남겨 전체가 실패한 듯한 인상을 준다. 완료 상태를 명시적으로 남기고 사용자가 정리하게 할 것. 진행률이 전송/처리 2단계로 나뉘면 단계명을 표시할 것.

---

## E-38: 새 폴더 이름 입력 불가 — restoreFocus 미적용 입력 필드 잔존 (2026-06-11)

**증상**: 문서 탭 "새 폴더" 클릭 후 이름 입력창을 클릭해도 커서가 안 생기고 타이핑이 안 됨.
**원인**: E-23/E-27과 동일한 pet 모드 `setFocusable(false)` 문제의 재발. 채팅 입력(ChatPanel)과 노트(NotesView)에는 `restoreFocus()` 패치가 적용됐지만, **새로 추가된 입력 필드들엔 누락**돼 있었다. DOM `focus()`만으로는 OS 키보드 포커스(key window)가 오지 않는다.
**수정**: 전 컴포넌트 입력 필드 일괄 감사(`grep <input|<textarea` vs `restoreFocus` 카운트) 후 누락분 전부 패치 —
- DocumentsView: 새 폴더 입력 + 폴더 이름변경 입력 (onClick + 입력창 열릴 때 useEffect에서도 호출 — 클릭 없이 바로 타이핑 가능하도록)
- CalendarView: 제목·기간·설명 3개
- MeetingView: 녹취/회의록 textarea 4개
- SettingsView: OpenAI 키, 서버 주소 2개
**교훈**: **키보드 입력을 받는 모든 `<input>`/`<textarea>`는 `onClick={() => window.electronAPI?.restoreFocus()}`가 필수**이고, 코드로 `focus()`를 호출하는 useEffect에서는 그 직전에 `restoreFocus()`를 호출해야 한다. 새 입력 필드를 추가할 때마다 빠뜨리면 이 버그가 무한 재발한다 — FRONTEND_CONSTRAINTS §6에 규칙化. 리뷰 시 `grep -c "<input\|<textarea"` 와 `grep -c restoreFocus` 비교로 빠르게 감사 가능.

---

## E-37: melo "model_dir not found" — 로컬 TTS 모델을 한 번도 쓴 적이 없었음 (2026-06-11)

**증상**: 기동마다 `model_dir not found: assets/models/melotts-ko — melo 자동 다운로드 경로 사용` 경고. 사용자는 이 지점에서 로딩이 오래 걸린다고 인지.
**원인**: 3중 결함.
1. `tts/builder.py`의 기본 `asset_root="assets/models"`가 **상대경로** — 런처가 다른 cwd에서 백엔드를 실행하면 경로가 빗나가 "not found". `cache_dir="cache"`도 동일(캐시 폴더가 실행 위치마다 생성됨).
2. `melo_tts_engine.py`가 model_path를 **검증만 하고 로드에 사용하지 않음** — 경로를 찾아도 `MeloTTS(language="KR")`로 호출해 항상 HF 캐시 경로로 로딩. 로컬 모델 디렉토리는 장식이었다.
3. `assets/models/melotts-ko/`의 실체가 깨져 있었음 — `config.json`이 2바이트 `{}`(복사 누락), `checkpoint.pth`는 HF 캐시본과 해시 불일치(손상 추정). 즉 (2)를 고쳐도 로드 불가능한 상태.
**수정**:
- 엔진에서 상대 model_dir/cache_dir을 `_project_root()` 기준으로 해석 (cwd 무관).
- model_path 발견 시 `MeloTTS(config_path=..., ckpt_path=...)`로 **로컬 직접 로드** (HF 캐시/원격 조회 우회).
- 모델 파일 복구: HF 캐시(`models--myshell-ai--MeloTTS-Korean`)에서 정상 config.json + checkpoint.pth 복사 (네트워크 불필요 — 지금까지 실제 동작하던 검증본).
- 검증: cwd=C:\에서 init → 경고 없음, 로컬 로드, 합성 241KB wav 성공. tests/tts 57 passed.
**교훈**:
1. "경고 후 폴백으로 어쨌든 동작"하는 코드는 폴백이 영구 기본값이 된다 — 로컬 자산이 한 번도 안 쓰이고 있어도 아무도 모른다. 폴백 발동을 눈에 띄게 기록하고, 자산 배치를 주기적으로 검증할 것.
2. 모델 자산 복사 후에는 파일 크기만 보지 말고 해시·로드 테스트로 무결성 확인.
3. 참고: TTS 초기화 ~10-20초는 melo+다국어 BERT 로드 자체의 비용으로 이 수정과 무관하게 남는다. 더 줄이려면 lazy init(첫 사용 시 로드) 같은 구조 변경 필요.

---

## E-36: 일괄 업로드 "Network error" + 진행률 행 꼬임 + STT 미초기화 (2026-06-11)

**증상**: (1) RFP 수십 건 일괄 업로드 중 일부 파일만 "Network error during upload", 일부는 0% 멈춤. (2) 회의록 탭 전사 시 "STT 엔진이 초기화되지 않았습니다".
**원인**:
1. Network error 자체는 코드 버그 아님 — 업로드 도중 백엔드가 재시작되던 시간대(13:37~38)에 전송된 파일들이 연결 거부된 것. 백엔드 access log 확인 결과 **도달한 업로드는 전원 201, 5xx 0건**. 실패 파일은 서버에 흔적이 없으므로 재업로드만 하면 됨(부분 상태 없음).
2. 단, 조사 중 실버그 3건 발견:
   - `DocumentsView.tsx` 업로드 목록이 **인덱스로 항목 제거/갱신** — 하나가 끝나 제거되면 나머지의 idx가 밀려 진행률·에러가 엉뚱한 행에 표시됨.
   - `Promise.all`로 **전 파일 동시 업로드**(브라우저 6연결) — 대형 HWPX 파싱을 서버가 6개씩 동시 수행.
   - 업로드 임베딩이 to_thread로 옮겨진 후(E-34) 동시 업로드 시 **같은 SentenceTransformer에 병렬 진입** 가능 — 스레드 안전성 미보장 + VRAM 스파이크.
3. STT: Whisper 모델이 이 PC에 미배치(E-34 참고사항)였고 conf.yaml이 맥 시절 HF 캐시 경로를 가리킴.
**수정**:
- 업로드 목록을 id 기반 추적으로 변경 + `UPLOAD_CONCURRENCY=2` 워커 큐로 동시 업로드 제한. web/dist 재빌드.
- `rag_routes.py`에 모듈 레벨 `_EMBED_LOCK`(threading.Lock)으로 임베딩 직렬화.
- Whisper 모델 배치: `huggingface_hub.snapshot_download('mobiuslabsgmbh/faster-whisper-large-v3-turbo')` → `assets/models/faster-whisper-large-v3-turbo/`(1.6GB), conf.yaml `model_path` 갱신. 검증: CUDA/float16 로드 + 실제 wav 전사("안녕하세요 테스트입니다") + 임시 포트(12394) 풀부팅에서 "ASR 초기화 실패" 0건.
**교훈**:
1. React 목록에서 항목이 제거되는 비동기 작업은 인덱스가 아니라 고유 id로 추적할 것.
2. 파일 일괄 처리 UI는 동시성 상한을 둘 것 — 브라우저 연결 수가 서버 부하 상한이 되게 두면 안 된다.
3. blocking 작업을 to_thread로 옮길 때는 **그 작업이 공유 자원(모델 인스턴스)을 쓰는지** 같이 검토할 것 — 이벤트 루프 블로킹이 사실상 락 역할을 하고 있었을 수 있다.
4. 모델 검증은 임시 포트 풀부팅으로 — 사용 중인 포트(12393)와 충돌 없이 init 경로 전체를 확인할 수 있다.

---

## E-35: MeetingMinutesService 기동 실패 — 템플릿에 placeholder 미주입 (2026-06-11)

**증상**: 시작 로그마다 `MeetingMinutesService 초기화 실패 (템플릿 오류): 필수 placeholder 누락: ['{{ATTENDEES}}', ...]` 경고. 회의록 생성 기능 비활성.
**원인**: 코드 버그가 아니라 **배포 데이터 문제**. `data/Template/회의 결과보고 템플릿.hwpx`는 한글(HWP)로 만든 원본 양식 그대로였고(placeholder 0개), HwpxWriter는 회의록 생성 시 `{{TITLE}}` 등 11개 자리표시자를 찾아 치환하는 설계라 **`scripts/prepare_meeting_template.py`로 자리표시자를 주입한 가공본**이 필요하다. git에는 원본이 커밋돼 있어(2026-03-20) 새 머신 체크아웃에서는 가공 단계가 누락된 상태가 된다.
**수정**: 원본을 `회의 결과보고 템플릿.원본백업.hwpx`로 백업 → `prepare_meeting_template.py` 실행(placeholder 11개 주입 + 견본 단락 6개 제거) → `HwpxWriter(Path(...))` 검증 통과 → 백엔드 재시작 후 `MeetingMinutesService 초기화 완료` 확인.
**교훈**: 새 머신/체크아웃 셋업 시 `prepare_meeting_template.py` 1회 실행이 필수다. 템플릿 양식을 새 한글 파일로 교체할 때도 반드시 재실행할 것(스크립트는 단락 인덱스 하드코딩이라 양식 구조가 바뀌면 PLACEHOLDER_MAP 조정 필요).

---

## E-34: 폴더 일괄삭제 무반응 — Electron 미지원 prompt() + 이벤트 루프 블로킹 (2026-06-11)

**증상**: 문서 탭에서 폴더 삭제 → 확인 다이얼로그에서 "확인"을 눌러도 아무 일도 일어나지 않음. 에러 표시도 없음.
**원인**: 2중 결함.
1. **프론트(직접 원인)**: `DocumentsView.tsx`의 폴더 삭제 2차 확인이 `window.prompt()`(폴더 이름 직접 입력)였는데, **Electron은 prompt()를 지원하지 않고 예외를 던진다.** 이 throw가 try 블록 밖이라 조용히 죽음 → DELETE 요청이 백엔드에 아예 전송되지 않음(백엔드 access log로 확인). 1차 `confirm()`은 Electron이 지원해서 다이얼로그가 떴기 때문에 "확인했는데 안 됨"으로 보였다.
2. **백엔드(잠재 결함)**: 요청이 도달했더라도 `delete_folder`가 (a) async 라우트에서 블로킹 호출 직접 실행 → 이벤트 루프 점유로 채팅·TTS 포함 **앱 전체 정지**, (b) `to_arrow()`로 전체 테이블(벡터 포함, 28k행 ≈ 100MB+)을 로드한 뒤 문서별 `delete_by_doc_id` 반복 — 그 함수도 내부에서 또 전체 테이블 로드. 25문서 × 28k행이면 수 분 소요.
**수정**:
- `DocumentsView.tsx`: prompt() → 2차 confirm()으로 교체. web/dist 재빌드.
- `VectorStore.delete_by_category()` 신설 — `count_rows(filter)` + 단일 predicate `delete()`. 27,841청크 삭제 실측 **0.06초**.
- `delete_by_doc_id`도 `count_rows(filter)`로 교체 (전체 테이블 로드 제거).
- `rag_routes.py`: 폴더 삭제·문서 목록·업로드(파싱+임베딩)를 `asyncio.to_thread`로 — 대형 작업 중에도 채팅이 멈추지 않음.
- `_list_documents_from_store`: `search().select([3개 컬럼])`으로 벡터 로드 제거 (목록 조회 0.17초).
- 부수 수정: loguru 사용 파일들의 `%s` 스타일 로그 호출 일괄 f-string 전환 (loguru는 %-포맷 미지원 → "%s"가 리터럴로 찍혀 **에러 내용이 전부 숨겨지고 있었음**. 이 수정으로 ASR 초기화 실패의 실제 원인이 처음 드러남 — E-34 참고사항: Whisper 모델 미배치).
**교훈**:
1. **Electron 렌더러에서 `window.prompt()` 절대 금지** — confirm/alert은 되지만 prompt는 예외를 던진다. 사용자 입력이 필요하면 인앱 모달로.
2. async 라우트에서 무거운 sync 작업(임베딩·대량 삭제·전체 테이블 로드)을 직접 호출하면 단일 이벤트 루프가 멈춰 앱 전체가 정지한다. 반드시 `asyncio.to_thread`.
3. LanceDB에서 개수 확인은 `count_rows(filter)`, 컬럼 일부 조회는 `search().select([...])` — `to_arrow()`는 벡터까지 전부 메모리에 올린다.
4. loguru에 `logger.info("...%s", x)` 스타일을 쓰면 에러 없이 리터럴 "%s"가 찍힌다 — 진단 정보가 조용히 사라지므로 loguru 파일에선 f-string 필수.
5. "포트 10048 바인드 실패 후 셧다운" 로그는 백엔드 이중 기동이다 — 기존 인스턴스를 먼저 종료할 것.

---

## E-33: 특수문자 파일명 문서 삭제 불가 — "doc_id not found" (2026-06-11)

**증상**: 문서 탭에서 특정 문서 삭제 시 `doc_id '23. 기후변화에 따른 쌀 품질&' not found` 에러. 파일명이 잘린 채 서버에 전달됨.
**원인**: 2중 결함.
1. **프론트**: `web/src/services/api.ts`의 `deleteDocument`가 doc_id를 `encodeURIComponent` 없이 URL에 삽입 (바로 옆 `getDocumentDownloadUrl`은 인코딩함). doc_id는 파일명 기반(`{filename}_{uuid8}`)인데, 웹 포털에서 받은 파일은 이름에 `&#8729;` 같은 HTML 엔티티가 그대로 남아 있고, 그 안의 `#`이 URL fragment로 해석돼 doc_id가 `&`에서 잘려 전송 → 404.
2. **백엔드(더 깊은 문제)**: 일부 업로드에서 multipart 파일명이 UTF-8 surrogateescape로 잘못 디코딩돼, 저장된 doc_id/doc_name에 **lone surrogate(0xDCxx)** 가 포함됨. 이 문자열은 UTF-8 인코딩 불가라 JS `encodeURIComponent`가 예외를 던지므로 프론트 수정만으론 해당 문서를 영영 삭제할 수 없었다.
**수정**:
- `api.ts`: `deleteDocument`/`renameFolder`/`deleteFolder`에 `encodeURIComponent` 추가. `ELECTRON_BUILD=1`로 web/dist 재빌드.
- `rag_routes.py`: 업로드 시 `_sanitize_filename()` 추가 — surrogateescape 바이트 복구 → `html.unescape`(엔티티 → 실제 문자) → Windows 금지문자 제거. 신규 업로드에서 재발 차단.
- 기존 오염 데이터: `scripts/repair_broken_doc_ids.py`(일회성)로 정리 — 사용자가 지우려던 문서는 삭제(183청크+원본), 나머지 2건은 doc_id/doc_name/text를 정상 문자열로 수리 후 동일 벡터로 재-upsert(재임베딩 불필요), 원본 디렉토리명도 동기화. **surrogate가 든 doc_id는 SQL where 문자열 비교도 위험하므로 ASCII-safe한 chunk_id(UUID) IN 절로 삭제**한 것이 포인트.
**교훈**:
1. 경로 파라미터에 사용자 유래 문자열(파일명 등)을 넣을 땐 프론트에서 반드시 `encodeURIComponent`. 한 API 파일 안에서 인코딩 여부가 함수마다 다르면 누락 의심.
2. doc_id 같은 식별자에 원본 파일명을 그대로 쓰면 URL·SQL·JSON 세 군데서 각각 깨질 수 있다. 식별자에 들어가는 외부 문자열은 입구(업로드)에서 정규화할 것.
3. 벡터 스토어 데이터 수리 시 벡터를 그대로 복사해 재-upsert하면 재임베딩 없이 메타데이터만 고칠 수 있다.
4. 백엔드 재시작 직후 LanceDB 첫 조회가 옛 버전을 반환할 수 있다(매니페스트 캐싱 추정). 외부 프로세스에서 스토어를 수정했으면 재조회로 최종 확인.

---

## E-32: 개조식 문서(RFP) 업로드 시 청크 폭증 — 단락마다 1청크 (2026-06-10)

**증상**: 개조식 문장으로 된 3페이지짜리 연구 RFP(HWPX)를 업로드했더니 청크가 ~140개 생성됨. 사실상 불릿 한 줄마다 청크 1개.
**원인**: UI 업로드 경로(`app/rag_routes.py`)가 `DocumentIngest`/`chunk_segments`를 **쓰지 않고** 자체 인라인 청킹(`_chunk_text`, 500자 고정 윈도우)을 사용했다. 흐름이 (1) 파서가 단락(`<hp:p>`, HWPX는 개조식 한 줄 = 1단락)마다 세그먼트 1건 생성 → (2) 각 세그먼트를 독립적으로 `_chunk_text`에 통과시킴. 불릿 한 줄은 500자 미만이라 그대로 1청크가 되어, 단락 수 = 청크 수가 됐다. `chunk_chars` 누적 병합 로직이 단 한 번도 작동하지 않음(병합은 한 세그먼트 *안에서만* 일어나는데 세그먼트가 이미 한 줄짜리). 같은 내용이 PDF였다면 페이지=세그먼트라 정상적으로 묶였을 것 — 즉 포맷에 따라 청크 수가 10배 차이 나는 설계 결함.
**수정**:
- `document_ingest/segments.py`에 `chunk_meta_segments()` 추가 — 같은 page에 속한 인접 세그먼트를 `chunk_chars`까지 누적 병합(줄 구조는 `\n`로 보존), page가 바뀌면 병합 중단(출처 메타 보존), 단일 초과 세그먼트는 문장/하드 분할.
- `rag_routes.py`의 업로드 핸들러가 `_chunk_text` 루프 대신 `chunk_meta_segments(meta_segments, chunk_chars=500, overlap_chars=50)` 호출하도록 교체. `_chunk_text` 제거.
- 검증: 개조식 불릿 141줄 시뮬레이션 → 141청크에서 **15청크**로 감소, 페이지 메타 보존 확인.
**교훈**: 청킹 구현이 3곳에 분산돼 있다 — `document_ingest/segments.py:chunk_segments`(디렉토리 인제스트용), `app/rag_routes.py`(UI 업로드용·실사용 경로), `knowledge/service.py:_chunk_text`(노트용). "청크가 이상하다"는 보고가 오면 **실제로 어느 경로가 임베딩하는지** 먼저 확인할 것. UI 업로드는 `rag_routes.py`다. 파서가 "단락 = 세그먼트"로 쪼개는 포맷(HWPX/DOCX)은 반드시 세그먼트 병합 청킹을 거쳐야 한다.

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

## E-29: M_17 _prompt_provider 클로저 — effective_prompt 사용으로 기본 상수 강제 주입 (2026-06-08)

**증상**: 사용자가 아무 지침도 편집하지 않은 기본 상태(신규 설치)에서도 doc_query/work_query/note_save 모든 턴의 INPUT에 `[작성 지침] DOC_QUERY_ANSWER_GUIDE...` 가 강제 주입됨. M_16의 "지침 미설정 시 미주입" 계약 위반.

**원인**: `service_context.py`의 `_prompt_provider` 클로저가 `effective_prompt()`를 호출했는데, `effective_prompt()`는 커스텀이 비어있으면 기본 상수(`DOC_QUERY_ANSWER_GUIDE` 등)를 폴백으로 반환한다. 따라서 커스텀 0건 상태에서도 기본 상수가 반환되어 라우팅에서 None 정규화가 안 되고 미주입이 되지 않았다.

**수정**:
1. `_make_prompt_provider(app_config)` 헬퍼 함수를 모듈 레벨로 분리 (테스트 가능하게).
2. 클로저 내에서 `effective_prompt` 대신 raw 커스텀 값(`app_config.agent_prompts.<key>`)만 참조.
3. `strip()` 후 비어있으면 `""` 반환 → 라우팅에서 `None`으로 정규화 → 미주입.
4. `effective_prompt`는 GET `/api/settings/prompts`(표시·기본값 노출)용으로만 유지.

**추가 테스트**: `tests/app/test_service_context_prompt_provider.py` — 커스텀 0건 → 빈 문자열 반환, 기본 상수 미반환, 어댑터 연결 시 미주입 7개 케이스.

**교훈**: "GET용 표시 함수"와 "주입 경로용 값 조회"는 책임이 다르다. `effective_prompt()`는 기본값 폴백을 포함하므로 주입 경로에서 사용하면 "미설정 시 미주입" 계약을 위반한다. 주입 경로 클로저는 반드시 raw 커스텀 값만 참조해야 한다.

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

## E-52: 향후계획(next_steps) date에 'M.DD.' placeholder 반환 → JSON Schema 하드 실패

**날짜**: 2026-06-13  
**증상**: 회의결과보고서(Step 3) 생성 시 `JSON Schema 위반 (max_retries 소진): JSON Schema 위반 at next_steps/2/date: 'M.15.' does not match '^(\d{1,2}\.\d{1,2}\.|)$'` 오류로 생성 전체가 실패.  
**원인**: E-18에서 최상위 `date`의 placeholder 문제는 고쳤으나, `next_steps[].date`는 누락되어 있었다. 프롬프트(`prompts.py`)가 date 예시·설명을 `"M.DD."`로 제시 → LLM이 문자 그대로 베껴 `M.15.`(월 자리 'M' 유지)를 반환. date는 선택 필드인데도 스키마 패턴 위반으로 보고서 생성이 하드 실패했다.  
**수정**:
1. `generator.py`에 `_sanitize_next_step_date()` 추가 — `_normalize_raw_draft`(스키마 검증 전 단계)에서 각 next_step의 date를 정규화. 'M.D.' 형식은 유지, 'YYYY.MM.DD.'는 'M.D.'로 축약, 그 외 형식 불명 값은 빈 문자열로 떨어뜨려 **하드 실패 대신 날짜만 생략**하고 보고서 생성은 진행.
2. `prompts.py`의 date 예시·설명을 `"M.DD."` → `"6.15."`(실제 숫자)로 교정하고 "문자 placeholder 사용 금지" 명시.  
**교훈**: (E-18 재확인) 형식 예시는 항상 실제 값으로 줄 것. 그리고 **선택 필드의 형식 위반은 재시도/하드 실패가 아니라 정규화로 흡수**할 것 — 분량·형식이 조금 어긋난다고 생성 전체를 막지 말고, 살릴 수 있으면 살리고 못 살리면 그 필드만 생략해 사용자가 본문에서 판단하게 둔다.

---

## E-53: gemma4 thinking 모델 — /v1에서 think:false 무시 → 빈 응답/에러/제네릭 노트

**날짜**: 2026-06-15
**증상**: 정전 후 채팅이 "이상해짐" — RAG 책읽기(study) 연출 안 뜨고, 오류 발생, hwpx/스크린샷 첨부 업무노트가 내용 기반이 아닌 제네릭 노트로 저장됨.
**원인(복합)**:
1. (선행) 크래시 전후로 채팅 모델이 한때 `exaone3.5:32b`(tools·vision 미지원)에 물려 AgentError가 났다. 사용자가 `gemma4:latest`로 되돌렸으나,
2. (핵심) `gemma4:latest`는 **thinking 모델**이고, 사용자의 `gemma4:e4b`(비-thinking)가 Ollama에서 사라져 `latest`만 남았다. 백엔드 `NoThinkLLM`은 `extra_body={"think": False}`로 추론을 끄려 했지만, **Ollama의 OpenAI-호환 엔드포인트(/v1/chat/completions)는 `think` 파라미터를 무시**한다(네이티브 /api/chat에서만 동작). 그 결과 gemma4가 항상 추론하며, 추론 토큰이 출력 예산(num_predict)을 잠식해 `content`가 빈 문자열이 됐다. 특히 비전 입력에서 추론이 길어 content='' → AgentError → note_save 강제 폴백이 (이미지 없는) 제네릭 답변을 요약 → 내용 없는 노트.
**진단 방법**: Ollama 직접 호출로 분리 검증 — 네이티브 `/api/chat`+`think:false`는 content 정상(이미지 글자도 정확히 판독), `/v1`+`think:false`는 finish=length·content='', `/v1`+`reasoning_effort:"none"`은 finish=stop·content 정상.
**수정**: `src/agent/no_think_llm.py`에서 `extra_body`에 `reasoning_effort="none"`을 함께 주입(setdefault — 호출자 지정값 존중). /v1에서 실제로 추론이 꺼져 content가 채워짐. 검증(헤드리스 WS): RAG 질의 정상(study 감정+근거 답변), 이미지 턴에서 save_knowledge_note 정상 호출/에러 없음. `gemma4:e4b` stale 참조도 conf.yaml에서 `latest`로 정리.
**교훈**: (1) Ollama OpenAI-호환 엔드포인트는 `think`를 무시한다 — 추론 끄기는 `reasoning_effort:"none"`. (2) thinking 모델은 추론이 출력 예산을 잠식해 빈 응답을 낼 수 있다(E-41과 동일 부류). (3) 모델 능력은 추측 말고 Ollama `/api/show` capabilities로 확인할 것. **남은 과제**: 이미지→노트 강제 폴백이 이미지가 아닌 채팅 답변을 요약하므로, 비전 노트 내용 충실도는 별도 개선 필요(실 스크린샷 검증 후 판단).

---

## E-55: 노트 강제 저장 폴백이 첨부 문서 원문이 아닌 LLM 답변만 요약 → 추측성 노트

**날짜**: 2026-06-15
**증상**: hwpx 등 문서를 첨부하고 "정리해줘" 해도, 노트가 문서 내용 기반이 아니라 두루뭉술한 추측으로 작성됨 (오래전부터).
**원인**: `_force_save_note`(note_save 강제 폴백)가 노트를 `user_text`(짧은 보고문) + `reply_text`(비서 답변)만으로 생성. 주석에 "답변에 첨부 내용이 *이미 반영돼 있다고 가정*"이라 적혀 있었으나, 모델이 "정리했어요"식 제네릭 답변을 내면 노트에 실제 문서 내용이 0건.
**수정**: `_force_save_note`에서 `_extract_attached_doc_ids`로 첨부 doc_id를 찾아 `_fetch_attached_chunks(per_doc_limit=30)`로 원문 청크를 가져와 complete_json 프롬프트에 "추측 말고 이 원문을 근거로" 넣음.
**교훈**: 폴백/요약은 "답변에 내용이 실려 있겠지" 가정 금지 — 1차 소스(문서 청크/이미지)를 직접 근거로 넣을 것.

---

## E-54: 이미지(스크린샷) 노트가 내용 미반영 — gemma4 OCR 불가 + 프롬프트 갭 → 비전 모델 라우팅

**날짜**: 2026-06-15
**증상**: 스크린샷을 붙이고 노트를 요청하면 이미지 글자를 전혀 못 읽고 추측성 노트 작성.
**진단(실데이터)**: 사용자 실제 한글 스크린샷을 Ollama에 직접 넣어 비교 — `gemma4:latest`는 `/api/chat`·`/v1` 모두에서 이미지 텍스트를 못 읽음("어두운 배경/추상 패턴"). 반면 `qwen2.5vl:7b`는 같은 스크린샷을 `/v1`(앱 경로)에서 정확히 판독. 즉 앱의 /v1 전송은 정상이고 **gemma4의 한글 OCR 능력 자체가 부재**가 핵심. (capabilities에 'vision'이 있어도 실제 OCR 품질은 별개.)
**보조 원인**: note_save tool_hint가 "처리한 업무 보고"로만 프레이밍 → 모델이 이미지 글자를 읽기보다 상황을 추측.
**수정**:
1. 이미지 첨부 턴에 "보이는 텍스트·표·수치를 그대로 전사하라(추측 금지)" 판독 지침 주입 (`_augment_with_rag` 진입부).
2. **비전 모델 라우팅**: `OllamaConfig.vision_model`(conf.yaml `app.ollama.vision_model: qwen2.5vl:7b`) 추가. 이미지가 있는 턴은 `GemmaChatAgent.chat`에서 비전 모델로 `_simple_stream`(no-tools) 호출 → 이미지 전사 → note_save 폴백(E-55)이 그 전사 내용으로 노트 저장. 텍스트·도구 대화는 그대로 gemma4.
**교훈**: (1) 모델 vision capability 플래그 ≠ 실제 OCR 품질 — 실데이터로 검증. (2) 합성 이미지(PIL 기본폰트/저화질)는 OCR 검증에 부적합, 사용자 실제 스크린샷으로 확인. (3) 한 모델이 모든 모달리티를 잘하지 못하면 모달리티별 라우팅이 답.

---

## E-57: 의도 분류기(gemma4) 콜드스타트 타임아웃 → 첨부 노트 작성 안 됨

**날짜**: 2026-06-15
**증상**: 첨부 파일/이미지를 붙이고 업무 노트 작성을 요청해도, 답변만 생성되고 노트가 저장되지 않음(아이콘도 note_writing으로 안 바뀜). 그런데 같은 동작이 어떤 때(예: 직전 턴으로 모델이 따뜻할 때)는 정상 작동 — 산발적.
**진단(로그)**: 분류 소요시간 패턴 — 텍스트 질문은 ~1.5s에 분류 성공(doc_query/work_query). 첨부 턴은 **정확히 8s에 타임아웃 → source=fallback_error → intent=chat**. chat이면 note_save가 아니라 강제 저장(force-save)이 안 돌아 노트 미작성. 한 번은 pptx 첨부가 1.8s에 note_save 0.98로 성공 — gemma4가 그때만 warm이었던 것. 즉 **gemma4(로컬) 콜드스타트/모델 경합으로 첫 분류가 8s 기본 타임아웃을 초과**가 근본 원인. (gpt-4o-mini 때는 빨라서 안 드러남.) 추가로 `has_attachment`가 `[첨부 자료:]`만 검사해 **이미지(`[첨부 이미지:]`)는 첨부로 인식조차 안 됨**.
**수정**:
1. `conf.yaml intent_gate.timeout_seconds: 8 → 30` (콜드스타트 여유).
2. 안전망(upstream_adapter): 분류가 `fallback_error`인데 첨부가 있으면 → note_save로 강제 라우팅. 분류 타임아웃에도 노트 유실 방지.
3. `has_attachment`가 `[첨부 이미지:]`도 포함.
**교훈**: (1) 로컬 LLM은 콜드스타트가 길어 외부 API 기준 타임아웃(8s)이 부족하다. (2) 분류기 실패 시 의도가 chat으로 떨어져 부가 동작이 통째로 사라지므로, 핵심 신호(첨부 등)에는 안전망 폴백을 둘 것. (3) 산발적 버그는 타이밍/웜업 의심 — 로그의 소요시간을 보라.

---

## E-58: 회의록 Step 2(개조식 텍스트)가 JSON을 토해냄 — 스타일·출력형식 커플링

**날짜**: 2026-06-15
**증상**: 회의록 탭 Step 2(회의록 작성)가 사람이 읽는 개조식 텍스트([개요]/[주요내용]/[향후계획], ○/-/*) 대신, 내부 JSON 스키마(title/summary_items/...)를 그대로 출력. 회의록 작성 지침을 무시하는 것처럼 보임. (chat·노트 기능과 무관한, 회의록 탭 전용 파이프라인.)
**원인**: `summarize_to_text`(Step 2)가 `complete_text`(텍스트 모드)로 호출하면서도 system 프롬프트로 `base_rules`(= 커스텀 지침 또는 `SYSTEM_PROMPT`)를 깔았는데, 그 안에는 **"출력은 지정된 JSON 구조만 사용"** 규칙이 들어 있었다. 뒤에 "이번엔 텍스트로 출력" 오버라이드를 붙였지만, **스타일 규칙과 JSON 출력 규칙이 한 덩어리로 섞여** 있어, gpt-5 같은 강한 모델은 오버라이드를 따르지만 **gemma4(로컬)는 방대한 JSON 규칙에 휩쓸려 JSON을 출력**. LLM 공급자를 ollama로 바꾸면서 드러난 커플링.
**수정 (OOP — 관심사 분리)**: `prompts.py`에서
- `MEETING_STYLE_RULES` — 작성 스타일(카테고리·위계·글자수·명사형 종결·수치 보존), **출력 형식과 무관**
- `_JSON_OUTPUT_SPEC` — JSON 전용 출력 규칙
- `SYSTEM_PROMPT = MEETING_STYLE_RULES + _JSON_OUTPUT_SPEC` (Step 3, 기존 동일 내용 — complete_json이 JSON 강제)
- `TEXT_OUTPUT_FORMAT` — Step 2 전용 개조식 텍스트 레이아웃

`generator.summarize_to_text`는 이제 `MEETING_STYLE_RULES + TEXT_OUTPUT_FORMAT`만 사용(JSON 지시 일절 없음). gemma4로 실제 검증: 개조식 텍스트 정상 출력.
**교훈**: 프롬프트에서 **"무엇을 쓰는가(스타일)"와 "어떤 형식으로 출력하는가"를 한 덩어리로 묶지 말 것.** 같은 스타일을 텍스트/JSON 두 형식으로 내보낼 땐 형식 지시를 분리해 조합해야, 약한 모델에서도 형식이 흔들리지 않는다. Step 3가 안정적인 이유는 `complete_json`이 API 차원에서 JSON을 강제하기 때문 — 텍스트 출력엔 그런 강제가 없으므로 프롬프트의 명료성이 더 중요하다.

---

## E-59: M_19 그래프 탭이 펫 모드에만 추가 — 데스크톱 모드 누락 + "근거 그래프" 버튼 빈 화면

**날짜**: 2026-07-16
**증상**: M_19(CR-18)에서 추가한 지식그래프 탭이 펫 모드에만 보이고 데스크톱 모드 사이드바에는 없음. 더 나쁘게는, 채팅 답변의 "근거 그래프" 버튼은 공유 컴포넌트(ChatContent)라 데스크톱에서도 노출되는데, 클릭하면 `chatTab`이 `"graph"`로 바뀌어도 DesktopView에 해당 탭 렌더링이 없어 **메인 영역이 빈 화면**이 됨.
**원인**: 펫 모드(`ChatPanel.tsx`의 `TABS`)와 데스크톱 모드(`DesktopView.tsx`의 `SIDEBAR_TABS`)가 탭 목록을 **각자 따로 정의**하고 있어, 새 탭을 한쪽에만 추가하면 두 모드 UI가 조용히 어긋난다. M_19 커밋이 ChatPanel에만 그래프 탭을 추가하고 DesktopView는 손대지 않았다.
**수정**: (1) DesktopView에 그래프 탭 + `GraphRagView` lazy 렌더링 추가. (2) 재발 방지로 탭 목록을 `web/src/chatTabs.ts`의 `CHAT_TABS` 단일 소스로 통합 — 펫/데스크톱 라벨만 `petLabel`/`desktopLabel`로 분리하고 두 컴포넌트가 여기서 파생. 새 탭은 이 파일에만 추가하면 두 모드에 동시 반영된다.
**교훈**: **같은 개념(탭 메뉴)을 두 곳에서 병렬 정의하지 말 것.** 모드별 UI가 갈라져 있으면 기능 추가 시 반드시 양쪽을 모두 확인해야 하고, 사람이든 AI든 한쪽을 빠뜨린다. 공유 컴포넌트가 발행하는 상태 전이(`setChatTab("graph")`)는 모든 모드가 처리할 수 있어야 한다.

---

## E-60: 1글자 엔티티("C")가 모든 그래프 검색에 매칭 — find_entities 역포함 오염

**날짜**: 2026-07-18
**증상**: 대량 인덱싱(495문서 중 126건, 엔티티 2,212개) 후 Neo4j 통합 테스트
test_delete_cascades_and_orphan_cleanup이 실패 — 삭제와 무관한 엔티티 "C"가
find_entities("테스트기관48ccbe") 결과로 반환됨. 실사용에서도 'c'가 포함된 모든
질의에 "C" 엔티티가 매칭되어 하이브리드 검색에 무관 청크가 유입될 수 있는 상태.
**원인**: (1) find_entities의 양방향 CONTAINS(`term CONTAINS e.norm_name`)가 초단문
엔티티명에 대해 사실상 와일드카드로 동작. LLM 추출이 "C" 같은 1글자 엔티티를 만들면
그래프 검색 전체가 오염된다. (2) Neo4j 통합 테스트가 공유 실그래프 위에서 돌면서
"결과가 비어야 한다"를 전역 단정 — 실데이터와 충돌.
**수정**: 역포함 매칭을 이름 3자 이상으로 제한 (`size(e.norm_name) >= 3 AND term
CONTAINS e.norm_name`, FakeGraphStore 계약 동기화). 테스트는 자신이 심은 sfx 포함
엔티티만 검사하도록 격리 보강.
**교훈**: **부분 문자열 양방향 매칭은 짧은 값에 대해 와일드카드가 된다** — 최소 길이
가드를 함께 설계할 것. 그리고 **공유 저장소 위에서 도는 통합 테스트는 "전역 비어있음"을
단정하지 말고 자신이 만든 데이터만 검사할 것** (실사용 데이터가 쌓이면 반드시 충돌한다).

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

---

## E-30: tool 핸들러의 avatar_state.push_emotion이 클라이언트에 도달하지 않음 — 대화 채널 emotion 태그로 우회

**날짜**: 2026-06-09  
**증상**: 업무 노트 저장 시 "작성 중" 캐릭터(note_writing 스프라이트)를 띄우려고 `ToolRouter._handle_save_knowledge_note`에서 `avatar_state.push_emotion("note_writing")`을 호출했으나, raw WS 캡처로 확인 시 해당 avatar-state 프레임이 **클라이언트에 전혀 도달하지 않음**(neutral·audio 메시지는 정상 도달). `_send_text=SET`이고 send_json 호출도 예외 없이 완료되는데도 수신 안 됨.  
**원인(정황)**: 회의록의 "writing" 캐릭터는 사실 `avatar_state.push_emotion`이 아니라 프론트 스토어 플래그 `isMeetingGenerating`(CharacterWidget.tsx)으로 구동된다. 즉 tool 실행 컨텍스트에서의 avatar_state WS push는 신뢰성 있게 클라이언트에 닿지 않는다(대화 처리 task와 별개 송신 경로/타이밍 문제로 추정). 반면 대화 채널로 가는 audio 메시지(display_text)는 항상 도달한다.  
**수정**: 노트 작성 중/완료 캐릭터 전환을 **대화 채널(audio display_text)의 emotion 태그**로 구동. 어댑터가 시작 안내 메시지에 `[note_writing]`, 완료 메시지에 `[neutral]` 태그를 붙여 보내면, 프론트 `stripEmotionTags`가 이를 파싱해 `setEmotion` + 태그 제거한다(이미 존재하는 메커니즘 재사용). avatar_state.push는 무해한 belt-and-suspenders로 유지.  
**검증**: raw WS 캡처로 display_text에 `[note_writing]`/`[neutral]` 태그가 그대로 전달됨을 확인, node로 stripEmotionTags가 해당 태그를 emotion으로 파싱하고 본문은 깨끗하게 남김을 확인. note_writing은 EMOTION_MAP·Emotion 타입(프론트/백엔드)·스프라이트 파일 모두 추가.  
**교훈**: **캐릭터 감정/상태 전환을 클라이언트에 확실히 전달하려면 tool 핸들러의 avatar_state.push가 아니라 대화 채널(메시지 emotion 태그) 또는 프론트 스토어 플래그를 쓸 것.** avatar_state.push는 tool 실행 컨텍스트에서 신뢰성이 낮다(회의록도 실제로는 isMeetingGenerating 플래그로 동작).

---

## E-31: 채팅으로 생성한 업무 노트가 노트 목록에 즉시 안 보임 (모드 전환 시에만 표시)

**날짜**: 2026-06-09  
**증상**: 채팅 비서가 save_knowledge_note로 노트를 생성하면 지식 그래프에는 반영되는데 노트 목록(NotesView)에는 안 보임. 펫↔데스크톱 모드를 전환하면 컴포넌트가 remount되며 그제서야 목록에 나타남.  
**원인**: (1) NotesView가 목록을 마운트 시 1회만 `fetchNotes()`하고, 채팅으로 노트가 생성돼도 refetch 트리거가 없었다. `invalidateNotesCache()`는 채팅 인용 칩용 websocket 캐시만 비울 뿐 NotesView state와 무관. (2) 도구 완료 신호 `tool_call_status`(언더스코어)가 클라이언트에 도달하는데, 프론트 타입·switch는 `tool-call-status`(하이픈)로 정의돼 있어 **매치되지 않는 dead code**였다(타입 정의가 백엔드 실제 전송값과 불일치). (3) 완료 메시지에 `[[note:slug]]` 마커가 항상 포함되는 것도 아니라(LLM이 생략 가능) 마커 기반 갱신도 불안정.  
**수정**: store에 `notesRevision` 카운터 추가 → NotesView가 구독해 `useEffect([refreshList, notesRevision])`로 자동 refetch. 노트 저장 신호에서 bump: 주력은 `tool_call_status`(타입을 실제값 언더스코어로 정정) + name==="save_knowledge_note"일 때 bump(항상 도달), 보조로 `attachNoteCitationsToMessage`(노트 마커 존재 시)에서도 bump.  
**검증**: raw WS 캡처로 `tool_call_status`(name=save_knowledge_note, status=completed)가 실제로 클라이언트에 도달함을 확인(완료 메시지엔 [[note:]] 마커 없었음 → 마커 경로만으론 실패했을 것). 타입 정정 후 tsc 빌드 통과.  
**교훈**: **WS 메시지 타입 문자열은 백엔드 실제 전송값과 1:1로 맞는지 raw 캡처로 확인할 것.** 하이픈/언더스코어 한 글자 차이로 case가 영영 dead code가 된다. 그리고 **목록형 뷰는 생성/변경 신호에 반응하는 갱신 트리거(store revision 등)를 둘 것** — 마운트 시 1회 fetch만으론 외부 생성(채팅 등)을 반영 못 한다.

---

## E-61: 그래프 검색→문서 "열어보기"가 동작 안 함 — 확장자 없는 제목을 임시파일명으로 사용

**날짜**: 2026-07-21
**증상**: 그래프 탭에서 검색 후 문서를 선택하면 나오는 버튼("다운로드")을 눌러도
원본이 열리지 않음. 문서 탭(DocumentsView)의 동일 버튼은 정상.
**원인**: 백엔드 다운로드는 정상(실 doc_id로 전부 200 확인). 프론트 문제였다.
Electron `shell:openDocument` 핸들러(`frontend/src/main/index.ts`)가 임시 사본 파일명을
**호출자가 넘긴 filename 힌트**로 정했는데, GraphRagView는 `openDocument(selected.id,
selected.label)`로 **확장자 없는 과제 제목**(CR-30 추출 title)을 넘긴다(DocumentsView는
실제 파일명 `doc.filename`을 넘겨 정상). 그 결과 임시파일이 확장자 없이 저장돼
`shell.openPath`가 연결 앱을 못 찾아 조용히 실패. 백엔드는 정확한 파일명을
`Content-Disposition: filename*=utf-8''...`(예: `….hwpx`)로 이미 보내고 있었으나
핸들러가 이를 무시했다.
**수정**: 핸들러가 **서버 Content-Disposition 파일명을 우선** 사용하도록 변경
(RFC 5987 `filename*=utf-8''<pct>` 파싱 + `filename="…"` 폴백 + 호출자 힌트 폴백 +
`'document'`). 이러면 호출자가 무엇을 넘기든 정확한 확장자가 붙어 어느 화면에서든
열린다. 더불어 사용자 피드백대로 그래프 버튼 라벨을 "다운로드"→"열어보기"(FileText
아이콘)로 변경 — 실제 동작이 임시 사본 열기이고, 저장은 각 앱의 "다른 이름으로 저장"에
맡긴다.
**검증**: 실 Neo4j 검색 결과 doc_id로 다운로드 엔드포인트 전부 200 + Content-Disposition
헤더 확인(한글 filename* 인코딩). 파서가 실 헤더에서 `….hwpx` 확장자 정확 추출(node 재현).
web/dist·frontend(electron-vite) 빌드 통과. 앱 재시작 후 실제 열기 동작은 사용자 확인 필요.
**교훈**: (1) **다운로드/열기 임시파일명은 호출자 힌트가 아니라 서버가 준 파일명
(Content-Disposition)을 신뢰**하라 — 화면마다 넘기는 값(파일명 vs 제목)이 달라 한쪽만
깨진다. (2) 확장자 없는 임시파일은 `shell.openPath`가 조용히 실패한다(에러 안 띄움).
(3) 버튼 라벨은 실제 동작과 일치시킬 것("다운로드"라 쓰고 "열기"를 하면 사용자 혼란).

---

## E-62: 앱이 매 실행 pet 모드로 떠서 창 모드 사용자가 매번 토글 + 그래프 검색 드롭다운이 그래프 가림

**날짜**: 2026-07-21
**증상**: (1) 그래프 탭 검색 시 결과 드롭다운이 그래프 중앙을 가림. (2) 앱 재시작
때마다 pet 모드(투명 전체화면)로 떴다가 창 모드로 전환됨 — 터미널에 `[PetMode]
enable → state file not found → [PetMode] disable`. 창 모드로 쓰는 사용자는 매 실행
수동 토글 필요. (터미널 "renderer config is missing"은 렌더러가 별도 web/dist라
electron-vite에 renderer 설정이 없어서 나는 설계상 무해한 경고 — 오류 아님.)
**원인**: (1) 검색 드롭다운(`position:absolute; right:0`)이 컨트롤 바 중앙-우측의
검색 입력 기준으로 열려 그래프를 덮음. (2) `App.tsx`가 시작 시 `window.petMode.enable()`을
**무조건** 호출(제품 기본 = 데스크톱 펫), 그런데 windowMode를 **영속화하지 않아** 창
모드 선택이 기억되지 않음. disable을 자동 호출하는 코드는 없고(유일 호출=CharacterWidget
토글), 터미널의 disable은 사용자가 매번 창 모드로 토글한 흔적.
**수정**: (1) GraphRagView 검색 래퍼에 flex `order:99` — 컨트롤 바 최우측으로 보내
드롭다운이 우측 끝에서 열리게(DOM 이동 없이 안전). (2) windowMode를 localStorage
(`saessagi_window_mode`)에 영속화(store `setWindowMode`), 시작 시 `loadWindowMode()`로
복원 — 저장이 "window"면 `disable()`(=창 모드 설정, **이미 검증된 토글 경로 재사용**),
없거나 pet이면 `enable()`(제품 기본 유지). 위험한 window-manager.ts는 미변경.
**검증**: web tsc·빌드 통과. 실제 동작(펫 플래시 제거·모드 기억)은 앱 재시작 후 확인
필요 — 최초 1회 창 모드 토글이 저장된 다음 실행부터 창 모드로 바로 뜬다.
**교훈**: (1) **모드/뷰 상태는 영속화해 마지막 선택을 기억**하라 — 매 실행 기본값
강제는 반대 모드 사용자를 괴롭힌다. (2) pet 모드 startup은 window-manager.ts 대신
**렌더러의 enable/disable(검증된 경로) 선택**으로 안전하게 제어 가능(E-21 위험 회피).
(3) electron-vite "renderer config is missing"은 web/dist 분리 구조의 정상 경고.

---

## E-63: WSLg에서 문서 "열어보기"가 안 됨 — xdg-open/.hwpx 핸들러 부재

**날짜**: 2026-07-21
**증상**: 그래프/문서 탭에서 원본 "열어보기"를 눌러도 아무 일도 안 일어남. 다운로드
엔드포인트는 200 정상(원본 존재).
**원인**: E-61에서 파일명(확장자) 문제로 봤으나 그건 부차적이었다. **진짜 원인은
실행 환경(WSLg)에 `xdg-open`도 `wslview`도 없고 `.hwpx` MIME 연결도 없다는 것** —
`shell.openPath(dest)`가 열 수단이 없어 실패한다(한글은 Windows 앱). `command -v`로
확인 시 `/mnt/c/WINDOWS/explorer.exe`만 존재.
**수정**: main 프로세스에서 WSL 감지(`WSL_DISTRO_NAME` 또는 `/proc/version`의
'microsoft') 후, 임시 사본 경로를 `wslpath -w`로 Windows 경로 변환해 `explorer.exe`로
기본 앱(한글 등)에서 연다. 비-WSL은 기존 `shell.openPath` 유지. (E-61의 서버
Content-Disposition 파일명 우선도 유지 — 확장자 정확성 보장.)
**검증**: 다운로드 200 + Content-Disposition 확인, WSL 감지·explorer.exe 폴백 빌드 확인.
실제 한글 실행은 사용자 GUI 확인 필요.
**교훈**: **`shell.openPath` 실패는 조용하다(에러 안 띄움).** 실행 환경의 파일 연결
가능 여부(xdg-open/mime/interop)를 먼저 확인할 것 — WSLg는 리눅스 앱이 아니라 Windows
interop(explorer.exe)로 열어야 한다.

---

## E-64: 딥 리서치 — 레퍼런스 문서 중복 + 좁은 폭 + 표/마크다운 스타일 부재

**날짜**: 2026-07-21
**증상**: (1) 참고 자료가 같은 문서인데 [1]/[21]/[23]처럼 2~3번 중복 표시. (2) 결과
패널이 오른쪽 끝까지 안 늘어나고(좁음) 우측 여백 큼. (3) 표가 테두리 없이 깨져 보이고
전반적 가독성 저하.
**원인**: (1) `DeepResearchService._merge_hits`가 chunk_id 기준으로 모아, 같은 문서의
여러 청크가 각각 참고자료 번호를 차지. (2) `DeepResearchView`의 컨테이너 `maxWidth:920`
(desktop)이 폭을 과하게 제한. (3) 마크다운 본문 클래스 `.md-body`에 **CSS 규칙이 전혀
없어** 표·제목·목록이 브라우저 기본값으로 렌더(표 테두리 없음).
**수정**: (1) `_rank_sources`를 doc_id별 최고 score 청크 1개로 중복 제거 → 참고자료·인용
번호가 문서 단위. (2) `maxWidth` 920→1400. (3) `index.css`에 `.md-body` 규칙 추가
(제목·문단·목록·코드·인용·표 테두리/헤더/줄무늬), 넓은 표는 `.md-table-wrap`(overflow-x)
컨테이너로 감싸 본문 레이아웃 보호(ReactMarkdown `components.table`).
**검증**: deep_research 단위 13건 통과(중복 제거 신규 테스트 포함 — 같은 문서 2청크→1건,
최고 score 대표). web tsc·빌드 통과. 실제 표 렌더는 사용자 GUI 확인 필요.
**교훈**: **참고자료/인용은 청크가 아니라 문서 단위로 집계**할 것(RAG는 한 문서를 여러
청크로 쪼갬). 마크다운 렌더 컨테이너에는 표·목록 CSS를 반드시 갖출 것(GFM 파싱만으론
테두리가 안 생긴다).

---

## E-65: 런처의 `uv run`이 실행 직전 melotts를 제거해 신규 설치 환경이 매번 깨짐

**날짜**: 2026-07-29
**증상**: bootstrap 직후에는 TTS·대화가 정상인데, `새싹이.sh`(또는 `start.sh`)로 기동하면
melotts가 사라져 TTS 초기화가 실패하고 E-54 경로를 그대로 타 LLM 대화까지 전멸.
**원인**: 두 런처가 `uv run --project "$ROOT" uvicorn ...`으로 백엔드를 띄운다. `uv run`은
실행 전 프로젝트 환경을 `uv.lock`에 맞춰 동기화하며, **락파일에 없는 패키지를 제거한다.**
melotts와 truststore는 pypinyin 버전 충돌 때문에 의도적으로 `pyproject.toml`에서 빠져
있고 `bootstrap.py`가 `uv pip install`로 따로 넣는다. 그래서 매 실행마다
`melotts==0.1.2` 제거 + `pypinyin 0.50.0 → 0.55.0` 다운그레이드가 반복됐다
(`uv sync --dry-run`으로 재현: "Would uninstall 3 packages").
**수정**: 두 런처의 `uv run`에 `--no-sync` 추가 (`start.sh`, `새싹이.sh`).
**검증**: `uv run --no-sync --project . python -c "import melo, pypinyin"` → melo 정상 import,
pypinyin 0.50.0 유지. 백엔드 기동 후 WebSocket E2E로 응답 텍스트 + 오디오 423KB 수신 확인.
**교훈**: **`uv pip install`로 넣은 락파일 외 패키지는 `uv run`이 지운다.** 락파일 밖 의존성이
하나라도 있으면 실행 경로는 반드시 `--no-sync`(또는 venv 인터프리터 직접 호출)여야 한다.
"bootstrap은 됐는데 실행만 하면 깨진다"는 증상을 보면 이 조합을 먼저 의심할 것.

---

## E-66: 지침 관리 textarea가 whiteSpace:pre 때문에 긴 줄이 화면 밖으로 잘림 (CR-44, 2026-07-30)

**날짜**: 2026-07-30
**증상**: 설정 → 지침 관리에서 페르소나 등 긴 지침을 열면 문장이 중간에 뚝 끊겨 보임
(예: "먼저 한두 문장으로 핵심 요지를 말하고 → 소제목(## 또는 ###)으로 나눈 본" 에서 잘리고
"문..." 이후가 안 보임). 스크롤바도 없어서 잘린 게 아니라 내용이 없는 것처럼 보였다.
사용자 보고: "UI가 꽉 차지 않고 중간에 짤려있다."
**원인**: `SettingsView.tsx`의 지침 편집 textarea에 `whiteSpace: "pre"`가 걸려 있었다.
`pre`는 줄바꿈을 전혀 하지 않으므로 모노스페이스 폰트로 긴 줄을 입력하면 textarea 폭을
넘어서고, 이 렌더링 경로(브라우저·컨테이너 조합)에서는 가로 스크롤바도 뜨지 않아
내용이 그냥 시각적으로 잘려나갔다.
**수정**: `whiteSpace: "pre"` → `"pre-wrap"` + `wordBreak: "break-word"`. 들여쓰기·개행은
그대로 보존하면서 긴 줄만 textarea 폭 안에서 자동 줄바꿈되게 했다. 같이 진행한 지침 관리
UI 개편(CR-44, 목록→상세 방식)과 설정 컨텐츠 폭 확장(860→1040)도 가독성에 기여했지만
근본 원인은 이 한 줄이었다.
**검증**: 헤드리스 브라우저로 실측 — 수정 전 `scrollWidth`가 `clientWidth`를 초과(가로
오버플로), 수정 후 `scrollWidth === clientWidth`(924===924)로 오버플로 없음 확인.
**교훈**: **textarea에 `whiteSpace: "pre"`를 쓰기 전에 가로 스크롤 UX를 반드시 검증할 것.**
"들여쓰기를 보존해야 한다"는 이유만으로 `pre`를 쓰면 코드 편집기가 아닌 일반 설정
화면에서는 긴 줄이 잘려 보이는 사고로 이어진다. 들여쓰기 보존 + 자동 줄바꿈이 필요하면
`pre-wrap`이 정답이다.

---

## E-67: flex column 안의 카드들이 개수가 많아지면 스크롤 대신 찌그러짐 (CR-45, 2026-07-30)

**날짜**: 2026-07-30
**증상**: 문서 탭 → 청크 보기에서 청크가 적을 때는 내용이 보이는데, 많아지면(1,000개)
청크 내용 영역이 "완전히 찌부"되어 아무것도 읽을 수 없다. 스크롤바도 생기지 않는다.
**원인**: 청크 목록 컨테이너가 `display:flex; flex-direction:column`이고 `flex:1`로 높이가
확정된 상태였다. 자식 카드들은 **`flex-shrink` 기본값이 1**이므로, 내용 총합이 컨테이너
높이를 넘으면 flexbox가 먼저 자식들을 min-content까지 압축한다. 카드에 `overflow:hidden`이
있어 압축된 만큼 내용이 잘려 사라지고, 압축 결과 컨테이너 안에 다 들어맞으므로
`overflowY:auto`도 스크롤을 만들지 않는다. 청크가 적을 때는 압축이 필요 없어 정상으로 보여
개수 의존적인 버그처럼 나타났다.
**수정**: (1) 목록 행에 `flexShrink: 0` 명시 — 개수와 무관하게 높이를 유지하고 넘치면 실제로
스크롤되게 했다. (2) 근본적으로는 화면을 목록→내용 2단으로 분리해(CR-45) 한 화면에
본문을 N개 쌓지 않도록 구조를 바꿨다.
**검증**: 1,392청크(표시 1,000개) 문서에서 목록 앞 5행 높이 36~37px 유지, 가로/세로 넘침 0.
수정 전에는 동일 문서에서 카드 높이가 사실상 0에 가까웠다.
**교훈**: **세로 flex 컨테이너에 목록을 담을 때는 자식에 `flexShrink: 0`을 명시할 것.**
`overflowY:auto`만 걸어두면 "스크롤될 것"이라고 착각하기 쉽지만, flexbox는 스크롤보다
축소를 먼저 적용한다. 특히 항목 수가 데이터에 따라 변하는 목록에서는 **적은 개수로 테스트하면
버그가 드러나지 않는다** — 실제 최대 규모(수백~수천 건)로 확인해야 한다.

---

## E-68: 양방향 폴더 동기화가 "신규"와 "반대편에서 삭제됨"을 구분 못 해 지운 폴더가 부활 (CR-46, 2026-07-30)

**날짜**: 2026-07-30
**증상**: RAG 폴더에서 지운 폴더(`f`, `완결보고서`)가 30초 뒤 계속 되살아난다. MobaXterm에서
지우면 UI에 남아 있고, UI에서도 지우면 다시 생긴다. 사용자는 "지우는 순서라도 있나?"라고
물었다 — **어떤 순서로 지워도 부활한다.** 파일은 정상적으로 지워지는데 폴더만 그랬다.
**원인**: CR-41의 동기화 규칙이 `disk - app`이면 앱에 만들고 `app - disk`면 디스크에 만드는
**생성 전용** 로직이었다. 그런데 그 조건은
(a) 방금 새로 생긴 폴더 (b) 반대쪽에서 사용자가 지운 폴더 두 경우에 **똑같이 성립**한다.
현재 상태만 보고는 둘을 구분할 방법이 원리적으로 없다. 로그가 핑퐁을 그대로 보여줬다:
```
앱 폴더 생성 'f' (디스크에서 발견)
디스크 폴더 생성 'f' (UI에서 만든 폴더)
앱 폴더 생성 'f' (디스크에서 발견)
```
**수정**: 상태 파일에 `folders`(= 직전에 양쪽 모두에 있다고 확인된 집합, `known`)를 기억해
"이전에 있었는데 한쪽에서 사라졌다"를 삭제로 판정한다. `known` 갱신에서 **두 번 틀렸고 둘 다
실제로 재현됐다**:
1. 주기 끝에서 디스크·앱을 **새로 읽어** 교집합으로 덮었다 → 대량 재임베딩으로 주기가 길어진
   동안 사용자가 UI에서 한 삭제까지 "이미 동기화됨"으로 흡수해 기억을 지우고, 다음 주기에
   신규로 오인해 부활시켰다. → **이번 주기에 실제로 만든 것/지운 것으로만 갱신**하도록 고쳤다.
   지우려 했지만 보존한 경우(파일 남음·ignore 정책·삭제 실패)는 `known`에 유지해야 한다.
2. `disk ∩ app`을 `known`으로 **학습하는 경로가 없었다** → 이미 양쪽에 있는 폴더는 어떤
   분기에도 걸리지 않아 영원히 등록되지 못하고, 그 뒤 한쪽에서 지우면 신규로 오인됐다.
   업그레이드 직후·상태 파일 유실 후가 정확히 이 상황이다.
**검증**: 회귀 테스트 `test_f6`(학습 경로)·`test_f7`(주기 중 삭제 흡수 금지)를 만들고
**예전 구현으로 되돌려 실패하는 것을 확인**했다. f7은 실서버와 같은 로그
`앱 폴더 생성 '완결보고서' (디스크에서 발견)`을 재현한다. 실서버에서 UI 삭제·디스크 삭제
양방향 각각 4주기 동안 부활 없음.
**교훈**: **양방향 동기화에서 "한쪽에만 있다"는 사실만으로는 생성과 삭제를 구분할 수 없다.**
반드시 "직전에 양쪽에 있었다"는 상태를 남겨야 하며, 그 상태는
(1) **관측으로 학습되는 경로**가 있어야 유실 후 자기 치유가 되고,
(2) **주기 끝에 현재 상태를 다시 읽어 덮으면 안 된다** — 그 사이 사용자가 한 변경을
흡수해 버린다. 실제로 처리한 작업의 결과로만 갱신할 것.
동기화 루프의 검증은 **한 주기가 오래 걸리는 상황(대량 처리 중)**까지 포함해야 한다.

---

## E-69: 파일을 다른 폴더로 옮기면 삭제로 오판해 unindex 후 재임베딩 (CR-46, 2026-07-30)

**날짜**: 2026-07-30
**증상**: RAG 폴더 안에서 파일을 다른 폴더로 옮겼는데, 색인에서 지워진 뒤 다음 주기에 새
문서로 다시 임베딩됐다. 270건 중 **44건**이 이렇게 처리됐다. CR-41이 "이동은 재임베딩하지
않는다"를 목표로 내용 해시를 키로 쓴 것인데 그 목적이 무력화됐다.
**원인**: 이동 직후 한 주기 동안 그 파일은 **어느 쪽에도 보이지 않는다**.
· 상태에 기록된 옛 경로는 이미 없다 (`_recorded_path_exists` 실패)
· 새 경로는 첫 관측이라 "안정화 대기"로 해시를 계산하지 않아 `live_digests`에도 없다
그 순간 삭제로 확정되어 `delete_policy=unindex`가 색인을 지웠고, 다음 주기에 새 경로가
안정화되면 상태에 기록이 없으므로 신규 파일로 인제스트됐다.
**수정**: 삭제 확정에 유예 주기(`delete_grace_cycles=2`)를 도입했다. 연속 2주기 이상 사라진
상태가 유지될 때만 삭제로 확정하고, 그 사이 다시 보이면 카운터를 리셋한다. 이동은 1주기 안에
해소되므로 오판되지 않는다.
**검증**: `test_p9d_reappearing_file_resets_grace`(이동 → 삭제 확정 안 됨 → 다음 주기에
`to_move`로 해소). 기존 삭제 테스트 6건도 2주기 유예에 맞춰 갱신했고
`test_p9c_still_detects_real_deletion`으로 실제 삭제는 여전히 잡히는지 확인했다.
**교훈**: **"안 보인다"는 관측 하나로 파괴적 동작(삭제)을 실행하지 말 것.** 특히 안정화 대기
같은 지연 판정이 있는 파이프라인에서는 "옛 위치에 없다"와 "새 위치가 아직 확정 안 됨"이
겹치는 **관측 사각지대**가 생긴다. 삭제는 연속 관측으로 확정하고, 되돌릴 수 없는 동작일수록
유예를 둘 것. E-69는 이미 같은 계열의 사고(E-69 이전, 재시작 시 전체 삭제 위험)를 겪고
`_recorded_path_exists`를 넣었던 코드에서 재발했다 — 사각지대가 하나 더 있었다.

---

## E-70: `update_doc_category` 반환값을 확인하지 않아 상태와 색인이 어긋난 유령 항목 225건 (CR-46, 2026-07-30)

**날짜**: 2026-07-30
**증상**: 감시자가 특정 파일들을 영구히 건너뛴다. 상태 파일은 418건이 색인됐다고 기록하는데
실제 색인에는 193건뿐이었다 — **유령 225건**. 사용자 입장에서는 폴더에 넣은 문서가 검색에
전혀 걸리지 않는데 로그에는 "폴더 이동 완료"만 남아 원인이 보이지 않았다.
**원인**: `_apply_moves`가 `store.update_doc_category(doc_id, folder_id)`를 호출하고
**반환된 갱신 행 수를 확인하지 않았다.** 그 문서가 이미 색인에서 사라진 경우(E-69로 unindex된
경우가 대부분) 0행이 갱신되는데도 성공으로 간주해 로그를 남기고 상태 기록을 유지했다.
상태가 "이미 색인됨"이라 주장하므로 이후 모든 주기가 그 파일을 건너뛴다 — **조용히, 영구히.**
**수정**: 반환값이 0이면 `state.forget(digest)`로 기록을 지우고 경고를 남긴다. 다음 주기에
신규로 인식되어 재인제스트된다.
**검증**: `_GhostStore`(0행 반환)로 `test_g1_move_of_missing_doc_clears_state_for_reingest`.
실서버 복구 후 상태 doc_id와 색인 doc_id를 실제로 대조해 유령 0건·미추적 0건 확인
(색인 418 = 상태 418).
**교훈**: **"몇 건이 바뀌었는지" 반환하는 API는 반드시 그 값을 확인할 것.** 예외가 없다는
것은 성공을 의미하지 않는다(0행 갱신은 정상 반환이다). 특히 **캐시·상태 파일이 외부 저장소를
"믿는" 구조**에서는 확인 누락 한 곳이 조용히 영구적인 데이터 누락으로 이어진다.
그리고 **상태 파일과 실제 저장소의 정합성을 검사하는 수단**(doc_id 대조)을 처음부터 갖춰야
한다 — 이 사고는 정합성 대조를 해보기 전까지 존재 자체를 알 수 없었다.

---

## E-71: pytest가 실서비스 데이터에 써서 "지운 폴더가 되살아난 것"처럼 보임 (CR-46, 2026-07-30)

**날짜**: 2026-07-30
**증상**: E-68을 고치고 양방향 삭제를 실서버에서 검증해 통과한 뒤, 얼마 지나 **폴더 `f`가
다시 UI에 나타났다.** 그런데 이번 부활에는 설명이 붙지 않았다 — HTTP 요청 기록도 없고
(`POST /api/rag/folders` 없음), 감시자 로그에도 `앱 폴더 생성 'f'`가 없고, 그 주기의 요약은
`인제스트 0 / 이동 0`이었다. 앱 버그로 보이는 상황이었다.
**원인**: 앱이 만든 게 아니었다. **내가 돌린 `pytest tests/`가 실서비스
`data/rag_folders.json`에 폴더를 추가했다.** `data/rag_originals/<folder_id>` 디렉토리 생성
시각이 전체 테스트 실행(228초)이 끝난 시각과 정확히 일치해 특정할 수 있었고,
`test_d4_guard_allows_normal_delete`가 실제 파일을 만들지만 폴더 헬퍼를
monkeypatch하지 않아 `_folder_id_for` → `_create_app_folder`가 **실제** `_save_folders`를
호출한 것이 원인이었다. 테스트가 쓰는 폴더 이름이 하필 `f`여서, 사용자가 지웠던 폴더가
되살아난 것과 구별되지 않았다. 과거 테스트 실행이 남긴 고아 디렉토리가 6개 있었다.
**수정**:
1. **세션 격리** — `conftest.py`에 autouse(scope=session) 픽스처를 추가해
   `rag_routes._FOLDERS_FILE`·`_ORIGINALS_DIR`을 tmp 경로로 돌린다. 개별 테스트를 고치는
   방식은 다음에 추가되는 테스트에서 또 새므로 세션 전체를 막았다. 전체 1147건 실행 전후로
   실 파일 md5와 디렉토리 목록이 변하지 않는 것을 확인했다.
2. **폴더 생성 시 항상 로그** — `_create_app_folder(name, reason=...)`. 예전에는 조용히
   만들었기 때문에 "누가 되살렸는지"를 로그로 추적할 수 없어 원인 파악이 훨씬 오래 걸렸다.
3. **같은 주기 부활 방지(방어)** — 인제스트·이동은 폴더 처리보다 나중에 돌지만 대상 목록은
   삭제 전에 세운 계획이다. 이번 주기에 삭제한 폴더 이름을 넘겨 그 폴더의 파일은 건너뛴다.
   (일관된 계획에서는 도달하기 어려운 경로라 불변식 테스트로만 검증한다 — 실제 사고 원인은
   아니었고, 그 점을 테스트 docstring에 명시했다.)
**검증**: 격리 후 `pytest tests/rag_watch/`·전체 1147건 모두 실 데이터 무변화.
디스크 폴더 삭제를 다시 수행하고 6주기(4분) 동안 부활 없음.
최종 정합: 디스크 = 앱 = known = {2020완결보고서, 2021완결보고서, 2022완결보고서, RFP}.
**교훈**: **운영 중인 시스템과 같은 작업 디렉토리에서 테스트를 돌리면 테스트가 운영 데이터를
바꾼다.** 이 프로젝트는 `data/`를 상대 경로로 쓰므로 특히 위험하고, `_delete_app_folder`는
폴더 안 청크를 전부 지우므로 **실 문서가 삭제될 수도 있었다.** 데이터 경로 격리는 편의가
아니라 안전 장치다.
그리고 **"증상이 앱 버그처럼 보인다"는 것과 "앱이 원인이다"는 다르다.** 로그·HTTP 기록에
근거가 전혀 없을 때는 앱 밖(내가 돌린 명령, 다른 프로세스)을 의심해야 한다.
디렉토리 생성 시각(mtime)을 내가 실행한 명령의 타임라인과 대조한 것이 결정적이었다.
**부수 교훈**: 상태를 바꾸는 동작에 로그가 없으면 사후 추적이 불가능하다. 사용자에게 보이는
상태(폴더 목록)를 바꾸는 코드는 사유와 함께 반드시 로그를 남길 것.

---

## E-72: 감시 상태를 주기 끝에만 저장해 재시작 중 인제스트한 문서가 중복 색인 (CR-46, 2026-07-30)

**날짜**: 2026-07-30
**증상**: 백엔드를 재시작한 뒤 특정 문서가 색인에 **두 번** 들어갔다(디스크에는 1개).
로그가 정확히 보여준다: `20:46:40 인제스트 완료` → `20:47:08 상태 로드`(재시작) →
`20:48:00 인제스트 완료`(같은 파일, 다른 doc_id).
**원인**: `run_once`가 상태 파일을 **주기 끝에 한 번만** 저장했다. 인제스트는 청크를 즉시
벡터 스토어에 쓰지만 기록은 메모리에만 있으므로, 그 사이 프로세스가 죽으면
**"청크는 색인에 있는데 기록은 없는"** 상태가 된다. 다음 기동에서 그 파일은 신규로 보여
다시 임베딩된다. 주기당 최대 `max_per_cycle`(설정값 20)건까지 중복될 수 있다.
재시작·OOM·노드 재부팅 모두 같은 결과다.
**수정**: 인제스트 1건이 성공할 때마다 즉시 `state.save()`. 저장은 원자적 rename이고 파일도
작아서(수백 KB) PDF 파싱·임베딩 비용에 비하면 무시할 수준이다.
**검증**: `test_i3_state_persisted_per_file_survives_crash` — 인제스트 직후 주기가 끝나기
전에 예외로 `run_once`를 중단시키고(= 크래시), 같은 상태 파일로 새 서비스를 기동해 기록이
남아 있는지 확인한다. **수정을 되돌리면 실패**하는 것을 확인했다(`상태 로드 파일 0건`).
실서버에서 이 원인으로 생긴 중복 2건을 제거했다.
**교훈**: **외부 저장소에 이미 쓴 작업의 기록을 메모리에만 두지 말 것.** "작업 수행"과
"수행했음을 기록"이 원자적이지 않으면 그 틈에서 죽을 때 중복(또는 누락)이 생긴다.
배치 루프에서 기록을 배치 끝에 모아 저장하는 것은 성능상 자연스러워 보이지만, 배치 안의
각 작업이 이미 되돌릴 수 없다면 **작업 단위로 기록을 내려써야** 한다.
E-70(유령 항목 = 기록은 있고 색인은 없음)과 정확히 반대 방향의 불일치이며, 원인은 같다 —
색인과 기록이 따로 움직이는데 그 사이의 원자성을 보장하지 않았다.

---

## E-73: STT가 통째로 동작 안 함 — ctranslate2가 CUDA 12 cuBLAS를 못 찾음 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "지금 stt 기능은 작동을 안 하나봐?". 마이크로 말해도 아무 일도
일어나지 않는다. 화면에는 오류 표시가 없다.
**원인**: `/asr`가 **13ms 만에** `{"error": "Internal server error during transcription"}`을
돌려줬다 — 모델이 도는 시간이 아니다. 로그에 진짜 원인이 있었다:
`Library libcublas.so.12 is not found or cannot be loaded`.
conf.yaml이 `device: auto`라 B200 GPU를 고르는데, ctranslate2 4.x는 **CUDA 12용**
cuBLAS를 dlopen한다. 그런데 이 환경의 venv에는 torch가 끌어온 **CUDA 13** 라이브러리
(`nvidia/cu13`)만 있어 `libcublas.so.12`가 없었다. cuDNN 9는 이미 있었으므로 빠진 것은
cuBLAS 하나뿐이었다.
로그를 보니 사용자가 실제로 시도한 `voice.wav`(4.24초)도 같은 이유로 실패해 있었다 —
**프론트엔드는 정상이었고 백엔드 전사만 죽어 있었다.**
**수정**: `nvidia-cublas-cu12`를 의존성에 추가(`sys_platform == 'linux'`). 설치만으로
LD_LIBRARY_PATH 조작 없이 GPU 전사가 동작한다. `pyproject.toml` + `uv.lock` 모두 갱신했고,
잠금 파일에는 이 패키지와 전이 의존성 1개만 추가됐다(기존 버전 변동 없음).
설치는 `uv pip install`로 했다 — `uv sync`/`uv run`은 락에 없는 melotts를 지운다(E-65).
**검증**: `/asr` 실호출 `{"text":" 다음 영상에서 만나요."}` 0.06~0.25초.
CPU(3.0초) 대비 약 50배. 브라우저 가짜 마이크(`--use-file-for-fake-audio-capture`)로
녹음→업로드→응답 전 경로 확인.
**교훈**: **"아무 일도 안 일어난다"는 증상에서 응답 시간을 먼저 보라.** 13ms는 모델이
돌지 않았다는 뜻이고, 그것만으로 원인 범위가 "모델·성능"에서 "초기화·의존성"으로 좁혀진다.
그리고 **GPU 스택은 CUDA 메이저 버전이 갈린다** — torch가 CUDA 13을 끌어와도
ctranslate2 같은 다른 라이브러리는 CUDA 12를 요구할 수 있다. "GPU 패키지가 있으니 될 것"이라
가정하지 말고 실제로 dlopen되는 so 이름을 확인할 것.

---

## E-74: 무음일 때 Whisper가 지어낸 문장이 대화로 전송됨 (2026-07-31)

**날짜**: 2026-07-31
**증상**: E-73을 고친 뒤 확인 중, **완전한 무음 WAV(RMS 0.000000)**를 `/asr`에 넣었더니
`{"text":" 다음 영상에서 만나요."}`가 돌아왔다.
**원인**: Whisper의 알려진 환각이다. 학습 데이터(유튜브 자막)에 흔한 상투구라 들린 게
없으면 이런 문장을 지어낸다. 문제는 우리 음성 입력이 인식 결과를 **곧바로 대화로 전송**한다는
점이다(`onText` → `addMessage` + `send`). 즉 마이크를 켰다가 말하지 않고 끄면
"다음 영상에서 만나요."가 사용자 발화로 LLM에 전달된다. 무음 2초면 자동 종료되므로
실수로 누르기만 해도 발생한다.
**수정**: 두 겹으로 막았다.
1. 보내기 전에 녹음된 PCM의 실효값(RMS)을 재서 `0.004` 미만이면 **전송하지 않고**
   "소리가 들리지 않았어요"로 안내한다(불필요한 왕복도 줄인다).
2. 그래도 돌아온 결과가 알려진 상투구면 대화에 넣지 않는다(`isHallucination`).
**검증**: 단위 4건 — 실측된 환각 문구 차단, 자막 상투구 차단, **실제 업무 발화는 통과**
("내일 오후 2시 팀 회의 잡아줘", "다음 영상에서 만나요 라고 자막을 넣어줘" 등 과차단 방지).
브라우저 가짜 마이크 E2E에서 합성음 → `/asr` 200 → 환각 반환 → **대화에 들어가지 않음** 확인.
**교훈**: **음성 인식 결과를 사람 입력과 동일하게 신뢰하지 말 것.** 특히 결과를 자동으로
전송·실행하는 경로에서는 "인식할 것이 있었는가"를 먼저 확인해야 한다. 무음·잡음에 대한
생성 모델의 출력은 빈 문자열이 아니라 **그럴듯한 거짓**이다.
필터는 반드시 정상 발화를 막지 않는지 함께 검증할 것 — 과차단은 기능을 무의미하게 만든다.

---

## E-75: 첫 대화가 저장되지 않고, 답변이 다른 히스토리로 갈라짐 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "왜 처음 시작할 때 인삿말이 나온걸 히스토리에 넣어버리지?"
대화 목록에 **인사말만 든 대화**가 남아 있었다.
**원인**: 히스토리 파일을 직접 열어 보니 질문과 답변이 **서로 다른 파일**에 있었다.
```
05-46-45  [human] '안녕'
05-48-01  [ai]    '안녕하세요! 무엇을 도와드릴까요? 😊'
```
(참고: 시작 인사말 자체는 히스토리와 무관하다. `showStartupGreeting`은 채팅 메시지를
만들지 않고 히어로 문구·TTS로만 전달한다 — 목록에 보인 것은 "안녕"에 대한 LLM 답변이었다.)

원인이 둘 겹쳐 있었다.
1. **새 세션은 히스토리가 없어 아무것도 저장되지 않는다.** `store_message`는
   `history_uid`가 비어 있으면 경고만 남기고 반환하는데, `ServiceContext.history_uid`
   초기값이 `""`다. 프론트는 "메시지가 있을 때만" 새 히스토리를 만들었으므로, 접속 후
   첫 대화는 저장되지 않다가 나중에 히스토리가 생긴 시점부터 저장이 시작됐다.
2. **답변 생성 중에 히스토리를 바꿀 수 있었다.** 사이드바의 "새 대화"는 `messages.length > 0`
   이면 무조건 `create-new-history`를 보냈다. 답변을 기다리는 동안(모바일에서 1분 이상)
   이걸 누르면 뒤늦게 도착한 답변이 **새 히스토리**에 저장되어 질문과 답이 갈라진다.
   모바일에서는 서랍의 "새 대화"가 채팅 화면으로 돌아가는 통로이기도 해서 쉽게 눌린다.
**수정**:
1. WebSocket 연결 직후, 이어보는 대화가 없고 메시지도 없으면 `create-new-history`를 보내
   **세션이 쓸 히스토리를 미리 확보**한다. 빈 히스토리는 백엔드 목록 조회가 자동 정리하므로
   쌓이지 않는다(실측: 정리 후 빈 히스토리 0건).
2. `startNewHistoryIfSafe()` 한 곳으로 규칙을 모으고, **생성 중(`aiStatus !== "idle"`)에는
   히스토리를 바꾸지 않는다.** 호출부 3곳(사이드바 탭·펫 탭·↺ 버튼)이 같은 규칙을 쓴다.
**검증**: 단위 4건(대기 중 생성 / 생성 중 무시 / 말하는 중 무시 / 빈 대화 무시).
브라우저 E2E로 ws 프레임을 가로채 확인 — 생성 중 "새 대화"·↺ 모두 `create-new-history`
미전송, 응답 완료 후에는 정상 전송. 새 세션에서 대화 후 히스토리 파일이 `[HA]`
(질문+답변 한 파일)로 저장되는 것 확인 — 수정 전에는 파일 자체가 생기지 않았다.
**교훈**: **"저장되지 않는다"와 "잘못 저장된다"는 같은 증상으로 보인다.** 목록에 이상한
항목이 보였을 때 UI만 보지 말고 **저장된 원본 파일을 직접 열어 볼 것** — 질문과 답이 다른
파일에 있다는 사실이 원인 두 개를 한 번에 드러냈다.
그리고 **오래 걸리는 비동기 작업 중에 그 결과가 저장될 위치를 바꾸는 조작**은 막아야 한다.

---

## E-76: `height: 100vh` 때문에 모바일 크롬에서 상단 두 줄이 잘림 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 같은 페이지가 **네이버 브라우저에서는 정상**인데 **모바일 크롬에서는 타이틀 바와
상태줄이 통째로 사라져** 햄버거 메뉴·새 대화 버튼에 접근할 수 없었다.
**원인**: `App.tsx`가 전체를 `<div style={{ width: "100vw", height: "100vh" }}>`로 감싸고
있었다. 모바일에서 `100vh`는 **주소창이 숨겨진 큰 뷰포트**라 실제 보이는 높이보다 크다.
앞서 `html/body`를 `100%`→`100dvh`→`100svh`로 고쳤지만 **이 인라인 `100vh`가 그것을 전부
무시하고 있었다** — 그래서 두 번이나 "고쳤는데 그대로"인 상태가 반복됐다.
브라우저마다 툴바 높이가 달라 크롬에서만 증상이 드러났다.
**수정**:
1. `App.tsx`의 `100vw/100vh` → `100%/100%`. `#root`가 이미 올바른 높이이므로 채우기만 하면
   된다. (`100vw`도 스크롤바 폭을 세어 가로 넘침을 만든다.)
2. 근본적으로 **CSS 뷰포트 단위를 믿지 않는다.** `useAppHeight`가 `visualViewport`로
   지금 보이는 높이를 직접 재서 `--app-height`에 넣고, `html/body`가 그 값을 쓴다.
   툴바가 접히든 키보드가 올라오든 항상 사실을 따라간다(키보드가 떠도 입력창이 가려지지
   않는 부수 효과). 변수가 없는 초기 페인트에는 기존 `svh/100%` 규칙이 그대로 쓰인다.
**검증**: `innerHeight=874`인데 `visualViewport.height=774`인 상황을 강제로 만들어
(모바일 크롬 조건 재현) 확인 — 수정 전 앱이 `0~874`로 **100px 넘침**, 수정 후 `0~774`로
보이는 영역에 정확히 일치. 햄버거 클릭 가능, 입력바가 툴바에 가리지 않음.
데스크탑(1440×900)·모바일(402×774, 390×664) 모두 앱 높이 = 보이는 높이.
**교훈**: **"고쳤는데 그대로"면 내 수정이 실제로 적용되는 자리인지 먼저 의심할 것.**
인라인 스타일 한 줄이 CSS 파일 전체를 무력화하고 있었다. 같은 속성을 여러 층에서 정하고
있지 않은지 계산된 스타일(`getComputedStyle`)로 확인하는 것이 추측보다 빠르다.
그리고 **모바일 뷰포트는 단위로 맞추지 말고 재서 맞출 것** — `vh/dvh/svh`는 브라우저마다
해석이 다르지만 `visualViewport`는 실제 값이다.

---

## E-77: 문서 임베딩 중 캐릭터가 사라짐 — 업로드 영상이 403/404 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "문서 임베딩할 때 새싹이가 사라졌다가 다시보여. 문서 임베딩을 상징하는
동영상이 실행되기로 했었는데."
**원인**: 두 가지가 겹쳤다.
1. `/avatars`는 `assets/character/saessagi/`를 서빙하는데 그 디렉토리에 **`uploading.png`도
   `uploading.webm`도 없었다.** 두 파일은 `web/public/avatars/`에만 있었다(프론트 정적 자산).
   `/avatars` 마운트가 SPA 정적 서빙보다 우선이라 `/avatars/uploading.png`는 404가 됐다.
2. upstream `AvatarStaticFiles`가 이미지 확장자만 통과시키고 나머지를 **403**으로 막는다.
   그래서 파일을 옮겨도 `.webm`은 여전히 막혔다.
결과적으로 업로드 중 `emotion="uploading"`이 되면 `<video>`가 아무것도 그리지 못했고,
`<img>`에만 있던 폴백(`onError` → neutral)은 영상 경로에 없어서 **캐릭터가 통째로 사라진
것처럼** 보였다. 업로드가 끝나 neutral로 돌아오면 다시 나타났다 — 제보 그대로다.
**수정**:
1. `uploading.png`·`uploading.webm`을 `assets/character/saessagi/`로 복사.
2. `_SaessagiAvatarFiles`(서브클래스)로 `.webm`·`.mp4` 허용. upstream 파일은 건드리지 않았다.
3. `<video>`에도 `onError` 폴백 추가 — 영상을 못 읽으면 같은 감정의 정지 그림으로 물러선다.
   자산이 또 없어지더라도 캐릭터가 사라지는 일은 없다.
**검증**: `/avatars/uploading.webm` 403 → **200(4.4MB)**, `uploading.png` 404 → **200**.
실 브라우저에서 문서를 업로드해 아바타 전이를 추적:
`neutral.png → uploading.webm(재생됨) → uploading.png → neutral.png`,
**한 번도 안 보이거나 빈 상태가 되지 않음**, 자산 요청 실패 0건.
**교훈**: **같은 경로를 두 곳에서 서빙하면 우선순위가 조용히 결과를 바꾼다.** 프론트 자산과
백엔드 마운트가 같은 URL 접두사를 쓰면, 빌드에 있는 파일이 404가 날 수 있다.
그리고 **폴백은 모든 렌더 경로에 걸어야 한다** — `<img>`에만 있고 `<video>`에는 없었던 탓에
자산 하나가 빠진 것이 "캐릭터가 사라진다"는 큰 증상으로 나타났다.

---

## E-78: FastAPI `root_path`에 정체불명 문자열이 섞여 들어가 아바타가 전부 404 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "야 왜 새싹이가 안보여?" — 화면 오른쪽 아래의 떠 있는 캐릭터도,
인사말 위의 캐릭터 그림도 **둘 다 빈 자리**였다. 앱의 다른 부분은 정상 동작했다.
**원인**: `src/app/server.py`의 FastAPI 생성자에
`root_path="/sQRXE2adVt"` 라는 정체불명의 문자열이 들어가 있었다.
`root_path`가 설정되면 Starlette가 마운트 하위 경로를 계산할 때 그만큼을 기준으로 삼는데,
실제 요청 경로에는 그 접두사가 없으므로 계산이 한 칸씩 어긋난다.
그 결과 StaticFiles가 `neutral.png` 대신 **`avatars/neutral.png`**를 찾게 되어
(마운트 디렉토리 안에 `avatars/` 하위 폴더는 없으므로) 전부 404가 됐다.
`/assets/*.js`(프론트엔드 마운트)는 우연히 영향이 덜해 앱 자체는 떠서, 캐릭터만 안 보였다.

**이 줄은 내가 올린 커밋 `b9b1a90`(CR-50 모델 정렬)에 섞여 들어갔다.** 그 커밋은
`web/` 쪽만 건드려야 했는데 `git add -A`로 스테이징하면서 검토 없이 함께 커밋됐다.

**진단이 오래 걸린 이유와 배운 것**: 마운트는 살아 있었고(`.txt` 요청은 403 "Forbidden file
type"을 정상 반환), 디렉토리 경로도 맞았고, `lookup_path("neutral.png")`도 성공했다.
겉으로 보이는 모든 것이 정상이라 원인이 코드 밖(파일 권한·심볼릭 링크·프로세스 환경)에
있다고 여러 번 잘못 짚었다. 결국 **`get_response`에 실제로 넘어오는 인자를 찍어 보고서야**
`path='avatars/neutral.png'`, `root_path='/sQRXE2adVt'`가 드러났다.
→ 정적 파일이 "있는데 404"면, 경로 문자열을 추측하지 말고 **핸들러가 받는 실제 인자를
출력**해 볼 것. 그 한 줄이 앞선 여러 번의 추측보다 빨랐다.

**수정**: `root_path` 인자 제거.
**검증**: `neutral.png`·`thinking.png`·`study.png`·`uploading.png`·`uploading.webm` 모두
404 → **200**(각 0.5~4.4MB). 실 브라우저에서 떠 있는 캐릭터와 인사말 캐릭터 둘 다
`naturalWidth=1024`로 실제 렌더 확인, 아바타 요청 실패 0건.
**교훈**: **`git add -A`로 커밋하지 말 것.** 의도한 파일만 스테이징하거나, 최소한 커밋 전에
`git diff --staged`로 무엇이 들어가는지 확인해야 한다. 이번엔 한 줄이 서비스의 눈에 띄는
기능을 통째로 망가뜨렸고, 커밋 메시지에는 그 변경이 언급조차 되지 않아 추적을 더 어렵게 했다.

---

## E-79: "정리해줘"가 붙은 새 질문을 후속 질문으로 오인해 RAG를 통째로 건너뜀 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "RAG를 안 하고 답하거나". 화면에는 사내 문서 근거 없이 모델의 일반
지식으로 쓴 표가 나왔다.
**원인**: CR-23의 후속 질문 감지(`looks_like_followup`)가
"60자 이하 + 재표현 표현(요약/정리/짧게/표로…) 포함"이면 무조건 후속으로 판정했다.
그래서 **"기후변화 대응방안을 정리해줘"처럼 명백히 새 주제인 질문까지** 후속으로 잡혀
`followup_decision()`(inject_rag=False)로 라우팅됐고, 분류와 RAG 재검색을 모두 건너뛰었다.
로그가 그대로 보여준다:
```
13:52:18 IntentGate: doc_query inject_rag=True → GraphRAG 검색 hits=5 → RAG 주입   (정상)
13:55:26 IntentGate 후속 질문 감지 — 분류·RAG 재검색 생략   ('기후변화 대응방안을 정리해줘')
```
같은 주제를 살짝 다르게 물어본 것뿐인데 두 번째부터는 근거 없는 답이 나왔다.
**수정**: 재표현 요청은 **자체 주제어가 없을 때만** 후속으로 본다.
`residual_topic()`이 재표현 표현·군더더기(그럼/좀/자세히/해줘…)·조사만 남은 토막을 걷어내고,
남는 주제어가 3자 미만일 때만 후속으로 판정한다.
`"짧게 정리해줘"` → 잔여 `""` → 후속 / `"기후변화 대응방안을 정리해줘"` → 잔여
`"기후변화대응방안을"` → 새 질문.
**검증**: 단위 3건 신규 + 기존 52건 통과. 과교정 방지 검사를 함께 넣었다 —
"좀 더 자세히 정리해줘", "표로 정리해서 보여줘", "그럼 내용을 간단히 한 문장으로 요약해줘"는
여전히 후속으로 남아야 한다(안 그러면 재표현 요청마다 무관한 검색이 돌아 답이 딴 데로 샌다).
실서버에서 동일 질의 재현: `doc_query inject_rag=True → GraphRAG 검색 hits=5 →
RAG 컨텍스트 주입(인용 마커 4)`.
**교훈**: **휴리스틱은 "무엇을 잡는가"보다 "무엇을 잘못 잡는가"로 검증해야 한다.**
이 규칙은 "그럼 요약해줘"를 잡으려고 만들었는데, 실제로는 "정리해줘"가 든 모든 짧은 질문을
잡았다. 단어의 존재가 아니라 **문장에 자체 주제가 있는지**가 진짜 기준이었다.

---

## E-80: RAG 검색에 시한이 없어 턴이 무한정 매달림 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "무한 자료검색". 화면에 "자료를 찾아볼게요!"만 뜬 채 답이 오지 않는다.
**원인**: 로그상 `13:56:34 IntentGate: doc_query` 이후 **8분간 아무 기록도 없다가** 재시작으로
끝났다. `_augment_with_rag`의 검색 호출(`hybrid_retrieve` / 벡터 검색)에 **시한이 전혀 없어**,
GPU가 대형 모델 적재로 바쁘거나(사용자가 128B 모델로 바꿔가며 시험 중이었다) Neo4j가 멎으면
그대로 영원히 기다린다. 정상 검색은 실측 1~12초다.
**수정**: `asyncio.wait_for(..., _RAG_SEARCH_TIMEOUT_SEC=60)`으로 감쌌다. 시한을 넘기면
경고를 남기고 **검색 없이 답변을 계속한다** — 근거 없는 답이라도, 답이 아예 없는 것보다 낫다.
**검증**: 단위 2건(시한이 존재하고 합리적 범위인지, 멈춘 작업을 실제로 포기하는지).
실서버에서 같은 질의가 1초 만에 검색을 마치고 정상 응답.
**교훈**: **사용자를 기다리게 하는 모든 외부 호출에는 시한이 있어야 한다.** 특히 진행 상태를
먼저 알린 뒤("자료를 찾아볼게요!") 매달리면, 사용자에게는 고장이 아니라 "무한 작업"으로 보여
더 오래 기다리게 만든다. 실패를 빨리 드러내는 편이 친절하다.

---

## E-81: 모델 전환이 conf.yaml 한 곳만 갱신 + 정규식이 OpenAI 설정까지 덮어씀 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 모델을 바꿔가며 시험하는데 `app.ollama.model`은 `mistral-medium-3.5:128b`인데
`character_config.agent_config.llm_configs.ollama_llm.model`은 `gemma4:26b`로 남아 있었다.
어느 모델이 실제로 쓰이는지 알 수 없어 시험 결과를 신뢰할 수 없다.
CLAUDE.md 사고 2가 "두 곳을 같이 봐야 한다"고 경고한 바로 그 지점이다.
**원인**: 모델을 바꾸는 경로가 둘인데 **둘 다 문제**였다.
1. `set_llm_provider`(설정 화면의 "LLM 적용")는 `app.ollama.model`만 갱신하고
   `llm_configs.ollama_llm.model`은 건드리지 않았다 → 어긋남 발생.
2. `set_model`은 정규식 `([ \t]+model:\s*)...`으로 `model:` 줄을 **일괄 치환**했다.
   실측 결과 conf.yaml에서 이 패턴에 걸리는 줄이 4개인데 **그중 2개가 OpenAI 설정**
   (`app.openai.model`, `llm_configs.openai_llm.model`)이다. 즉 Ollama 모델을 바꾸면
   OpenAI 모델 설정이 조용히 덮어써진다.
**수정**: 공용 헬퍼 `_set_ollama_model_keys(raw, model)`을 만들어 **키 경로를 지정해
두 곳만** 바꾸도록 하고, 두 엔드포인트가 모두 이것을 쓰게 했다. 정규식 치환은 제거했다.
**검증**: 단위 4건(두 곳 동시 갱신 / OpenAI·vision 불변 / 형제 설정 보존 / 빈 설정에서 생성).
실서버에서 설정 화면과 동일한 요청을 보내 확인 —
`app.ollama.model`과 `llm_configs.ollama_llm.model`이 함께 바뀌고 `openai` 두 값은 그대로.
**교훈**: **설정 파일을 정규식으로 고치지 말 것.** YAML은 구조가 있으니 파싱해서 키 경로로
접근해야 한다. "들여쓰기 + 키 이름"으로 잡으면 같은 이름의 다른 설정까지 함께 바뀌는데,
그 피해는 조용히 일어나고 한참 뒤에야 드러난다.
그리고 **같은 값을 두 곳에 저장하는 구조라면 쓰는 지점을 한 함수로 모을 것** — 경로가
둘로 갈리면 한쪽만 고치는 사고가 반드시 생긴다.

---

## E-82: 모바일에서 입력하려 하면 화면이 확대되고 본문이 위로 밀려 흰 화면이 됨 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "모바일 환경에서 프롬프트를 입력하려고 하면 흰 화면이 되고, 기존의 것은
위로 확 올라가버린다. 뭔가 줌인이 되는 것도 같다."
**원인**: 서로 다른 두 가지가 겹쳤다.
1. **자동 확대** — iOS Safari는 글꼴이 **16px 미만**인 입력에 포커스하면 화면을 확대한다.
   입력창 글꼴이 `--fs-14`(=14px)였다. 확대되면 레이아웃 기준이 어긋나 사용자가 되돌리기도
   어렵다.
2. **본문이 위로 밀림** — 키보드가 올라오면 iOS는 보이는 영역(visual viewport)을 레이아웃
   안에서 아래로 밀어낸다(`visualViewport.offsetTop > 0`). 앱은 레이아웃 기준으로 고정돼
   있어 그만큼 화면 위로 사라지고, 아래에는 아무것도 없는 빈 영역이 남는다 — 사용자에게는
   "본문이 확 올라가고 흰 화면"으로 보인다.
**수정**:
1. `@media (pointer: coarse)`에서 `input/textarea/select` 글꼴 하한을 16px로 올렸다
   (`max(16px, var(--fs-14))`). 데스크톱은 설계값 그대로다. 인라인 style을 이겨야 해서
   `!important`가 필요하다.
2. viewport meta에 `interactive-widget=resizes-content` — 키보드가 화면을 밀지 말고
   레이아웃 크기를 줄이게 한다.
3. `useAppHeight`가 `visualViewport.offsetTop`을 `--app-offset`으로 내보내고 `#root`가
   그만큼 따라 내려가게 했다. 브라우저가 그래도 밀어낼 때의 안전망이다.
**검증**: 키보드 상황을 재현(보이는 높이 774→480, offsetTop 0→60)해 세 상태 모두 확인 —
키보드 없음: 앱 0~774 / 올라옴: 앱 top=60 h=480, 입력바 bottom=518 **보이는 영역 안** /
내려감: 원상 복귀. 입력창 글꼴 모바일 16px·데스크톱 14px.
**교훈**: **모바일 입력 글꼴은 16px가 하한이다.** 디자인상 작게 쓰고 싶어도, 그보다 작으면
iOS가 확대해 버려 레이아웃이 통째로 어긋난다. 그리고 **키보드는 화면을 가리는 것이 아니라
보이는 영역 자체를 옮긴다** — 높이만 맞추면 부족하고 `offsetTop`까지 따라가야 한다.

---

## E-83: 키보드가 올라와도 resize 이벤트가 안 와 입력바가 가려짐 (인앱 브라우저, 2026-07-31)

**날짜**: 2026-07-31
**증상**: E-82를 고친 뒤에도 사용자 제보 "여전한데?" — 카카오톡 인앱 브라우저에서 입력창을
누르면 화면 대부분이 흰 여백이 되고 **입력바가 보이지 않는다.** 그 흰 부분을 손가락으로
건드려 내리면 그제야 정상 배치(캐릭터 + 입력바가 키보드 바로 위)가 된다.
**원인**: 화면 세 장을 비교하니 **헤더 위치는 세 장 모두 같은데 입력바만 화면 밖**이었다.
즉 앱 높이가 키보드에 맞춰 줄지 않은 것이고, 계산식이 아니라 **호출 시점**이 문제였다.
`useAppHeight`는 `visualViewport`의 `resize`/`scroll`에만 반응하는데, 이 인앱 브라우저는
키보드가 올라올 때 그 이벤트를 제때 보내지 않는다. 사용자가 화면을 건드리자 비로소
이벤트가 발생해 정상 배치가 됐다 — 계산은 처음부터 맞았다.
**수정**: 이벤트만 믿지 않는다. `focusin`/`focusout`(=키보드가 열리고 닫히는 순간)에
**0.1초 간격으로 1.2초 동안 다시 잰다.** 키보드 애니메이션이 끝난 최종 높이를 반드시
잡게 되고, 1.2초 뒤 멈춰 배터리를 낭비하지 않는다.
**검증**: "키보드가 올라와 보이는 높이는 줄지만 **이벤트는 발생하지 않는** 상황"을 재현.
- 수정 전: `--app-height`가 774px에 멈추고 입력바 bottom=752 → **키보드에 가림**
- 수정 후: 0.4초 만에 430px로 갱신, 입력바 bottom=408 → 키보드 위에 보임
이벤트가 정상인 브라우저(E-82 시나리오)도 회귀 없음.
**교훈**: **이벤트가 온다는 보장은 없다.** 특히 인앱 브라우저(WKWebView 계열)는 표준
이벤트를 빠뜨리는 경우가 흔하다. 사용자가 화면을 건드려야 고쳐진다면, 그건 로직이 아니라
"언제 실행되는가"의 문제다 — 상태가 바뀔 것이 확실한 시점(포커스 등)에 스스로 다시 재는
경로를 함께 둘 것.

---

## E-84: iOS가 문서를 강제 스크롤해 입력바만 화면 맨 위에 남음 (2026-07-31)

**날짜**: 2026-07-31
**증상**: E-82·E-83을 고친 뒤에도 사용자 화면녹화 제보 — 모바일 크롬에서 입력창을 누르면
**입력바가 화면 맨 위(주소창 바로 아래)로 올라가고 그 아래는 전부 흰색**이 된다.
글자를 입력해도 그 상태가 유지된다.
**원인**: 녹화 프레임을 보니 헤더·캐릭터가 사라지고 **입력바만 최상단**에 있었다.
앱은 한 화면에 고정된 레이아웃(맨 아래가 입력바)이므로, 이 그림은 **문서가 앱 바닥까지
스크롤된 상태**라는 뜻이다. iOS는 포커스된 입력을 보이게 하려고 문서를 스크롤하는데,
`overflow: hidden`만으로는 이걸 막지 못한다(iOS의 오래된 예외 동작).
그래서 앱 높이 보정(E-82/E-83)이 제대로 돌아도 화면은 이미 밀려 있었다.
**수정**: 두 겹.
1. `body`에 `position: fixed; top/left/right: 0` — iOS에서 문서 스크롤을 실제로 막는
   표준 방법이다. 스크롤할 것 자체가 없어진다.
2. 높이를 다시 잴 때 `window.scrollY !== 0`이면 `scrollTo(0, 0)`으로 되돌린다(안전망).
**검증**: 문서를 700px 강제 스크롤해도 `scrollY`가 0으로 유지되고 입력바·헤더가 제자리.
키보드 시나리오(이벤트 정상 / 이벤트 없음) 모두 회귀 없음.
**내부 스크롤은 살아 있는지 함께 확인** — `position: fixed`가 본문 스크롤까지 막으면
설정·문서 목록을 볼 수 없게 되므로, 설정 화면에서 내부 컨테이너 `scrollTop 0 → 200` 확인.
데스크톱 영향 없음(글꼴 14px, `--app-offset` 0).
**교훈**: **`overflow: hidden`은 iOS에서 문서 스크롤을 막아 주지 않는다.** 한 화면 고정
레이아웃이라면 `body`를 `position: fixed`로 잠가야 한다.
그리고 **증상이 "요소가 화면 맨 위에 있다"면 그 요소가 올라간 게 아니라 화면이 내려간
것일 수 있다** — 무엇이 사라졌는지(헤더·본문)를 함께 보면 방향이 드러난다.

---

## E-85: 추가 질문에서 문서를 참조하지 않음 — 라벨로 검색 여부를 하드코딩 (2026-07-31)

**날짜**: 2026-07-31
**증상**: 사용자 제보 "왜 추가질문을 하면 참조를 안해?"
첫 질문("기후변화 대응방안에 관해 정리해줘")은 참고 자료 3건을 달고 답했는데,
이어서 물은 "구체적으로 농업분야에서 대응할 수 있는 방안은 뭐야?"는 **참고 자료가 비어 있고**
문서 근거 없이 답했다.
**원인**: CR-51에서 `followup` 라벨을 도입하면서 **라우팅이 라벨로 검색 여부를 도출**했다 —
`followup이면 무조건 inject_rag=False`. 로그가 그대로다:
```
20:12 IntentGate: intent=doc_query inject_rag=True  → RAG hits=5, 인용 마커=3
20:14 IntentGate: intent=followup  inject_rag=False → 문서 없음
```
"구체적으로 농업분야에서는?"은 직전 대화에 이어지지만 **직전 답변에 없던 새 정보**를 묻는
심화 질문이다. 재표현 요청("짧게 정리해줘")과 같은 취급을 받으니 근거를 못 찾았다.
**수정**: 검색 필요 여부를 **분류기가 직접 판단**하게 했다 (사용자 지시: "하드코딩을 해두었다면
전면 철폐하고 의도분류기를 고도화해").
- `IntentResult.needs_search`(bool)를 추가하고 JSON 스키마의 필수 필드로 넣었다.
- 프롬프트에 기준을 명시 — "직전 대화에 이어지는 질문이라도, 직전 답변에 없던 내용을
  물으면 true". 애매하면 검색 쪽으로 기울이라고 했다(검색 한 번 더 하는 손해 < 근거 없는 답).
- 라우팅은 `followup`이어도 `needs_search=True`면 RAG를 유지한다.
- 모델이 필드를 빠뜨리면 라벨 기반 기본값(doc_query/work_query만 true)으로 채운다.
**검증**: 실제 `gemma4:e4b`로 6/6 —
심화 질문 3종("구체적으로 농업분야에서는?", "그 중 예산이 가장 큰 과제는?",
"아까 말한 저탄소 농업 기술 더 자세히") → `needs_search=True`,
순수 재표현 3종("짧게 정리해줘", "표로 만들어줘", "번역해줘") → `False`.
실서버 재현: 문제의 질문이 `doc_query inject_rag=True` → **RAG 컨텍스트 주입 hits=5,
인용 마커 5개**(수정 전 0개).
**교훈**: **분류 결과에서 후속 동작을 코드가 "도출"하면, 라벨 하나가 여러 상황을 뭉뚱그린
순간 그 도출이 전부 틀린다.** 라벨은 "무엇인가"만 말하게 하고, "그래서 무엇이 필요한가"는
따로 물어보는 편이 안전하다 — 문맥을 아는 것은 모델이지 라우팅 코드가 아니다.

---

## E-87: 임베딩 중 딥 리서치를 돌리면 GPU 고갈로 백엔드가 죽음 (2026-08-02)

**날짜**: 2026-08-02
**증상**: 사용자 제보 "딥 리서치를 하다가 서버가 꺼진 것 같은데 문서 임베딩 중에 리서치를
하라고 해서 그런가?" — 정확한 관찰이었다.
**원인**: E-86에서 보존하기 시작한 `backend.prev-*.log`에 처음으로 증거가 남았다.
사망 로그 10개 중 9개는 감시자가 재시작하며 생긴 **정상 종료**였고,
하드 킬은 딱 하나(`backend.prev-0802-142941.log`)였다. 그 마지막 20초:
```
15:18:50  rag_watch: 인제스트 20 / … / 다음주기 1703      ← 임베딩 대량 진행 중
15:19:02  rerank 추론 실패 — cuDNN Frontend error         ← GPU 압박
15:19:09  GraphRAG 검색 (query='dsRNA 바이러스…')          ← 딥 리서치 하위 질의
15:19:14  rerank 추론 실패 — cuDNN Frontend error
15:19:14  GraphRAG 검색 (query='항바이러스…')  ← 여기서 로그 끝, 프로세스 사망
```
`cuDNN Frontend error: No valid execution plans built`는 전체 로그를 통틀어 4건인데
**그중 2건이 사망 직전 12초에 몰려 있다.** GPU 자원 고갈의 전형적 신호다.

셋이 같은 GPU를 두고 다퉜다: 딥 리서치가 올린 128B 모델(80GB, `keep_alive` 1시간) +
RAG 인제스트의 임베딩(대기열 1703건) + 리랭커.
**수정 판단**: 배경 작업이 비켜야 한다. 인제스트는 급할 것이 없고 실패해도 다음 주기에
다시 하면 되지만, 대화·딥 리서치는 사용자가 화면 앞에서 기다리는 작업이다.
GPU를 나눠 쓰면 죽지 않더라도 응답이 느려진다 — 의도 분류가 3~6초에서 16초로 늘어난
사례가 이미 있었다.
- `rag_watch/activity.py` — "지금 사용자를 기다리게 하는가" 표시등(스레드 안전 카운터).
  대화가 겹칠 수 있으므로 마지막 하나가 끝나야 재개한다.
- 대화(`chat`)와 딥 리서치(`run`)가 도는 동안 표시를 켠다.
- 감시자는 표시가 켜져 있으면 주기를 통째로 건너뛰고, **배치 도중이라도 문서 하나 단위로
  확인해 즉시 양보**한다(20건을 다 돌 때까지 GPU를 물고 있지 않도록).
**검증**: 단위 7건(표시등 중첩·예외 복구·스레드 안전, 감시자 skip/정상 동작).
실서버 — 대화 구간(15:34:41~15:35:36) **인제스트 0건**, 대화 종료 후 **20건 재개**.
**교훈**: **배경 작업과 대화형 작업이 같은 자원을 쓰면 반드시 우선순위를 정해야 한다.**
"둘 다 돌아가니 괜찮겠지"는 자원이 빠듯해지는 순간 가장 나쁜 방식으로 드러난다 —
느려지는 정도가 아니라 프로세스가 통째로 사라졌다.
그리고 **E-86의 로그 보존이 없었으면 이번에도 원인을 못 잡았다.** 증거를 남기는 수정이
때로는 기능 수정보다 값지다.

---

## E-88: 리랭커 cuDNN 실행계획 실패 → 다음 호출에서 프로세스 segfault (2026-08-02)

**증상**: 딥 리서치를 돌리면 백엔드가 흔적 없이 사라진다. 며칠에 걸쳐 7회 발생.
파이썬 로그에는 shutdown 기록도 예외도 없고, 그냥 다음 줄이 새 프로세스의 기동 로그다.
E-87에서 "GPU 고갈"로 추정하고 임베딩 양보 조치를 넣었지만 **그 뒤에도 계속 죽었다.**

**진짜 원인**: `dmesg`에 유일한 단서가 있었다.

```
python[2441995]: segfault at 228 ip ... error 4 in libtorch_cuda.so[...]
```

앱 로그에서 죽기 직전에 반복되던 경고와 짝을 맞추면 그림이 완성된다:

```
rerank 추론 실패 — 벡터 순서 유지: cuDNN Frontend error:
    [cudnn_frontend] Error: No valid execution plans built.
```

**torch 2.11+cu130 / cuDNN 9.19 / B200(sm_100)** 조합에서 cross-encoder를 FP16으로 돌리면
cuDNN attention 백엔드가 실행 계획을 만들지 못한다. 이 예외는 `rerank()`가 잡아 벡터 순서로
물러서므로 **검색은 겉보기에 멀쩡히 동작한다.** 문제는 그 다음이다 — 계획 수립에 실패한
뒤의 **두 번째 호출에서 libtorch_cuda 안에서 segfault가 나고 프로세스가 통째로 죽는다.**
파이썬 예외가 아니라 프로세스 사망이라 try/except로는 막을 수 없다.

딥 리서치가 유독 자주 죽은 이유는 단순하다. 하위 질의 6개 + 보완 질의 3개마다 검색이 돌고
검색 한 번에 리랭크가 여러 번 불리므로, **두 번째 호출까지 몇 초면 도달한다.** 일반 대화는
한 턴에 한두 번이라 운 좋게 살아남는 경우가 있었다.

**격리 재현** (조합별로 별도 프로세스, 30쌍 × 5회):

| 조합 | 결과 |
|------|------|
| cuda + FP16 (기존 설정) | 1회차 cuDNN 오류 → **2회차 core dump** |
| cuda + FP32 | 정상 (106ms) |
| **cuDNN SDPA 끔 + FP16** | **정상 (12ms)** |
| cuDNN SDPA 끔 + FP32 | 정상 (106ms) |
| cpu | 정상이지만 35초 — 사용 불가 |

**수정**: `Reranker.__init__`에서 CrossEncoder를 만들기 **전에**
`torch.backends.cuda.enable_cudnn_sdp(False)`를 부른다(`_disable_cudnn_sdpa()`).
flash/mem-efficient attention으로 내려가는데 오히려 9배 빠르다. 첫 호출의 커널 자동튜닝
비용(최대 길이 입력에서 12초)은 기동 시 워밍업 1회로 옮겼다.

**교훈**:
- **"예외를 잡아서 안전하게 물러섰다"가 안전하다는 뜻이 아니다.** 네이티브 라이브러리에서
  올라온 오류는 파이썬 예외로 보여도 그 아래 상태가 이미 망가졌을 수 있다. 반복해서 나는
  추론 실패 경고는 "성능 저하"가 아니라 **다음 크래시의 예고**로 다뤄야 한다.
- **프로세스가 로그 없이 사라지면 `dmesg`부터 본다.** OOM만 찾지 말 것 — 이번 건은 OOM이
  아니라 segfault였고, cgroup 메모리 사용량은 정상이었다. 애플리케이션 로그만 보면 영원히
  못 찾는다. 실제로 E-87에서 엉뚱한 원인(GPU 고갈)을 지목했다.
- **조합별 격리 재현이 답을 가장 빨리 준다.** 한 프로세스에서 여러 설정을 시험하면 첫
  크래시에 모든 정보를 잃는다. 조합마다 프로세스를 새로 띄우니 5분 만에 표가 나왔다.
- GPU/드라이버 조합이 바뀌면(B200 같은 신형) **가장 이국적인 커널 경로부터 의심할 것.**
  같은 코드가 다른 장비에서 몇 달간 멀쩡했다는 사실은 무죄의 증거가 아니다.

---

## E-89: 워치독과 런처가 동시에 백엔드를 띄워 사인(死因) 로그를 지움 (2026-08-02)

**증상**: `새싹이.sh`로 재시작한 직후 `backend.log`에
`ERROR: [Errno 98] error while attempting to bind on address ('0.0.0.0', 50002)`만 남고,
정작 **실제로 서비스 중인 프로세스의 로그는 사라졌다.**

**원인**: 백엔드는 모델 로딩 때문에 기동에 30~60초가 걸린다. 그 사이 워치독의 HTTP
헬스체크는 두 번 연속 실패하고 "죽었다"고 판단해 자기도 런처를 부른다. 두 번째 프로세스는
bind에 실패해 곧 죽지만, **죽기 전에 런처가 `backend.log`를 rotate하고 새로 열어 버린다.**
E-86에서 애써 보존하도록 만든 그 로그가 이 경로로 날아갔다.

**수정**:
- 워치독은 재시작 전에 **포트 점유 여부**를 본다. 응답이 없어도 포트를 잡고 있으면
  "기동 중"으로 보고 기다린다.
- `새싹이.sh`와 워치독이 같은 락(`data/run/launcher.lock`)을 쓴다. 런처는 `flock`으로
  자기를 감싸 재실행하고, 워치독은 `flock -n`으로 **못 잡으면 그냥 건너뛴다.**
  워치독이 부를 때는 `SAESSAGI_LAUNCH_LOCKED=1`을 넘겨 런처가 같은 락을 다시 잡아
  교착되는 것을 막는다.

**교훈**: **자동 복구 장치는 "아직 시작 중"과 "죽었다"를 구분해야 한다.** 둘을 같게 보면
복구가 아니라 방해가 되고, 최악의 경우 이번처럼 **원인 추적에 필요한 증거를 파괴한다.**
헬스체크 하나로 판단하지 말고, 기동 중임을 알리는 다른 신호(포트 점유, pidfile, 락)를
함께 볼 것.

---

## E-90: 런처 락을 백엔드가 물려받아 다음 재시작이 10분간 멈춤 (2026-08-02)

**증상**: E-89 수정(중복 기동 방지 락) 직후, `./새싹이.sh`가 **아무 출력 없이 멈췄다.**
`bash -x`를 붙여도 한 줄도 나오지 않았다.

**원인**: `exec flock -w 600 "$LOCK" "$0" "$@"` 형태로 스크립트 전체를 감쌌다. flock은
락 파일을 연 fd를 **일부러 닫지 않고**(FD_CLOEXEC 없이) 명령을 exec한다 — 그래야 명령이
도는 동안 락이 유지된다. 문제는 그 스크립트가 백그라운드로 띄우는 백엔드도 그 fd를
그대로 물려받는다는 것이다. 결과적으로 **백엔드가 사는 내내 락이 잡혀 있고**, 다음
런처 실행은 `-w 600`만큼 조용히 기다리다 죽는다.

확인은 한 줄이면 됐다:
```
$ fuser -v data/run/launcher.lock
    root  3900990 f....  python      ← 백엔드가 락 파일을 열고 있다
```

**수정**: 락을 **직접 연 fd 9**로 잡는다(`exec 9>"$LOCK"; flock -w 600 9`). fd 번호를
알고 있어야 자식에게 물려주지 않도록 닫을 수 있다 — 오래 사는 자식(백엔드·ollama·
neo4j·cloudflared)을 띄우는 자리마다 `9>&-`를 붙였다. 워치독은 자기가 flock으로 감싸는
대신 `SAESSAGI_LOCK_NOWAIT=1`을 넘기고, 런처가 락을 못 잡으면 exit 75로 물러난다
(워치독이 감싸면 그 fd가 또 백엔드로 상속된다 — 같은 함정).

**교훈**:
- **락을 명령 실행 래퍼로 쓰면, 그 명령이 띄우는 데몬까지 락을 물고 간다.** 데몬을
  띄우는 스크립트에는 `flock <파일> <명령>` 형태를 쓰지 말 것. fd를 직접 열고 자식에게
  닫아 줄 것.
- **"출력이 한 줄도 없다"는 곧 첫 줄에서 막혔다는 뜻이다.** 스크립트 본문을 의심하기
  전에 맨 앞의 exec·락·리다이렉션을 볼 것.
- 파일 락 문제는 `fuser`/`lsof`로 **누가 잡고 있는지 먼저 본다.** 추측보다 빠르다.
- 안전장치를 새로 넣을 때는 **그 장치가 만드는 새 실패 모드**를 함께 따져야 한다.
  E-89를 막으려고 넣은 락이 그 자리에서 새 장애를 만들었다.

---

## E-91: 인제스트 실패를 기록하지 않아 실패 파일 20건이 정원을 독차지, 임베딩이 7시간 반 정지 (2026-08-05)

**날짜**: 2026-08-05
**증상**: 2018완결보고서 임베딩이 진행되지 않는다. 557개 중 117개만 색인된 채 멈췄고,
백엔드는 살아 있으며 오류도 나지 않는다. 로그는 매 분 **똑같은 줄**을 찍는다:
```
rag_watch: 인제스트 20 / 이동 0 / 보류 0 / 다음주기 444 / 변화없음 11644 (누적 11641건)
```
「인제스트 20」이 계속 20인데 「누적」은 11641에서 1도 늘지 않는다. 마지막 성공은
2026-08-05 01:09:59였고 그 뒤 7시간 반 동안 0건이다.
**원인**: `_apply_ingest`의 실패 처리가 **아무것도 기록하지 않았다.**
```python
except Exception as exc:
    # 상태에 기록하지 않으므로 다음 주기에 재시도.
    logger.warning(f"rag_watch: 인제스트 실패 {cand.rel_path}: {exc!r}")
```
기록이 없으면 그 digest는 `state.get()`이 None이라 **다음 주기에 또 "신규 파일"로 잡힌다.**
텍스트 레이어가 없는 스캔 이미지 PDF는 `422 문서에서 텍스트를 추출할 수 없습니다`로
몇 번을 다시 해도 실패하므로 영구히 후보로 남는다. 그런 파일이 2016에 6건·2017에 12건·
2018에 2건, 합쳐서 정확히 **20건 = `max_per_cycle`**이 쌓인 순간, `build_plan`이 폴더명
정렬 순서(2016 → 2017 → 2018)로 훑으며 정원을 채우는 구조라 **앞쪽 실패 파일이 20슬롯을
전부 선점**했다. 나머지 444건은 매 주기 `deferred`로 밀려 영원히 처리되지 않았다.
개별 실패는 WARNING으로 찍혔지만 20줄이 매 분 반복되니 오히려 눈에 띄지 않았고,
"인제스트 20"이라는 숫자는 정상 가동처럼 보였다.
**수정**:
- 상태 파일에 `failures`(digest → count·error·path·시각)를 **디스크까지 저장**한다.
  메모리에만 두면 재시작마다 카운터가 초기화돼 같은 사고가 되풀이된다.
- `max_ingest_failures`(기본 3)회 연속 실패하면 `plan.quarantined`로 빼고 **`to_ingest`
  정원을 쓰지 않는다.** 이것이 핵심 — 실패가 정상 파일을 굶기지 않게 한다.
- 키가 내용 해시이므로 사용자가 파일을 고쳐 넣으면 digest가 달라져 자동으로 재시도된다.
  성공하면 `clear_failure`로 기록을 지워 일시적 오류가 누적되지 않게 한다.
- 포기 목록은 **목록이 바뀐 주기에만** 사유와 함께 한 번 남긴다(매 주기 반복하면 묻히고,
  안 남기면 조용히 빠진 것을 알 수 없다). 주기 요약에도 `/ 포기 N`을 추가했다.
- 실패 기록 정리는 `live_digests`가 아니라 **기록된 경로의 존재 여부**로 판단한다.
  재시작 직후엔 모든 파일이 "안정화 대기"라 해시를 계산하지 않아 `live_digests`가 비는데,
  그걸 기준으로 지우면 매 재시작마다 기록이 날아간다 (E-69와 같은 함정).
**검증**: 회귀 테스트 12건. 핵심은 `test_q3_quarantined_does_not_consume_cycle_budget`와
`test_f2_failure_does_not_starve_healthy_files` — 정렬상 앞선 실패 파일이 정원과 같은 수만큼
있어도 뒤의 정상 파일이 전부 처리되는지 본다(예전 구현에서는 0건 처리된다).
`test_q5`·`test_f4`는 재시작을 넘겨 격리가 유지되는지 확인한다.
**교훈**:
- **실패를 기록하지 않는 재시도는 재시도가 아니라 무한 루프다.** "다음 주기에 재시도"는
  재시도 횟수에 한도가 있을 때만 성립한다. 한도가 없으면 영구 실패 항목이 처리량을
  0으로 만든다.
- **작업 정원(`max_per_cycle`)은 성공 가능한 일에만 배분할 것.** 실패가 정원을 소비하면
  큐 뒤쪽은 굶는다. 정원 밖으로 빼는 경로를 반드시 둘 것.
- **"N건 처리 중"이라는 숫자는 진척이 아니다.** 진척은 누적치의 변화로만 판단해야 한다.
  이 사고에서 `인제스트 20`은 7시간 반 동안 아무 일도 하지 않으면서 정상처럼 보였다.
- 매 주기 반복되는 WARNING은 **경보가 아니라 배경 소음이 된다.** 반복되는 실패는
  상태로 집계하고 **변화가 있을 때만** 알릴 것.

---

### E-88: doc_entity_id 인덱스 누락으로 7단계 쓰기가 2.7시간짜리가 됨 (CR-61, 2026-08-06)

**증상**: 전역 정규화(7단계)의 계산은 5분 만에 끝났는데(정확일치 8초 + 퍼지 4.3분),
그 뒤 결과를 SQLite에 쓰는 단계에서 진행이 멎었다. 4분 동안 `doc_entities` 206,460건 중
**5,000건만** 연결됐다 — 이 속도면 전체 2.7시간이다. 로그에는 아무 오류도 없고 그냥 느렸다.

**원인**: `link_doc_entities_bulk`가 후보 행까지 되짚어 갱신했다.

```sql
UPDATE entity_candidates SET canonical_id=?, state=? WHERE doc_entity_id=?
```

`entity_candidates`의 인덱스는 `doc_id`·`state`·`entity_type`·`chunk_id` 넷뿐이고
**`doc_entity_id`에는 인덱스가 없었다.** 그래서 이 UPDATE 한 번이 216,509행 전체 스캔이고,
그것을 206,460번 반복했다 — 4.5×10¹⁰ 행 방문.

`doc_entity_id` 컬럼은 CR-60에서 "근거 역추적용"으로 만들어졌지만 그때는 **아무도 그 컬럼으로
조회하지 않았다.** 조회 경로가 생긴 것은 7단계를 구현한 지금이다.

**수정** (두 가지를 같이 했다):
1. `idx_cand_docent`·`idx_cand_canon` 인덱스 추가. `CREATE INDEX IF NOT EXISTS`라 기존 DB도
   다음 열 때 자동으로 생긴다(0.5초).
2. 더 근본적으로, **행별 UPDATE를 집합 연산 하나로 바꿨다.**
   `propagate_canonical_to_candidates()`가 조인 한 번으로 216,509행을 갱신한다.
   `link_doc_entities_bulk`는 이제 `doc_entities`만 건드린다.

**결과**: 쓰기 단계가 **2.7시간(추정) → 45초**. 7단계 전체 312.8초.
리포트 쿼리도 같은 이유로 느려서(285,555행 관계에 인덱스 없음) `idx_rel_type`·
`idx_rel_source`·`idx_rel_target`·`idx_canon_df`를 함께 추가했다 — **120초 초과 → 1.6초.**

**교훈**:
- **컬럼을 만든 시점과 그 컬럼으로 조회하는 시점이 다르다.** CR-60은 `doc_entity_id`를
  기록용으로만 썼기에 인덱스가 필요 없었고, 실제로 없어도 아무 문제가 없었다. 새 조회
  경로를 추가할 때는 **그 조건절에 인덱스가 있는지 반드시 확인할 것.** 스키마를 만든
  사람과 쓰는 사람이 같아도 시차가 있으면 놓친다.
- **"느림"은 오류로 보이지 않아서 발견이 늦다.** 이 버그는 예외도 경고도 없었고 진행률만
  천천히 올라갔다. 대량 배치는 **처음 몇 %에서 전체 소요를 외삽해 보는 습관**이 필요하다.
  4분에 5,000건이면 206,460건은 2.7시간이라는 산수를 그 자리에서 했어야 한다.
- **20만 번의 개별 UPDATE는 인덱스가 있어도 집합 연산 하나보다 느리다.** 인덱스는 증상을
  줄일 뿐이고, 루프를 SQL 안으로 밀어넣는 것이 본치다.

---

### E-89: df 임계값이 '벼'·'토마토'를 행정 상용구와 같이 취급 (CR-61, 2026-08-06)

**증상**: 상용구 판정(`document_frequency >= 60`)에 57건이 걸렸는데, 그 안에
`벼`(343문서)·`작물`(263)·`토마토`(201)·`콩`(193)·`고추`(167)가 `산업재산권 출원`(204)·
`학술발표`(133)와 나란히 있었다. 검색 랭킹에서 상용구는 가중치를 0.15배로 깎으므로,
**연결성을 살리려고 일부러 만든 작물 허브를 우리 손으로 무력화**하는 상태였다.

**원인**: 빈도만으로 상용구를 판정했다. 그런데 df가 높은 데에는 성격이 다른 두 이유가 있다.
- `산업재산권 출원` — 모든 과제가 쓰는 행정 항목. 변별력이 없다.
- `벼` — 이 코퍼스의 주요 연구 대상. **높은 df 자체가 의미 있는 사실**이다.

**수정**: `from_target_key=1`인 엔티티는 상용구로 찍지 않는다. 근거는 작물이라서가 아니라
**구조적 사실** — target_key는 추출기가 "이 연구의 핵심 대상"으로 지목한 값이라 정의상
연구 주제이지 행정 항목이 아니다. (`merge.py`의 "작물 목록을 박지 않는다" 원칙과 같은 방식으로
도메인 사전 없이 판정한다.) 상용구 57건 → **22건**, 전부 실제 행정 OUTPUT.

**교훈**:
- **하나의 통계량으로 두 가지를 가르려 하면 둘 다 틀린다.** df는 "흔하다"만 말해 주지
  "의미 없다"는 말해 주지 않는다. 흔하면서 의미 있는 것과 흔하면서 무의미한 것을 가르려면
  빈도 밖의 신호가 필요하다.
- 검색 가중치는 IDF가 이미 부드럽게 처리한다(`벼` log(1+6121/343)=2.9 vs 희소어 8.7).
  거기에 **하드 페널티를 겹쳐 걸 때는 대상이 정말 무의미한지 따로 확인할 것.**

---

### E-90: "그래프 초기화" 버튼이 M_23 그래프를 반쯤 부수는 상태였음 (CR-61, 2026-08-06)

**증상**: 아직 터지지 않은 잠복 결함이다. 그래프 탭의 **초기화 버튼을 한 번 누르면**
Mention 216,509개가 통째로 고아가 되는 상태였다.

**원인**: `Neo4jGraphStore.clear_all`(M_19, CR-26)이 지우는 라벨 목록에
`Chunk`와 `Document`가 들어 있다.

```python
for label in ("Chunk", "Entity", "Keyword", "TechnologyCode", "Document", "Note"):
    self._run(f"MATCH (n:{label}) DETACH DELETE n")
```

M_19만 있을 때는 맞는 목록이었다. 그런데 CR-61에서 M_23이 **같은 `Document`·`Chunk`
노드를 쓰기 시작**했다. 라벨 목록은 그대로인데 그 라벨이 가리키는 대상이 늘어난 것이다.

더 나쁜 것은 실패 모습이다. `CanonicalEntity`·`Mention`·`Project`는 목록에 없어서 **노드는
남는다.** 대신 `DETACH DELETE`가 `HAS_DOCUMENT`·`HAS_CHUNK`·`HAS_MENTION` 엣지를 전부
끊는다. 결과는 "노드 20만 개가 멀쩡히 있는데 그래프 탭은 텅 비어 있고 검색도 아무것도
못 찾는" 상태 — 지워졌다면 차라리 알아채기 쉬웠을 것이다.

**수정**:
1. `KgGraphStore.clear_all()` 신설 — M_23 라벨까지 함께, 배치로 지운다. 반쪽 그래프를
   남기지 않는다. **Neo4j만 지우고 `data/kg_candidates.db`는 건드리지 않는다** —
   26시간짜리 추출 결과인 `entity_candidates` 216,509건은 다른 저장소에 있고,
   그래프는 거기서 8분이면 다시 만든다.
2. `/api/graphrag/clear`가 M_23 적재 여부를 보고 초기화 경로를 고른다.
3. `purge_legacy_keyword_graph`에 **선행 검사**를 붙였다 — M_23이 비어 있거나 M_23 노드가
   legacy 라벨을 겸하고 있으면 삭제를 거부한다. `purge_preflight()`는 숫자만 세고
   아무것도 지우지 않는다.
4. 회귀 테스트 11건(`tests/kg/test_graph_store.py`) — Neo4j 없이 실행된 Cypher를 가로채
   **무엇을 지우려 했는지**를 검사한다.

**교훈**:
- **라벨 기반 삭제는 라벨을 공유하는 순간 남의 데이터를 지운다.** 스키마를 하나 더 얹을
  때는 새 코드가 옛 코드를 깨뜨리는지뿐 아니라 **옛 코드가 새 데이터를 깨뜨리는지**를
  같이 봐야 한다. 이번 것은 후자였고, 그래서 새 코드만 테스트해서는 절대 안 잡힌다.
- **부분 삭제는 전체 삭제보다 나쁘다.** 노드가 남고 엣지만 끊기면 "데이터는 있는데
  동작만 안 하는" 상태가 되어 원인 추적이 몇 배 어려워진다. 지울 거면 일관되게 지울 것.
- 되돌릴 수 없는 동작에는 **선행 검사(preflight)를 별도 함수로 두고, 그 함수는 절대
  아무것도 바꾸지 않게** 할 것. 호출자가 숫자를 보고 판단할 수 있어야 한다.

---

### E-91: 스캔 상한 400,000이 폴더를 통째로 숨겨 문서 5,949건이 추출에서 빠짐 (CR-61, 2026-08-06)

**증상**: 사용자 지적 — "엔티티 추출할 때 폴더 상황이 등록문서 폴더 상황이랑 달라.
동기화가 자동으로 안 되는 것 같은데?" 두 화면을 대조하니 실제로 어긋나 있었다.

| 폴더 | 문서 탭 | 추출 드롭다운 |
|---|---|---|
| 2017완결보고서 | 932 | **93** |
| 2018완결보고서 | 544 | **목록에 없음** |
| RFP(2015-2019) | 2,136 | **목록에 없음** |
| RFP(2020-2025) | 1,903 | **목록에 없음** |
| 2025완결보고서 | 562 | 231 |
| 나머지 9개 | 605·631·418·780… | 전부 일치 |

**원인**: 폴더 집계가 벡터 스토어를 `.limit(400_000)`으로 스캔하고 있었다.

```python
rows = tbl.search().select(["doc_id", "category"]).limit(400_000).to_list()
```

청크가 **599,338개**로 늘면서 **33%(199,338건)가 조용히 잘려나갔다.** LanceDB는 상한에
걸려도 예외를 던지지 않는다 — 그냥 앞에서부터 40만 행만 준다. 스캔 순서상 뒤쪽에 있던
폴더가 통째로 사라지거나 일부만 세어졌다.

문서 탭이 멀쩡했던 이유는 거기가 `.limit(1_000_000)`을 쓰기 때문이다. **같은 데이터를
두 곳에서 서로 다른 상한으로 읽고 있었고**, 한쪽만 코퍼스 성장을 못 따라갔다.

**파장이 표시 오류에 그치지 않았다.** 같은 상한이 `_documents_in_folder()`
— 즉 **추출 대상 선정** — 에도 쓰였다. 폴더가 드롭다운에 없으면 고를 수가 없고,
골라도 목록이 잘려 있었다. 그 결과:

```
전체 문서 12,074 · 추출된 문서 6,121 · 추출 안 된 문서 5,953 (49%)
  RFP(2015-2019) 2,136 · RFP(2020-2025) 1,903 · 2017완결 932
  2018완결 544 · RFP(2008-2009) 434
```

**지식그래프가 코퍼스의 절반만 담고 있었고, 아무도 그것을 몰랐다.**

**수정**: `_scan_doc_categories()` 하나로 합치고, 상한을 `tbl.count_rows()`에서 구한다.
그러고도 상한에 걸리면 **ERROR 로그를 남긴다** — 조용히 틀리느니 시끄럽게 틀리는 편이 낫다.
`src/kg/service.py`(폴더 목록·추출 대상)와 `scripts/kg_run.py`(CLI) 양쪽 4곳을 고쳤다.

**교훈**:
- **매직 넘버 상한은 유효기간이 있는 코드다.** 작성 시점엔 넉넉했고(32만 < 40만),
  데이터가 자라면서 어느 날 조용히 틀리기 시작했다. 상한은 **데이터에서 구하거나,
  최소한 걸렸을 때 소리를 내야** 한다.
- **같은 데이터를 두 화면이 다른 방법으로 세면 언젠가 어긋난다.** 문서 탭과 추출 패널이
  각자 스캔했고 상한만 달랐다. 세는 방법은 한 곳에 두고 공유할 것.
- **"자동 동기화가 안 되는 것 같다"는 사용자 관찰이 정확했다.** 동기화 로직의 문제가
  아니라 한쪽이 데이터를 덜 읽고 있었지만, **증상 서술은 옳았다.** 사용자가 두 화면의
  숫자가 다르다고 하면 먼저 양쪽이 같은 것을 세는지 확인할 것.
- 표시용 조회 코드가 **실행 대상 선정에도 쓰이면** 표시 버그가 곧 처리 누락이 된다.
  이번엔 그래서 화면 문제가 문서 5,949건 누락으로 번졌다.

---

### E-92: 예산 머리글이 본문을 통째로 버려 RFP 수백 건이 빈 채로 "완료" (CR-61, 2026-08-06)

**증상**: 전체 추출 전에 `limit=3`으로 시험 실행했더니 3건 모두
`COMPLETED · accepted 0 · rejected 0`이었다. 후보도 0, 거절도 0 — **LLM을 한 번도 안 불렀다**는 뜻이다.
문서를 열어 보니 연구목표·연구내용·작물명이 가득한 정상 RFP였다.

**원인**: `score_chunk()`가 앞 200자에 저가치 패턴이 하나라도 있으면 무조건 `EXCLUDED`를 줬다.

```python
for bad in _LOW_VALUE_PATTERNS:        # "연구비", "소요예산", "참고문헌" …
    if bad.lower() in lowered[:200]:
        return EXCLUDED, ""
```

2008~2014년 RFP는 **한 쪽짜리**라 머리글에 `총 연구비 : 130백만원`이 있고 **바로 아래에
연구목표가 이어진다.** `"연구비"`가 앞 200자에 걸려 알맹이가 가득한 청크가 '예산표'로
오인돼 버려졌다. 문서에 청크가 1개뿐이니 남는 게 없었다.

`select_chunks`의 "첫머리 최소 1개 확보" 안전장치도 `c.score > EXCLUDED` 조건이라
구제하지 못했다 — 하드 제외를 존중하도록 **의도적으로** 설계된 부분이었다.

**실측 피해** (폴더별 25건 표본, 0청크 비율):

| 폴더 | 수정 전 | 수정 후 |
|---|---|---|
| RFP(2008-2009) | **84%** | 0% |
| RFP(2010-2014) | **28%** | 0% |
| 나머지 4개 폴더 | 0% | 0% |

RFP(2010-2014)는 **이미 추출을 끝낸 폴더**다. 1,988건 중 약 550건이 이 버그로 빈 채
"완료"로 기록돼 있었고, 전체 6,121건 중 708건이 후보 0건이던 것의 큰 몫이 이것이다.

**수정** (두 겹):
1. `score_chunk` — 하드 제외를 **고가치 신호가 하나도 없을 때만** 적용한다.
   저가치 패턴의 취지는 "참고문헌·예산표처럼 건질 게 없는 자리"를 거르는 것이지
   "예산 숫자가 한 줄 섞인 본문"을 버리는 것이 아니다.
2. `select_chunks` — **문서가 통째로 0청크가 되면 가장 긴 청크 1개를 복구한다.**
   제외 규칙이 틀릴 수 있다는 것을 전제로 한 최후의 안전망이고, 복구 시 로그를 남긴다.
   틀린 청크를 보내는 비용은 LLM 호출 1회지만, 문서를 통째로 잃는 비용은 영구적이다.

회귀 테스트 6건 (`tests/kg/test_documents.py::TestLowValueVetoDoesNotStarveDocuments`) —
예산 머리글이 있어도 연구목표가 있으면 통과하고, **순수 예산표는 여전히 제외**되는지
둘 다 고정했다.

**교훈**:
- **필터의 실패 방향을 보라.** 이 제외 규칙은 "애매하면 버린다"였는데, 버리는 쪽 실패는
  조용하다(오류 없음, 로그 없음, 그냥 결과가 0). 반대로 "애매하면 보낸다"의 실패는
  LLM 호출 한 번 낭비하고 검증 단계가 잡아 준다. **되돌릴 수 없는 쪽으로 실패하게
  설계하지 말 것.** `merge.py` 서두의 "사전 누락은 안전한 쪽으로 실패한다"와 같은 원칙인데
  여기서는 반대로 돼 있었다.
- **파이프라인 단계마다 "출력이 0이 될 수 있는가"를 물어야 한다.** 0이 정상일 수 있다면
  0과 실패를 구분할 수단이 있어야 하고, 없다면 0을 막는 안전망을 둬야 한다.
- **대규모 실행 전 소규모 시험은 건수가 아니라 산출물을 봐야 한다.** `completed: 3`만
  봤으면 통과였다. `accepted: 0`을 보고 멈춘 것이 25시간을 아꼈다.
- 앞 200자 같은 **위치 기반 휴리스틱은 문서 서식이 바뀌면 조용히 깨진다.** 한 쪽짜리
  문서와 200쪽짜리 문서에 같은 규칙을 쓰고 있었다.

---

### E-93: 그래프에서 문서를 열면 깨진 JSON · 노드 이름이 "(해당 시 작성)" (CR-61 회귀, 2026-08-07)

**증상**: 사용자가 그래프 탭에서 문서를 열었더니 새 탭에
`{"detail":"?맠낮 ?뜻씩?? ...doc:TRKO202100010370_...pdf_25c9033d"}` 가 떴고,
그래프에 이름이 `(해당 시 작성)`인 노드가 여럿 보였다. 서로 다른 세 결함이 겹쳤다.

#### (1) 문서 노드 id 계약을 깼다 — 주 원인

CR-61 이전 계약은 **"문서 노드의 `id` == `doc_id`"** 였다(`graph_rag/neo4j_store.py:307`).
프론트는 그 전제로 `openDocument(selected.id, …)`를 부른다 — 접두사 제거 같은 건 없다.

CR-61에서 새로 쓴 개요 쿼리가 **`Project.project_id`** 를 노드 id로 넣었다:

```python
nodes[pid] = {"id": pid, ..., "kind": "document"}   # pid = p.project_id
```

과제번호가 없는 문서는 `project_id`가 `doc:<doc_id>` 대체키다. `_find_doc_dir()`은
디렉토리명을 그대로 매칭하므로 `doc:` 접두사가 붙으면 못 찾고 404.

더 나쁜 것은 **두 엔드포인트의 id 체계가 달랐다**는 점이다:

| 엔드포인트 | 문서 노드 `id` | 열어보기 |
|---|---|---|
| `/graph` (개요) | `Project.project_id` | **404** |
| `/doc-focus` | `Document.doc_id` | 정상 |

같은 화면에서 어디를 거쳤느냐에 따라 되기도 하고 안 되기도 했다.

**수정**: id 체계를 억지로 통일하지 않았다 — 개요가 보여주는 것은 **과제**가 맞고,
다문서 과제가 377개라 doc_id와 1:1이 아니다. 대신 **여는 데 쓸 `doc_id`를 노드에 별도
필드로** 실어 보내고, 없으면 프론트가 열기 버튼을 감춘다. 읽기 시점 쿼리라 **재적재 불필요.**

#### (2) 404 본문이 브라우저에 날것으로 뜨고 한글이 깨짐

Starlette는 media type이 `text/`로 시작할 때만 charset을 붙인다(`responses.py:79`).
`application/json`에는 안 붙으므로 브라우저가 시스템 로케일(CP949)로 폴백해 UTF-8 한글이
`?맠낮 ?뜻씩??`가 됐다.

**전역 문제인데 지금까지 안 드러난 이유**: 앱 내부 `apiFetch`는 `res.json()`이 스펙상
항상 UTF-8이라 멀쩡하다. `openDocument()`가 `window.open()`으로 **응답을 문서로 직접
렌더**하는 유일한 경로였다.

**수정**: `server.py`에 `HTTPException` 핸들러를 등록해
`media_type="application/json; charset=utf-8"`로 응답한다(한 곳으로 전 라우트 해결).
더해서 `openDocument()`가 브라우저 경로에서 HEAD로 먼저 확인하고, 실패하면 새 탭 대신
호출자 콜백으로 알린다 — 날 JSON을 보여주는 일 자체를 없앴다. Electron 경로는 자체
폴백이 있어 건드리지 않았다.

#### (3) 제목이 서식 안내문 — 663건

`identity.py`의 제목 정규식이 `과제명: (해당 시 작성)` 처럼 **채우라고 비워 둔 칸**을
과제명으로 잡았다. 실측:

```
663건  (해당 시 작성)      69건  (내역사업명)
 81건  농업과학기반기술연구    34건  단위사업명 차세대바이오그린21
서식 문구 제목 합계 686 / 11,276 (6.1%)
```

**수정 (두 겹)**:
- `identity.is_placeholder_title()` — 서식 어휘 판정. 첫 매치에서 멈추지 않고
  안내문이면 **다음 매치를 계속 본다**(같은 문서 뒤쪽에 진짜 과제명이 또 나온다).
- `projects.resolve_projects()` — 제목이 안내문이면 **파일명에서 만든다.**
  실측상 663건 **전부** 파일명이 진짜 과제명이었다
  (`TRKO..._한우이유시기와단백질수준에따른대사생리및탄소저감연구.pdf`).
  폴백이 **읽기 시점**에 있어 이미 저장된 663건이 재추출(14시간) 없이 고쳐진다.
- 판정 함수는 `identity.py` **한 곳**에 두고 양쪽이 공유한다 — 목록을 복사하면 한쪽만
  고쳐진다.

#### (4) 범례가 옛 키워드 문구

`● 키워드 / ◪ 노트 / — 공유 키워드 연관`이 그대로였다. 지금 그래프에 노트 노드는 없고
엣지는 `shares_entity`다. 노드 상세 패널은 이미 "엔티티"로 고쳐져 있어 범례만 어긋났다.

**교훈**:
- **데이터 출처를 바꿀 때 소비자와의 암묵적 계약을 목록으로 적어 볼 것.** "문서 노드
  id == doc_id"는 어디에도 문서화돼 있지 않았고 프론트 코드에만 암묵적으로 존재했다.
  CR-61은 스키마를 갈아끼우면서 그 계약을 몰랐다.
- **같은 개념의 노드를 두 엔드포인트가 다른 id로 주면 반드시 사고가 난다.** 하나는
  되고 하나는 안 되니 재현이 들쭉날쭉해 원인 파악이 늦다.
- **오류 응답도 UI다.** 내부 fetch로만 소비된다고 가정했는데 `window.open`이 사용자에게
  직접 보여 주고 있었다. 사용자에게 보일 수 있는 모든 경로에 인코딩을 챙길 것.
- **서식 문서에서 정규식으로 값을 뽑을 때는 "빈 칸"을 값으로 착각하지 말 것.**
  양식은 채우라고 비워 두며, 그 안내문이 정규식에는 값처럼 보인다.
- 데이터 정정이 **읽기 시점 폴백**으로 되면 재추출을 하지 않는다. 14시간과 몇 줄의 차이다.

---

### E-94: HEAD 사전확인을 넣었더니 모든 문서 열기가 실패 (E-93 수정의 부작용, 2026-08-07)

**증상**: E-93을 고친 직후 사용자가 "뭔 전부 다 원본 파일을 찾을 수 없대?" — 그래프에서
어떤 문서를 눌러도 `원본 파일을 찾을 수 없습니다: …` 배너가 떴다. 원본 파일은 멀쩡히
있었다(`data/rag_originals`에 12,069개 디렉토리).

**원인**: E-93에서 "날 JSON을 새 탭에 띄우지 말자"며 `openDocument()`에 HEAD 사전확인을
넣었는데, **다운로드 엔드포인트가 HEAD를 받지 않았다.**

```
GET  /api/rag/documents/{id}/download → 200 · 128,290 bytes
HEAD /api/rag/documents/{id}/download → 404      ← 사전확인이 여기서 죽었다
```

`@router.get()`으로 등록된 FastAPI `APIRoute`는 **HEAD를 자동으로 붙여 주지 않는다.**
Starlette의 평범한 `Route`는 `if "GET" in methods: methods.add("HEAD")`를 하지만 FastAPI는
안 한다. 그래서 HEAD가 API 라우트에 안 걸리고 `/`에 마운트된 정적 파일로 흘러가 404가 났고,
프론트는 그것을 "파일 없음"으로 해석했다.

**수정**: 라우트를 `@router.api_route(..., methods=["GET", "HEAD"])`로 바꿨다.
`FileResponse`는 HEAD면 헤더만 보내므로(`starlette/responses.py`의 `send_header_only`)
본문 낭비도 없다. 검증: 실패했던 그 문서 HEAD 200 · GET 200(128KB), 없는 문서는 여전히
HEAD 404(오탐 방지), 그래프 노드 표본 8/8 HEAD 200.

**교훈**:
- **새 HTTP 메서드로 기존 엔드포인트를 부르기 전에 그 엔드포인트가 그 메서드를 받는지
  확인할 것.** "GET이 되니 HEAD도 되겠지"는 프레임워크마다 다르다 — Starlette는 되고
  FastAPI는 안 된다.
- **오류 UX를 개선하려다 정상 경로를 깼다.** 사전확인은 "실패할 때만 다르게 행동"하려던
  것인데, 사전확인 자체가 항상 실패해서 **모든 성공 경로를 실패로 바꿨다.**
  안전장치를 넣을 때는 그것이 통과 경로에서 무해한지를 먼저 봐야 한다.
- E-93 검증에서 나는 **GET으로만 확인했다**(`curl -o /dev/null`). 프론트가 실제로 부르는
  것은 HEAD였는데 그 경로를 안 밟아 봤다. **사용자가 밟는 경로 그대로 확인할 것.**

---

### E-95: 추출→구축 자동 연결이 조용히 거부됨 (CR-61, 2026-08-07)

**증상**: 추출이 정상 완료(442/442, 후보 10,867, 실패 0)됐는데 그래프가 갱신되지 않았다.
로그에는 `추출 완료 — 그래프 구축을 자동으로 이어서 시작합니다 (후보 10867건)` 가 찍혔는데
`jobs` 표에 **`build` 작업 기록이 아예 없었다.** 사용자가 "추출 끝났는데?" 하고 물어서 발견.

**원인 두 가지가 겹쳤다.**

1. **가드가 자동 연결 자신을 막았다.** `start_build()`에 동시 실행 방지가 있다:
   ```python
   if self.running:   # self._task 가 아직 done 이 아니면 True
       return {"started": False, "reason": "추출이 진행 중입니다…"}
   ```
   그런데 자동 연결은 `_run()` — **추출 태스크 안** — 에서 부른다. 그 시점에 태스크는
   당연히 아직 done이 아니므로 `self.running`이 True고, 자기 자신 때문에 거부됐다.

2. **호출부가 반환값을 안 봤다.** `start_build`는 예외를 던지지 않고
   `{"started": False, "reason": ...}`를 돌려준다. 호출부는 `try/except`만 두고 반환값을
   버려서, 거부당해도 흔적이 남지 않았다. `except`가 안 걸리니 에러 로그도 없었다.

**수정**:
- `start_build(..., after_extraction: bool = False)` — 추출 자신이 부를 때만 가드를 건너뛴다.
  수동 호출은 여전히 추출 중이면 거부된다(반쪽 그래프 방지).
- 호출부가 `result.get("started")`를 검사해 실패 시 ERROR 로그를 남긴다.
- 회귀 테스트 4건(`tests/kg/test_service_autobuild.py`) — 가드 통과·수동 거부 유지·
  구축 중복 거부, 그리고 **호출부가 반환값을 검사하는지**를 소스로 확인하는 테스트까지.

**교훈**:
- **반환값으로 실패를 알리는 API는 반환값을 봐야 한다.** `try/except`만 두면 "예외가 안
  났으니 성공했겠지"가 되는데, 이 API는 예외를 안 던진다. 성공 로그를 **호출 전에** 찍은
  것도 문제였다 — "시작합니다"는 시작한 뒤에 찍어야 한다.
- **재진입 가드는 자기 자신을 막을 수 있다.** "A가 도는 동안 B 금지"를 A 안에서 B를 부르는
  구조에 그대로 적용하면 영원히 안 된다. 가드를 넣을 때 **호출 그래프상 누가 부르는지**를
  볼 것.
- 이번 것도 E-94와 같은 부류다 — **안전장치가 정상 경로를 막았다.** 이틀 사이 세 번째다
  (E-92 청크 제외, E-94 HEAD 사전확인, E-95 재진입 가드).

---

### E-96: 딥 리서치가 자기 출력을 자기 근거로 인용 (자기참조 순환, 2026-08-08)

**증상**: 사용자 지적 — "딥 리서치에서 업무 노트의 내용도 참조를 해 버리네. 노트를
참조하면 리서치 결과물도 참조를 해 버리는 거잖아."

**원인**: 딥 리서치 보고서에는 "업무노트로 저장" 버튼이 있고(CR-20), 저장된 노트는
`__knowledge__` 카테고리로 **벡터 스토어에 들어간다.** 그런데 `_retrieve()`가
`source` 인자를 넘기지 않아 기본값 `"both"`로 검색했다 — 문서 + 노트 전부.

즉 **LLM이 지어낸 문장이 다음 보고서에서 `[3]`으로 인용된 사실로 승격된다.**
환각 억제 장치(`EVIDENCE_RULES`)가 "근거에 있는 것만 진술하라"고 강제하는데, 그 근거
자체가 이전 LLM 출력이면 규칙이 오히려 환각을 세탁해 준다.

실측: 벡터 스토어의 노트 4건이 **전부** 딥 리서치 산출물이었다.
```
__knowledge__:중복성검토미래기후-시나리오와-극한기상에-따른-작물-및-탄소흡수-적응기술-연구
__knowledge__:중복성검토저탄소-논물관리를-위한-다중물떼기-기술체계-확립
__knowledge__:중복성검토사과-유전체-육종시스템-개발
```

스펙 §1이 "**사내 지식 기반만을 근거로**"라고 한 것의 취지에도 어긋난다 — 사내 자료란
사람이 만든 문서를 말하지 기계가 방금 쓴 보고서가 아니다.

**수정 (두 겹)**:
- `_retrieve()`가 `source="docs"`로 검색한다 (`vector_search.store`의
  `docs` = `category IS NULL OR category != '__knowledge__'`).
- 그래도 섞여 오면 `_is_note()`로 한 번 더 거른다. 지금 지식그래프에는 노트가 0건이라
  그래프 경로는 깨끗하지만, 나중에 노트를 넣으면 이 한 줄이 없을 때 순환이 조용히
  되살아난다.
- 노트만 걸리면 근거 0건으로 처리한다 — 순환으로 보고서를 쓰느니 안 쓰는 게 낫다.

회귀 테스트 4건(`TestNotesExcluded`) — 검색이 `docs`로 나가는지, 노트 hit이 걸러지는지,
노트만 있으면 `NO_EVIDENCE_REPORT`가 나오는지, 벡터-only 폴백 경로도 같은지.

**부수 발견**: 테스트 fake들이 `hybrid_retrieve(query, top_k)`만 받아 `source` 추가로
TypeError가 났는데, `_retrieve`의 `except Exception`이 그것을 **검색 실패로 삼켜** 근거
0건이 됐다. 그중 하나는 fake가 이벤트를 set하기 전에 죽어 테스트가 **영원히 대기**했다.
→ 광범위한 `except Exception`이 계약 위반(TypeError)까지 삼키면 원인이 안 보인다.
지금은 그대로 두되(검색 실패에 파이프라인이 죽으면 안 된다) 이 사례를 기록해 둔다.

**교훈**:
- **시스템이 자기 출력을 다시 입력으로 먹는 경로가 있는지 보라.** 생성물을 저장하는
  기능(노트 저장)과 저장소를 검색하는 기능(RAG)이 같은 저장소를 쓰면 순환이 생긴다.
  기능 각각은 맞는데 조합이 틀린 경우라 코드 리뷰로는 잘 안 보인다.
- 근거 인용 강제는 **근거의 출처가 신뢰할 수 있을 때만** 환각을 막는다. 근거 자체가
  생성물이면 인용 규칙이 환각에 각주를 달아 줄 뿐이다.

---

### E-97: 진행률이 `360,000/363,235`에서 멈춘 것처럼 보임 — 실제로는 다음 단계가 조용히 9분간 실행 (CR-61, 2026-08-08)

**증상**: 사용자 지적 — "여기에 왜 360,000/363,235 이렇게 왜 멈춰있어?"
화면은 `진행 중 · 전역 정규화 · 360,000/363,235 · 경과 7분`에 얼어붙어 있었다.
구축은 정상 진행 중이었다(`top -H`로 스레드 1개가 99.9% 점유, CPU 시간이 벽시계와 같은
속도로 증가). **작업은 멀쩡한데 표시만 죽어 있었다.**

**원인 두 개**:

1. **꼬리 눈금 누락** — `normalize.py`의 정확일치 루프가 2만 배수에서만 보고했다.
   ```python
   if progress is not None and idx % 20000 == 0:   # 363,235 % 20000 != 0
   ```
   `enumerate`가 0부터라 마지막 3,235건은 **어떤 경우에도 보고되지 않는다.**
   이 단계는 우연이 아니라 **구조적으로 항상** `360,000/363,235`에서 끝난다.
   6단계 통합(`:201`)은 `i == total`을 함께 보는데 7단계만 빠져 있었다.

2. **가장 긴 구간이 보고를 안 함** — `_fuzzy_pass()`가 `progress` 인자를 아예 받지
   않았다. `should_stop`만 봤다. 실측 이번 구축에서:
   ```
   08:59:34  정확일치 후 314,015개        ← 15초
   09:08:29  퍼지 314,015 → 312,733       ← 8분 55초, 보고 0건
   ```
   화면이 얼어 있던 시간의 대부분이 여기다.

**수정**:
- 정확일치 루프: `done = idx + 1`로 세고 `done == len(entries)`면 반드시 보고.
- `_fuzzy_pass(..., progress)` — 1만 건마다 + 마지막에 `normalize:fuzzy` 단계로 보고.
- `KgExtractionPanel.tsx`에 `"normalize:fuzzy": "전역 정규화 · 유사 병합"` 라벨 추가.
- 회귀 테스트 2건: 총건수가 보고 주기의 배수가 아닐 때 마지막 눈금이 `done == total`인지,
  퍼지 단계가 진행률을 내는지.

**함께 드러난 것 — 퍼지 병합의 비용 대비 효과**:
```
314,015 → 312,733 (병합 1,282 = 0.41%)   8분 55초
```
구축 전체(16분)의 **절반 이상**을 써서 0.41%를 병합한다. CR-61 설계 때 측정한 0.5%와
일치한다. 끄면 구축이 절반으로 줄지만 지금은 사용자 판단 사항으로 남긴다 —
`kg.normalization.fuzzy_enabled: false`.

**교훈**:
- **주기적 진행률 보고에는 반드시 종료 눈금을 함께 둘 것.** `i % N == 0` 하나만 두면
  총건수가 N의 배수가 아닌 한 **항상** 끝을 못 찍는다. 이건 가끔 나는 버그가 아니라
  거의 항상 나는 버그인데, 마지막 몇 %라 그동안 아무도 신경 쓰지 않았을 뿐이다.
- **조용히 오래 도는 것은 죽은 것과 구분되지 않는다.** 파이프라인에서 가장 느린 구간이
  진행률을 안 내면, 사용자는 정상 동작을 장애로 인식하고 중단 버튼을 누른다.
  진행률은 **가장 느린 구간에 우선** 붙여야 한다 — 빠른 구간의 진행률은 어차피 안 보인다.
- 사고 6의 교훈("N건 처리 중은 진척이 아니다")의 거울상이다. 저기서는 **멈춘 것이
  움직이는 것처럼** 보였고, 여기서는 **움직이는 것이 멈춘 것처럼** 보였다.
  둘 다 원인은 같다 — 표시되는 숫자가 실제 진척과 연결되어 있지 않다.

---

### E-98: "구축 시작" 버튼이 24분을 돌고 Neo4j에 한 글자도 안 쓴 채 COMPLETED (CR-61, 2026-08-08)

**증상**: 구축이 `COMPLETED`로 끝났는데(1,435초) 그래프 화면 헤더는 그대로였다.
로그의 구축 결과와 화면 숫자가 어긋난다:
```
로그  : 과제 11,678 · 정규 엔티티 361,032
화면  : 과제 11,107 · 엔티티 348,673      ← 이전 구축 값
```
로그 흐름도 이상했다 — 8단계 완료(09:18:20) 직후 **1.4초 만에** 종료(09:18:21).
36만 건을 Neo4j에 넣는 데 1.4초일 리 없다.

**원인**: `kg_routes._graph_store_factory()`가 **언제나 `None`을 반환**했다.
```python
cfg = getattr(ctx, "config", None)                         # upstream Config — 존재한다
g = getattr(getattr(cfg, "app", None), "graphrag", None)   # cfg.app 이 없다 → None
```
`ctx.config`는 벤더링된 upstream `Config`이고 최상위 필드는
`system_config / character_config / live_config` **셋뿐이다.** 새싹이의 `app:` 블록은
별도 `AppConfig`로 파싱돼 **`ctx.app_config`** 에 실린다. `service_context.py:859`는
`getattr(app_config, "graphrag", None)`로 **맞게** 읽고 있었는데, 라우트만 자기 팩토리를
따로 만들면서 경로를 틀렸다.

`getattr(..., None)` 연쇄라 **AttributeError가 나지 않는다.** 잘못된 경로가 조용히
None으로 흘러 정상 분기처럼 보였다.

**증폭 요인 — 실패가 성공으로 보고됐다.**
```python
if dry_run or graph_store_factory is None:      # 두 경우를 같이 묶었다
    p.counts["note"] = "dry-run — Neo4j에 쓰지 않음"
...
p.state = "COMPLETED"
```
사용자가 선택한 미리보기와 설정 사고가 **같은 분기·같은 문구**로 처리됐고, 상태는
`COMPLETED`였다. 그래서 24분을 쓰고도 "잘 끝났다"로 보였다.
자동 연결 경로(추출 후 자동 구축)는 `self._graph_store_factory`를 써서 **정상 적재**됐기
때문에, 그래프에 데이터가 있다는 사실이 오히려 버그를 가려 줬다.

**수정 (세 겹)**:
- `kg_routes._graph_store_factory()` → `ctx.app_config.graphrag`. 못 찾으면 WARNING.
- `start_build()`: 팩토리를 안 받으면 **배선 때 받아 둔 `self._graph_store_factory`로
  폴백**한다 — 진실을 한 곳에 둔다.
- `start_build()`: `dry_run=False`인데 스토어가 없으면 **시작을 거부**한다.
  24분 뒤에 알려 주는 대신 누르는 즉시 알린다. 미리보기는 그대로 허용한다.
- `_run_build()`: "dry-run"과 "스토어 없음"을 다른 문구로 남기고, 후자는 ERROR 로그.

회귀 테스트 3건 — 스토어 없이 실구축은 거부되는지, 미리보기는 여전히 되는지(거부가
정상 경로를 막으면 안 된다), 배선된 팩토리로 폴백하는지.

**교훈**:
- **같은 설정을 두 곳에서 따로 읽지 말 것.** 배선(`service_context`)이 이미 올바르게
  읽어 서비스에 넘겨 준 값을, 라우트가 요청 객체에서 다시 유도하다 틀렸다.
  이미 주입된 의존성이 있으면 **다시 만들지 말고 그것을 쓴다.**
- **`getattr(x, "y", None)` 연쇄는 오타와 스키마 변경을 침묵으로 바꾼다.** 없으면 안 되는
  설정에는 기본값을 주지 말고 실패하게 두거나, 최소한 없을 때 로그를 남길 것.
- **"사용자가 고른 안전 모드"와 "고장 나서 아무것도 못 한 것"을 같은 분기로 묶지 말 것.**
  묶는 순간 고장이 정상 동작으로 보고된다. 이 사고에서 24분이 그렇게 날아갔다.
- E-97과 같은 날 같은 화면에서 나왔다. 둘 다 **표시되는 상태가 실제 상태와 연결되어
  있지 않은** 문제다 — 하나는 진행률이, 하나는 완료 판정이.

---

### E-99: 서식 라벨 `주관과제명`이 과제 2,251건의 노드 이름으로 남음 (E-93 미완, 2026-08-08)

**증상**: E-98 수정 후 그래프를 검증하다 발견. `(해당 시 작성)`은 0건으로 깨끗했는데
`주관과제명 무잔량 곡물건조기 개발` 같은 노드가 2,251건 있었다.

**원인**: E-93은 사용자가 화면에서 본 것(`(해당 시 작성)`)만 보고 고쳤다.
`strip_placeholder_prefix()`는 **안내문이 앞에 있을 때만** 그 뒤의 라벨을 뗐다:
```python
r"^...(?:해당\s*시\s*작성|내역사업명|...)...(?:(?:총괄\s*)?(?:연구개발)?(?:세부)?\s*(?:과제|사업)\s*명...)?"
```
`주관`이 목록에 없었고, 무엇보다 **라벨이 단독으로 앞에 붙는 경우**를 아예 안 봤다.
실측하니 그쪽이 훨씬 많다:
```
주관과제명 2,595 · 단위사업명 154 · 내역사업명 73 · 연구개발과제명 18 · 세부과제명 4 · 사업명 4
합계 2,848 / 12,070 (23.6%)
```

**수정**: 라벨 조각을 정규식 변수로 빼고 `(a) 안내문 [+ 라벨]` 또는 `(b) 라벨 단독`
두 갈래로 매치한다. 라벨 어휘에 `주관·위탁·협동·공동`을 추가.
떼고 나서 **아무것도 안 남으면 원본을 지킨다** — 제목이 라벨뿐인 경우의 판정은
`is_placeholder_title()` 몫이지 여기서 빈 문자열을 만들 일이 아니다.

읽기 시점(`projects.resolve_projects`) 폴백이라 **재추출 없이** 기존 데이터가 고쳐진다.

**검증(실데이터)**: 제목 12,070건 중 2,785건(23.1%) 변경, **6자 미만으로 쪼그라든 것 0건**.
회귀 테스트 3건 — 라벨 단독 제거, 내용어·문장 중간 라벨 **미**제거
(`벼 재배 사업명 개선` 그대로), 라벨뿐인 제목이 빈 문자열이 되지 않는지.

**교훈**:
- **사용자가 보고한 사례는 증상이지 범위가 아니다.** E-93에서 `(해당 시 작성)` 663건을
  고치며 "서식 문구 제목 합계 686/11,276(6.1%)"까지 셌는데, 그 집계 자체가
  안내문 어휘만 세고 **라벨 접두는 안 셌다.** 측정 범위가 수정 범위를 정해 버렸다.
  같은 클래스의 결함을 찾을 때는 **패턴을 넓혀 한 번 더 세어 볼 것.**
- 문자열 정제 규칙은 **긍정 사례만큼 부정 사례를 테스트할 것.** 과도한 제거는 못 고친
  것보다 나쁘다 — 제목이 사라지면 무슨 과제였는지 알 길이 없다.

---

### E-100: 코퍼스에서 빠진 문서가 그래프에 영구히 남음 — 세대 정리 목록에 Document 누락 (CR-61, 2026-08-08)

**증상**: E-98 적재 후 검증하다 Neo4j의 Document가 12,071건인데 SQLite는 12,070건.
차이 1건은 `embed_test.md_ebe7f0cb` — 예전 임베딩 테스트 파일이었다. 원본 파일도,
RAG 등록 문서 목록에도, 후보 저장소에도 없었다. **Neo4j에만 관계 0건짜리 껍데기**로
남아 재구축을 여러 번 지나도 살아 있었다.

**원인**: `neo4j_load.load_graph()`의 세대 정리가 라벨 넷만 지웠다.
```python
graph.delete_stale(bid, ("CanonicalEntity", "Mention", "Chunk", "Project"))
```
`Document`가 없다. 문서 노드는 `doc_id`가 안정적이라 **해시 변경으로 고아가 되지 않으므로**
정리 대상에서 빠진 것으로 보이는데, 그건 고아가 되는 경로 하나만 본 것이다.
**코퍼스에서 문서가 빠지는 경로**(삭제·이동·테스트 파일 정리)는 막지 못한다.

**수정**: 목록에 `"Document"` 추가. 안전한 이유를 주석으로 남겼다 — 문서 적재는 폴더
범위와 무관하게 `documents` 전체를 매번 올리며 현재 `build_id`를 찍으므로, 여기서
지워지는 것은 **SQLite에 더 이상 없는** 문서뿐이다. 같은 성질인 `Chunk`는 이미 목록에 있었다.

남아 있던 1건은 관계 0건·`build_id IS NULL`을 조건에 넣어 직접 지웠다
(12,071 → 12,070). 회귀 테스트 1건.

**교훈**:
- **정리 대상 목록은 "왜 고아가 되는가"가 아니라 "무엇이 사라질 수 있는가"로 정할 것.**
  이 목록은 `canonical_id` 해시 변경이라는 **한 가지 경로**를 기준으로 만들어졌고,
  그 경로를 안 타는 라벨이 조용히 빠졌다. 원본에서 사라질 수 있는 것은 전부 대상이다.
- SQLite(진실의 원본)와 Neo4j(투영)의 **건수를 대조하는 확인**이 이걸 잡았다.
  투영을 쓰는 시스템에서는 양쪽 건수 일치를 정기적으로 볼 값어치가 있다.
- E-97~E-100이 하루에 나왔고 전부 CR-61 구간이다. 공통점은 **끝을 확인하지 않은 것** —
  마지막 진행률 눈금(E-97), 적재 완료 판정(E-98), 제목 정제 범위(E-99), 정리 대상
  목록(E-100). 파이프라인은 중간이 아니라 **경계에서** 샌다.

---

### E-101: "근거 그래프" 버튼이 아무 일도 안 함 — 근거 노드가 개요에 없다 (E-93 미완, 2026-08-08)

**증상**: 사용자 지적 — "질문이 끝난 다음에 참조문서 아래 근거그래프를 누르면 참조한
문서가 핀이 꼽히면서 보여줘야 할 것 같은데 그게 잘 안되고있어."

**원인 두 겹.**

**(1) 문서 노드 id 체계가 서로 다르다 (E-93의 미완).**
```
개요  : 'doc:(1-2-1 NBT) 고효율…pdf_3' · 'pj:PJ015143'   ← Project.project_id
근거  : 'TRKO202500017741_….pdf_afbc4f63'                ← raw doc_id
```
E-93에서 문서 열기 404를 고치며 개요 노드 `id`를 `project_id`로 두고 `doc_id`를 별도
필드로 뺐는데, **근거 payload는 손대지 않았다.** 근거 노드는 `doc_id` 필드조차 없어
우회 경로도 없었다. 게다가 `label = doc_id`라 노드 이름이 파일명 그대로 그려졌다.

**(2) 더 근본 — 개요는 표본이다.** 프론트는 개요를 그린 뒤 근거 노드를 하이라이트하려
했는데, 개요는 엔티티 361,032개 중 **500개를 뽑은 표본**이다. 실측(`limit=500`, 노드 1,874):
```
근거 엔티티 52건 → 개요에 존재 0건      ← id 체계가 같은데도 0건
근거 문서 42건   → id 일치 0건 / doc_id 일치 4건
```
엔티티는 양쪽 다 `ce_...`로 규약이 같은데도 0건이다. **id를 맞춰도 42건 중 4건만
살아난다** — 하이라이트라는 접근 자체가 틀렸다.

**수정**:
- `KgGraphStore.evidence_snapshot()` 신설 — 개요(`snapshot`)와 **같은 Cypher 형태**로
  문서 노드를 만든다(id=project_id, label=과제 제목, doc_id=대표 문서). 두 곳이 갈라지면
  또 어긋나므로 쿼리를 의도적으로 닮게 뒀다.
- `evidence_payload(..., graph=)` — 그래프가 있으면 위 스냅샷을, 없으면 예전 형태로
  폴백(근거가 아예 안 뜨는 것보다 낫다). 폴백에도 `doc_id`를 채운다.
- `GraphNode.doc_id` 추가, `latest_evidence` 라우트가 옮긴다.
- **프론트: 근거 모드에서 근거 서브그래프 자체를 그린다.** 개요를 하이라이트하지 않는다.
  그러면 모든 근거 노드가 화면에 존재하므로 핀이 꽂힌다.
- 근거 모드 진입 시 **참조 문서에 자동으로 핀**을 꽂는다(사용자가 기대한 동작).
  레이아웃이 좌표를 잡은 뒤여야 하므로 한 틱 늦게 실행한다.
- 조립 실패 로그를 `debug` → `warning`으로. 기능이 통째로 죽는데 로그가 안 보였다.
  성공 시에도 노드·문서 수를 INFO로 남긴다.

회귀 테스트 5건 — 그래프 있을 때 project_id 규약, 폴백, 빈 스냅샷 시 폴백,
엣지 양 끝이 노드 목록에 존재, chunk_ids 보존.

**교훈**:
- **id 규약을 바꿨으면 그 id를 쓰는 곳을 전부 세어 볼 것.** E-93에서 개요만 고치고
  근거·doc-focus를 확인하지 않았다. "같은 값을 만드는 두 번째 코드 경로"는 규약 변경 때
  거의 항상 잊힌다 — E-98(팩토리 두 곳)과 같은 실패다.
- **표본 위에서 하이라이트하는 설계는 원본이 커지면 조용히 죽는다.** 그래프가 1,000개일
  때는 우연히 맞았을 것이다. 37만 개가 되니 확률이 0으로 갔다. 데이터 규모가 바뀌면
  "맞는지"가 아니라 "확률이 얼마인지"를 물어야 한다.
- 사용자 표현("핀이 꼽히면서 보여줘야")이 **기대 동작의 명세**였다. 하이라이트가 아니라
  핀이라고 말한 것을 그대로 구현했다.

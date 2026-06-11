# FRONTEND_CONSTRAINTS.md — 프론트엔드 핵심 제약사항

이 문서는 Electron 투명창 특성상 절대 위반해서는 안 되는 제약을 정의한다.
새 기능 개발 전 반드시 읽을 것.

---

## 1. Click-through 메커니즘

### 정상 동작
- `setIgnoreMouseEvents(true, {forward:true})` — 마우스 이벤트 무시 (클릭은 바탕화면으로 통과), mousemove만 전달
- **macOS 포함 모든 플랫폼에서 `ignore=true` 시 반드시 `{forward:true}` 사용할 것**
  - `{forward:true}` 없으면 macOS에서 mousemove가 렌더러에 전달되지 않아 `evaluate()`가 실행되지 않음
  - 결과: 창이 영구 클릭스루 상태로 고착 → 입력창 사용 불가 (ERROR_HISTORY E-09 참조)
- `clickthrough.ts`의 `evaluate()` — `elementFromPoint`로 UI 요소 위에 있을 때만 `setIgnoreMouseEvents(false)` 호출
- 따라서 캐릭터/채팅 패널 위에서만 클릭 가능, 투명 영역은 바탕화면 클릭 통과

### dragLock
- `setDragLock(true)` → `setIgnoreMouseEvents(false)` 강제 (mousemove 체크 없이)
- **오직 드래그 중에만 사용할 것** — 마우스 버튼 누른 직후, 뗄 때 해제
- 드래그 외 목적으로 사용 절대 금지 (바탕화면 전체 차단됨)

### OS 파일 드래그 제약 (해결 불가)
- Finder/탐색기에서 파일을 드래그할 때는 `mousemove` 이벤트가 전달되지 않음
- `forward:true`는 오직 실제 마우스 이동만 전달; OS 드래그 프로토콜(NSDraggingDestination)은 별개
- 따라서 Electron 투명창으로 OS 파일 드래그 수신은 근본적으로 불가능
- `setDragLock(chatOpen)` 같은 "패널 열림 = setIgnoreMouseEvents(false)" 패턴은 **절대 금지**
  - chatOpen 동안 화면 전체가 차단 → Finder 클릭이 우리 창에서 mousedown으로 발화 → 패널 닫힘 회귀
  - ERROR_HISTORY E-01, E-08 참조
- **대안**: 파일 선택 버튼(`<input type="file">`) 사용. 드롭존 UI는 남길 수 있으나 Finder 드래그는 동작하지 않음을 인지할 것

---

## 2. pointer-events: none

- `#root`, 최상위 `<div>` — `pointer-events: none` 필수
- 이를 제거하면 투명 영역이 마우스 이벤트를 흡수 → click-through 불가
- 실제 상호작용이 필요한 요소에만 `pointer-events: auto` 적용

---

## 3. 외부 클릭으로 채팅 패널 닫기

- `App.tsx`의 mousedown 핸들러 — 패널/캐릭터 외 클릭 시 패널 닫음
- `target === document.body || target === documentElement` 제외 로직은 추가하지 말 것
  - click-through 상태에서는 body/documentElement가 target이 될 수 없음 (클릭이 아예 안 들어옴)
  - dragLock 상태에서 예외 처리 목적으로 추가했다가 패널 닫기 버그 유발

---

## 4. TTS / 오디오

- Electron `autoplay-policy: no-user-gesture-required` 설정되어 있음 → AudioContext 자동재생 가능
- MeloTTS 모델은 `assets/models/melotts-ko/`에 있어야 하지만 없으면 HF 캐시 사용
- 백엔드 TTS 초기화는 포트 오픈 후 ~8초 추가 소요 → /api/tts/speak 503 시 재시도 필요

---

## 5. Electron 앱 실행 명령

```bash
# 개발 서버 (Electron)
npm run electron:dev

# 절대 금지: 브라우저에서 테스트
npm run dev  # 이 명령은 브라우저에서 열림
```

---

## 6. Electron 미지원 브라우저 API

- **`window.prompt()` 절대 금지** — Electron은 prompt()를 지원하지 않고 예외(`prompt() is and will not be supported`)를 던진다. try 밖에서 호출하면 핸들러가 조용히 죽어 "버튼을 눌러도 아무 일도 안 일어나는" 버그가 된다 (ERROR_HISTORY E-34: 폴더 일괄삭제 무반응).
- `window.confirm()` / `window.alert()`은 지원된다 — 단순 확인은 confirm으로, 텍스트 입력이 필요하면 인앱 모달 컴포넌트로 구현할 것.
- API 경로에 사용자 유래 문자열(파일명 기반 doc_id 등)을 넣을 땐 반드시 `encodeURIComponent` (E-33: `#` 포함 파일명이 URL fragment로 잘림).
- **키보드 입력을 받는 모든 `<input>`/`<textarea>`에 `onClick={() => window.electronAPI?.restoreFocus()}` 필수** — pet 모드에서 창이 `setFocusable(false)`라 DOM focus만으로는 타이핑이 안 된다. 코드로 `.focus()`를 호출하는 useEffect에서도 직전에 `restoreFocus()` 호출. 누락 감사: 컴포넌트별 `grep -c "<input\|<textarea"` vs `grep -c restoreFocus` 비교 (ERROR_HISTORY E-38).

# M_13 desktop_mascot SPEC (Rev 2)

## 목적

데스크톱 위에 떠다니는 새싹이 캐릭터(드래그 가능, 클릭 시 ChatPanel 노출)를
구현한다. 캐릭터·패널이 차지하지 않은 화면 영역은 항상 다른 앱이 정상적으로
사용 가능해야 한다(클릭, 드래그, 스크롤 모두 통과).

캐릭터는 **멀티 모니터 가상 데스크탑 전체를 자유롭게 이동**할 수 있다.
어느 모니터에든 위치할 수 있으나 동시에 두 곳에 존재하지 않는다(단일 인스턴스).

## 요구사항 연결

- REQUIREMENTS.md §UI/Frontend — "데스크톱 마스코트 + 채팅 패널 (Shimeji 유사)".
- REQUIREMENTS.md §Cross-Platform — macOS 14+ (개발), Windows 10/11 (배포).
- 본 스펙은 두 차례 실패한 시도(전체화면 click-through 미작동, 130×130
  small window 트랩)에 대한 교정 설계다.

---

## 1. 아키텍처 결정

### 1.1 윈도우 전략: 단일 가상 데스크탑 스패닝 투명 윈도우 + 동적 hit-testing

| 항목 | 선택 | 근거 |
|---|---|---|
| 윈도우 개수 | 1개 | 패널 열기 시 리사이즈/이동 불필요. Attempt 2의 "트랩" 문제 원천 차단. |
| 크기·위치 | `screen.getAllDisplays()`로 모든 디스플레이의 가상 좌표 union 계산 | 캐릭터가 어느 모니터로든 드래그 가능. primary만 덮으면 보조 모니터로 이동 불가. |
| 투명 | `transparent: true`, `frame: false`, `hasShadow: false`, `backgroundColor: '#00000000'` | 배경 픽셀이 합성 시 무시. |
| Click-through 토글 | `setIgnoreMouseEvents(ignore, { forward: true })` 동적 토글 | `forward: true`는 **Windows에서 필수** — ignore=true 상태에서도 mousemove가 렌더러에 전달됨. macOS는 ignore=true일 때 mousemove가 렌더러에 **자동으로 전달되므로** forward 옵션이 무시되지만 무해하다. 양쪽 모두 동일 코드 사용 가능. |
| alwaysOnTop | macOS: `setAlwaysOnTop(true, "floating")` Windows: `setAlwaysOnTop(true, "screen-saver")` | macOS의 `floating` 레벨은 일반 앱 윈도우 위, 시스템 UI 아래. |
| 모든 워크스페이스 표시 | macOS: `setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })` | 다른 앱이 풀스크린 모드로 진입해도 캐릭터가 사라지지 않음. 화면 공유·발표 중 노출이 우려될 경우 사용자가 트레이에서 숨길 수 있음. |
| skipTaskbar | true | Dock/Taskbar에 아이콘 미노출(트레이 전용 앱). macOS Dock 숨기기는 `app.dock.hide()` 별도 필요. |
| focusable | true | ChatPanel input 포커스 필수. |

#### 1.1.1 가상 데스크탑 bounds 계산 (필수 알고리즘)

```ts
function getVirtualDesktopBounds(): { x: number; y: number; width: number; height: number } {
  const displays = screen.getAllDisplays();
  const minX = Math.min(...displays.map(d => d.bounds.x));
  const minY = Math.min(...displays.map(d => d.bounds.y));
  const maxX = Math.max(...displays.map(d => d.bounds.x + d.bounds.width));
  const maxY = Math.max(...displays.map(d => d.bounds.y + d.bounds.height));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}
```

이 값을 BrowserWindow `{ x, y, width, height }`와 `win.setBounds()` 양쪽에 사용한다.
렌더러에서 바라보는 좌표계는 항상 (0, 0) 기준 (뷰포트 기준)이므로, 캐릭터 position은
클라이언트 좌표 `[0, virtualWidth] × [0, virtualHeight]` 범위 내에서 저장한다.

#### 1.1.2 채택하지 않은 대안

- **Attempt 1 (전체화면 + ignoreMouseEvents 정적)**: forward:true 없이는 ignore 상태에서
  윈도우가 mousemove를 받지 못해 hit-test 트리거가 사라진다.
- **Attempt 2 (small window 동적 리사이즈)**: 패널 열기 시 윈도우가 700×800으로 커지며
  그 안의 모든 픽셀이 이벤트를 가로챈다. 영구 폐기.
- **display별 다중 윈도우**: 모니터 경계에서 핸드오프 로직이 복잡하고 깜빡임 발생. 폐기.

---

## 2. main.ts 설계

### 2.1 BrowserWindow 생성 파라미터

```ts
const vd = getVirtualDesktopBounds();

new BrowserWindow({
  x: vd.x,
  y: vd.y,
  width: vd.width,
  height: vd.height,
  transparent: true,
  frame: false,
  hasShadow: false,
  backgroundColor: '#00000000',
  alwaysOnTop: true,
  skipTaskbar: true,
  resizable: false,
  movable: false,
  minimizable: false,
  maximizable: false,
  fullscreenable: false,
  focusable: true,
  acceptFirstMouse: true,
  webPreferences: {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: false,          // preload에서 require('electron') 접근 필요
    preload: join(__dirname, 'preload.cjs'),  // CommonJS preload — ESM 모호성 방지
  },
});
```

생성 직후:
- macOS: `win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })`
- macOS: `win.setAlwaysOnTop(true, 'floating')`
- Windows: `win.setAlwaysOnTop(true, 'screen-saver')`
- 공통: `win.setIgnoreMouseEvents(true, { forward: true })` — 초기 상태 click-through ON.

### 2.2 IPC 핸들러

| 채널 | 방향 | 페이로드 | 책임 |
|---|---|---|---|
| `mascot:set-ignore-mouse` | renderer → main (send) | `{ ignore: boolean }` | `win.setIgnoreMouseEvents(ignore, { forward: true })`. **본 스펙의 핵심 채널.** |
| `mascot:get-display` | renderer → main (invoke) | — | 가상 데스크탑 `{ width, height }` 반환 (클라이언트 좌표 기준, x=0, y=0 고정). scaleFactor는 primary display 기준. |
| `mascot:quit` | renderer → main (send) | — | `app.quit()`. |
| `mascot:open-devtools` | renderer → main (send) | — | `win.webContents.openDevTools({ mode: 'detach' })`. dev 전용. |
| `display-changed` | main → renderer (send) | `{ width, height }` | 디스플레이 변경 시 새 가상 데스크탑 크기 전달. |
| `tray:open-chat` | main → renderer (send) | — | 트레이 "채팅 열기" 선택 시 ChatPanel 오픈 지시. |

### 2.3 라이프사이클

1. `app.requestSingleInstanceLock()` — 두 번째 실행 시도 시 즉시 종료. `second-instance`
   이벤트에서 기존 윈도우 `focus()`.
2. `app.whenReady` → `createWindow()` → `createTray()`.
3. `app.on('window-all-closed')`: 트레이가 살아있으므로 quit 안 함.
4. `app.on('before-quit')`: `win = null` 정리.
5. `screen.on('display-metrics-changed' | 'display-added' | 'display-removed')`:
   가상 데스크탑 재계산 → `win.setBounds(newVd)` + `display-changed` IPC 송신.

### 2.4 트레이

- 아이콘: 앱 번들 내 캐릭터 PNG(`neutral.png`)를 `nativeImage.createFromPath()`로 로드 후
  `img.resize({ width: 18, height: 18 })`(macOS) / `width: 32`(Windows).
  - macOS: 18×18 px. Template Image 형식 권장이나, 컬러 PNG도 허용 (자동 흑백 미적용).
  - Windows: 32×32 px ICO 대신 PNG 허용 (Windows 10+ 지원).
  - 로드 실패 시 인라인 base64 fallback (8×8 단색 PNG) — tray 항목이 반드시 보여야 함.
- 메뉴: `채팅 열기`, `새싹이 보이기/숨기기`, `종료`.
- `채팅 열기` → `win.show()` + `win.webContents.send('tray:open-chat')`.
- `보이기/숨기기` → `win.show()/hide()` (bounds 변경 없음).

---

## 3. preload 설계

**파일명**: `electron/preload.cjs` (CommonJS — `"type": "module"` 패키지에서 ESM·CJS 모호성 방지).
`tsconfig.electron.json`에서 preload.ts 제외; preload.cjs는 직접 유지보수.

`contextBridge.exposeInMainWorld('electronAPI', { ... })` 로 다음만 노출:

```ts
interface ElectronAPI {
  readonly isElectron: true;
  setIgnoreMouseEvents(ignore: boolean): void;
  getDisplay(): Promise<{ width: number; height: number; scaleFactor: number }>;
  quit(): void;
  openDevTools(): void;
  onDisplayChanged(cb: (size: { width: number; height: number }) => void): () => void;
  onOpenChat(cb: () => void): () => void;  // 트레이 "채팅 열기" → ChatPanel 오픈
}
declare global { interface Window { electronAPI?: ElectronAPI } }
```

보안 정책:
- `contextIsolation: true` — renderer와 preload 컨텍스트 분리.
- `nodeIntegration: false` — renderer에서 Node.js API 직접 접근 금지.
- `sandbox: false` — preload에서 `require('electron')` 접근 필요이므로 sandbox 해제. preload가 최소 API만 노출하므로 attack surface는 제한적.
- **노출 금지**: `ipcRenderer` 자체, `require`, `process`, fs 관련 어떤 것도.
- `openDevTools`는 `dev 빌드에서만` 렌더러에서 호출 허용 (production에서는 트레이 메뉴 dev 항목으로만).

---

## 4. clickthrough 서비스 설계

### 4.1 위치

`src/services/clickthrough.ts`.

### 4.2 알고리즘

```
init(opts?: { throttleMs?: number }) -> ClickthroughHandle

규칙:
1. window.addEventListener('mousemove', handler, { passive: true }).
2. handler는 requestAnimationFrame throttle로 직렬화 — 마지막 좌표만 평가.
3. 매 평가에서:
   const el = document.elementFromPoint(e.clientX, e.clientY);
   const interactive = el !== null
     && el !== document.body
     && el !== document.documentElement;
   const nextIgnore = !interactive;
4. nextIgnore가 직전 상태와 다를 때만 IPC send (상태 변화 없으면 noop).
5. 초기 상태는 ignore=true (main.ts와 동일).
6. dispose() 호출 시 listener 제거 + ignore=true 송신.
```

### 4.3 픽셀 단위 정밀도

- `html, body { background: transparent !important }` CSS reset 필수.
- `#root { pointer-events: none }` — 투명 영역이 `elementFromPoint`에서 body/html을
  반환하도록 보장. 실제 인터랙티브 요소(CharacterWidget, ChatPanel)는 `pointer-events: auto`.

### 4.4 엣지 케이스

| 상황 | 처리 |
|---|---|
| 드래그 중 커서가 캐릭터 밖으로 빠짐 | `mousedown`~`mouseup` 구간 `dragLocked=true` → evaluate 시 ignore=false 강제. |
| drag lock 해제 (`mouseup`) | **즉시 마지막 커서 좌표로 hit-test 재평가** 후 그 결과로 ignore 결정. "무조건 true"가 아님 — 커서가 여전히 캐릭터 위라면 ignore=false 유지. |
| ChatPanel 외부 클릭 | document `mousedown` listener로 패널 영역 밖 시 `setChatOpen(false)`. overlay div 사용 금지 — click-through를 깨뜨림. |
| 모니터 해상도·배치 변경 | `display-changed` IPC 수신 시 `screenSize` 상태 갱신 → 다음 mousemove에서 자연 동작. |
| `electronAPI` 미정의 (브라우저 모드) | 초기화 skip, no-op handle 반환. CharacterWidget은 일반 SPA처럼 동작. |

---

## 5. CharacterWidget 설계

### 5.1 좌표계

- 풀스크린(가상 데스크탑) 윈도우이므로 `position.x`, `position.y`를 CSS `left`/`top`에 직접 사용.
- Clamp 범위:
  - Electron 모드: `getDisplay()` 반환값 `{ width, height }` — 가상 데스크탑 크기.
  - 브라우저 모드: `window.screen.width/height` 폴백.
- `getDisplay()`는 mount 시 1회 호출, `onDisplayChanged()` 구독으로 갱신.

### 5.2 드래그

- `useDraggable` hook: `DRAG_THRESHOLD_PX=5`.
- `mousedown` → `clickthrough.setDragLock(true)` + `setChatOpen(false)`.
- `mouseup` → `clickthrough.setDragLock(false)`.
- 클릭(임계 미만 이동) → `toggleChat()`.

### 5.3 위치 영속성

- zustand `position` + `localStorage('saessagi_char_pos')`.
- 부팅 시 복원. 복원 좌표가 가상 데스크탑 범위 밖이면 우하단 기본값으로 초기화.

### 5.4 캐릭터 이미지

- `imageSrc = ${import.meta.env.BASE_URL}avatars/${emotion}.png`
- 로드 실패 시 `neutral.png` 폴백.
- `sad.png`의 불투명 검은 배경은 별도 작업(자산 정규화)으로 처리 — 본 스펙 범위 외.

---

## 6. 이미지 서빙

### 6.1 원본 → 빌드 경로

- 원본: `assets/character/saessagi/*.png` (9개).
- 대상: `web/public/avatars/*.png` — Vite가 `dist/avatars/`로 복사.

### 6.2 자동화

- `web/scripts/sync-character-assets.sh` — `rsync` 또는 `cp -R`.
- npm `predev`, `prebuild` hook에 등록.

### 6.3 file:// 프로토콜

- Electron 프로덕션: `loadFile('dist/index.html')`.
- `base: process.env.ELECTRON_BUILD === '1' ? './' : '/'` — `import.meta.env.BASE_URL`로
  이미지 경로 구성.

---

## 7. Dev vs Prod

| 항목 | Dev | Prod |
|---|---|---|
| 진입 | `npm run electron:dev` → vite(5173) + electron | `electron:build` → electron-builder |
| 로드 | `loadURL('http://localhost:5173')` | `loadFile('dist/index.html')` |
| DevTools | 자동 open (detach 모드) | 트레이 개발자 메뉴에서만 |

---

## 8. 플랫폼 노트

### 8.1 macOS

- `setAlwaysOnTop(true, 'floating')` — 일반 앱 윈도우 위, 시스템 UI 아래.
- `setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })` — Mission Control·전체화면 앱에서도 표시.
- `app.dock.hide()` — Dock 아이콘 미노출.
- **click-through 동작**: macOS에서 `setIgnoreMouseEvents(true)` 시 mousemove는 자동으로 렌더러에 전달됨. `{ forward: true }` 옵션은 무시되지만 무해.
- Stage Manager 사용 시 윈도우 그룹 포함 가능 → RISKS.

### 8.2 Windows

- `setAlwaysOnTop(true, 'screen-saver')`.
- `{ forward: true }` — Windows에서 ignore 상태에서의 mousemove 전달에 필수.
- DWM 합성 비활성(클래식 테마): 투명 합성 실패 가능. Windows 10/11은 DWM 강제이므로 사실상 문제없음.
- RDP 세션: 투명 합성 실패 가능 — RISKS.

---

## 9. 에러 처리

| 상황 | 동작 |
|---|---|
| `electronAPI` 미정의 | clickthrough skip. 브라우저 SPA 모드로 동작. |
| `setIgnoreMouseEvents` IPC 실패 | 다음 mousemove에서 자가 회복. |
| 캐릭터 이미지 로드 실패 | neutral.png 폴백 → 실패 시 빈 div (드래그는 가능). |
| `getDisplay` reject | `window.screen.width/height` 폴백. |
| 가상 데스크탑 bounds 계산 실패 | primary display bounds로 폴백. |

---

## 10. 성능 요구사항

| 지표 | 목표 | 측정 방법 |
|---|---|---|
| mousemove 평가 | < 1ms/회 | DevTools Performance 패널, CPU throttle 4x |
| IPC `set-ignore-mouse` 빈도 | 상태 변화 시에만 (idle: 0회/초) | 메인 프로세스 ipcMain 카운터 로그 |
| 메인 프로세스 RSS | < 80MB (idle 30초 후) | `process.memoryUsage().rss` in main, 30s 후 측정 |
| 렌더러 JS heap | < 100MB (idle) | Chrome DevTools Memory 탭 |
| 시작 시간 | < 3초 (cold start, SSD 기준) | 수동 스톱워치 |

---

## 11. 테스트 케이스

### 정상 (≥5)

1. 앱 시작 → 캐릭터 우하단 기본 위치 표시 + 데스크톱 다른 영역 클릭 가능.
2. 캐릭터 드래그 → 위치 이동, mouseup 후 유지, 재시작 후 복원.
3. 캐릭터 클릭(드래그 임계 미만) → ChatPanel 열림.
4. ChatPanel 외부(투명 영역) 클릭 → 패널 닫힘.
5. ChatPanel 입력창에 한글 IME 입력 → 정상 입력.

### 엣지 (≥5)

1. 화면 좌상단 (0,0) 드래그 → clamp 동작.
2. 화면 우하단 끝 드래그 → `virtualW/H - CHAR_SIZE` 까지만 이동.
3. 보조 모니터로 드래그 → 모니터 경계 통과 후 보조 모니터에 위치.
4. 캐릭터와 ChatPanel 겹침 → 두 영역 모두 인터랙티브.
5. 빠른 마우스 이동 → 마지막 좌표만 평가 (rAF 스로틀).
6. 디스플레이 연결·해제 이벤트 → 가상 데스크탑 재계산, 캐릭터 clamp 갱신.

### 적대적 (≥3)

1. `setIgnoreMouseEvents` 1초 100번 강제 → 상태 변화 시에만 IPC 송신, 큐 적체 없음.
2. preload 미주입 환경 → `electronAPI` undefined, 브라우저 모드 동작, console error 없음.
3. 드래그 중 `display-removed` 이벤트 → 가상 데스크탑 재계산 후 캐릭터 clamp.

---

## 12. Definition of Done

- [ ] 가상 데스크탑 스패닝 투명 윈도우가 macOS / Windows에서 검은 박스 없이 합성됨.
- [ ] 캐릭터 외부 영역 모든 픽셀에서 데스크톱/타 앱 클릭 통과 확인 (수동 QA).
- [ ] 캐릭터를 보조 모니터로 드래그·이동 가능 (수동 QA).
- [ ] `setIgnoreMouseEvents`는 항상 `{ forward: true }`로 호출.
- [ ] drag lock 해제 시 마지막 커서 좌표로 즉시 hit-test 재평가.
- [ ] 9개 캐릭터 PNG가 `web/public/avatars/`에 동기화, 빌드 산출물에 포함.
- [ ] dev/prod 모두 이미지 경로 정상 (vite `base` 설정).
- [ ] 트레이 아이콘 가시적 (macOS·Windows), 종료 가능, 단일 인스턴스 보장.
- [ ] `npm run test` (vitest) 통과 — 정상 ≥5, 엣지 ≥5, 적대적 ≥3.
- [ ] `tsc --noEmit` + `vite build` 통과.
- [ ] `reviews/M_13_*.md`에 Critic PASS 기록.
- [ ] `docs/MODULES.md` M_13 `✅ DONE` 갱신.

---

## 13. 의존성

- electron 30.x, React 18, Vite 5, Zustand (기존)
- 신규 npm 패키지 없음.

---

## 14. 스펙 외 사항 (명시적 제외)

- 캐릭터 애니메이션/Live2D — 별도 모듈.
- `sad.png` 검은 배경 알파 처리 — 자산 정규화 별도 작업.
- 자동 시작(autostart) — 배포 모듈(M_12).
- Linux — 요구사항 외.
- 접근성(키보드·스크린 리더) — CHANGE_REQUEST로 별도 검토.
- 음성 입출력, TTS, ASR — 기존 ChatPanel 위임.

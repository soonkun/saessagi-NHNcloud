# CLAUDE.md — 프로젝트 인덱스

사내 오프라인 AI 비서 (Electron + Python 백엔드). upstream: `Open-LLM-VTuber`.

---

## 세션 시작 시 읽을 문서

| 상황 | 읽을 문서 |
|------|-----------|
| 신규 기능 기획·설계 | `REQUIREMENTS.md` → `docs/ARCHITECTURE.md` → `docs/MODULES.md` |
| 버그 수정 | `docs/ERROR_HISTORY.md` (과거 실수 확인) → 해당 소스 파일 |
| 프론트엔드(Electron/React) 작업 | `docs/FRONTEND_CONSTRAINTS.md` 먼저 읽을 것 |
| 모듈 구현 진행상황 확인 | `docs/MODULES.md` |
| 전체 계획·단계 확인 | `PROJECT_PLAN.md` |

---

## 절대 규칙

- **구현된 척 속이는 짓 금지.** 기능이 실제로 동작하는지 직접 데이터로 확인하기 전까지 "작동한다"고 보고하지 말 것. 특히 RAG·TTS·STT 등 외부 의존성 있는 기능은 반드시 실제 데이터 흐름(벡터 스토어 조회 결과, 오디오 payload, 로그)을 확인 후 보고.
- **AI 모델의 자체 지식을 RAG 결과로 착각하지 말 것.** 벡터 스토어가 비어 있어도 LLM이 정답을 맞힐 수 있다. RAG 작동 여부는 반드시 (1) 벡터 스토어에 실제 문서가 있는지, (2) 쿼리 시 hit가 반환되는지, (3) 로그에 "Proactive RAG: N건 주입" 같은 실제 주입 기록이 있는지로만 판단할 것.
- **specs/M_NN_SPEC.md 없이 src/ 파일 생성 금지.**
- **REQUIREMENTS.md에 없는 기능 추가 금지.** 필요 시 `docs/CHANGE_REQUESTS.md` 작성 후 사용자 승인 먼저.
- **외부 네트워크 호출 금지.** fetch/requests는 127.0.0.1·localhost·사내 IP만 허용.
- **변경 전 반드시 관련 소스 읽기.** 코드 동작 이해 없이 수정하면 회귀 발생.
- **버그를 발견하고 수정했으면 반드시 `docs/ERROR_HISTORY.md`에 기록할 것.** 증상·원인·수정 내용·교훈을 빠짐없이 작성. 같은 실수가 반복되지 않도록 하는 것이 목적이며, 기록 없이 수정만 하는 것은 금지.
- **프론트엔드 작업 전 `docs/FRONTEND_CONSTRAINTS.md` 확인.**
- **백엔드 변경 후 반드시 프론트엔드까지 E2E 검증할 것.** 백엔드 테스트만으로 완료 보고 금지.
  - E2E 검증은 WebSocket 테스트 스크립트(`/tmp/test_rag2.py` 등)로 수행. 백엔드 응답 텍스트·오디오 payload 수신 확인.
  - **`open http://127.0.0.1:12393` 절대 금지.** 브라우저에서 앱을 열면 새싹이 캐릭터가 일반 브라우저 창에 갇히는 심각한 UX 문제 발생. Electron 앱 전용 UI이며 브라우저에서 실행되어서는 안 됨.

---

## 반복 발생 사고 — 반드시 읽고 같은 실수 금지

### [사고 1] web/dist 재빌드 시 흰 화면 (E-22, 2026-04-26)

**무슨 일이 일어났나**: 프론트엔드 작업 중 `web/dist/`를 수동으로 `npm run build`로 재빌드했다. `ELECTRON_BUILD=1`을 빠뜨렸기 때문에 Vite가 `base: "/"` (기본값)로 빌드했고, `web/dist/index.html`의 script 경로가 `/assets/...` (절대 경로)로 생성됐다. Electron은 `file://` 프로토콜로 이 파일을 로드하므로 `/assets/...`는 파일 시스템 루트를 가리켜 JS를 찾을 수 없었고, React가 마운트되지 않아 흰 화면만 표시됐다.

**올바른 web/dist 빌드 명령**:
```bash
cd web && ELECTRON_BUILD=1 npm run build
```

**빌드 후 검증 필수**: `web/dist/index.html`을 열어 script src가 `./assets/...` (상대 경로)인지 반드시 확인. `/assets/...` (절대 경로)면 빌드가 잘못된 것이다.

**`새싹이.command`의 자동 빌드는 이미 `ELECTRON_BUILD=1`을 설정**하고 있으나, Claude Code가 수동으로 빌드할 때는 반드시 직접 설정해야 한다.

---

### [사고 2] window-manager.ts 수정 시 pet mode 회귀 (E-21 관련, 2026-04-26)

**무슨 일이 일어났나**: click-through 버그를 수정하면서 `window-manager.ts`의 `setIgnoreMouseEvents()` 메서드 안에 `setFocusable(true)`를 추가했다. `continueSetWindowModePet()`이 명시적으로 `setFocusable(false)`를 설정하는데, `setIgnoreMouseEvents(false)` 경로에서 이를 덮어써버려 pet mode가 깨졌다.

**규칙**: `window-manager.ts`를 수정하기 전에 반드시 `docs/FRONTEND_CONSTRAINTS.md`와 `docs/ERROR_HISTORY.md`를 읽을 것. `continueSetWindowModePet()`의 `setFocusable(false)`는 절대 다른 경로에서 `setFocusable(true)`로 덮어쓰면 안 된다. `setIgnoreMouseEvents` 관련 로직을 수정할 때는 pet mode 전환 흐름 전체(setWindowModePet → continueSetWindowModePet)를 먼저 추적한 뒤 손댈 것.

---

### [사고 3] 테스트 시 백엔드 없이 Electron만 실행 (2026-04-26)

**무슨 일이 일어났나**: Electron 앱을 `cd frontend && npm run start`로 단독 실행해서 테스트했다. 백엔드(uvicorn)가 없으니 calendar·documents·LLM·TTS 모두 실패하고 시작 인사도 없었다. 앱이 고장난 것처럼 보였지만 실제로는 정상이었다.

**규칙**: Electron 앱 기능 테스트 시에는 반드시 백엔드도 함께 실행해야 한다. 빠른 테스트라도 아래 순서를 지킬 것:
```bash
# 1. 백엔드 시작
cd /프로젝트루트 && export SAESSAGI_ROOT="$(pwd)" && export SAESSAGI_CONFIG_PATH="$(pwd)/conf.yaml" && export PYTHONPATH="$(pwd):$(pwd)/src:$(pwd)/upstream/Open-LLM-VTuber/src:$(pwd)/upstream/Open-LLM-VTuber" && uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393 &
# 2. 백엔드 준비 확인
until curl -sf http://127.0.0.1:12393 >/dev/null 2>&1; do sleep 1; done
# 3. Electron 실행
cd frontend && npm run start
```
또는 그냥 `새싹이.command`를 실행하면 된다.

---

## 멀티에이전트 역할

| 에이전트 | 모델 | 호출 시점 |
|----------|------|-----------|
| `planner` | opus | 아키텍처·스펙 설계 |
| `builder` | sonnet | 구현 |
| `validator` | haiku | 테스트·린트·빌드 |
| `critic` | opus | 적대적 리뷰 (builder 세션과 분리) |

---

## 테스트·빌드 명령

```bash
ruff format . && ruff check . && mypy src/ && pytest tests/ -v
```

---

## 파일 규칙

- 스펙: `specs/M_NN_<name>_SPEC.md` / 리뷰: `reviews/M_NN_<name>_REVIEW.md`
- 소스: `src/<module>/` / 테스트: `tests/<module>/`
- Python 파일: 타입 힌트 필수. 새 의존성: `pyproject.toml` 추가 + PR에 사유 기록.

---

## 상황별 상세 문서

- `docs/ERROR_HISTORY.md` — 과거 버그와 교훈 (반복 방지)
- `docs/FRONTEND_CONSTRAINTS.md` — Electron 투명창 제약, click-through, TTS 주의사항
- `docs/ARCHITECTURE.md` — 전체 아키텍처
- `docs/MODULES.md` — 모듈별 상태 및 인터페이스 계약

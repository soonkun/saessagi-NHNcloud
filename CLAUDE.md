# CLAUDE.md — 프로젝트 인덱스

사내 오프라인 AI 비서 (**웹 UI** + Python 백엔드). 대화 엔진: `vendor/open_llm_vtuber` (Open-LLM-VTuber 벤더링, CR-17 — 클론 불필요).

**CR-38 (2026-07-29): Electron 앱은 제거됐다.** 배포 대상이 헤드리스 GPU 서버로 바뀌어 앱 창을 띄울 수 없게 됐고, UI는 백엔드가 `/`에 서빙하는 웹 페이지가 됐다. `frontend/` 디렉토리와 펫 모드(바탕화면 캐릭터)는 삭제됐다. 접근 통제는 `app.web.auth_enabled` 비밀번호 인증(M_21)이 담당한다.

---

## 세션 시작 시 읽을 문서

| 상황 | 읽을 문서 |
|------|-----------|
| 신규 기능 기획·설계 | `REQUIREMENTS.md` → `docs/ARCHITECTURE.md` → `docs/MODULES.md` |
| 버그 수정 | `docs/ERROR_HISTORY.md` (과거 실수 확인) → 해당 소스 파일 |
| 프론트엔드(React 웹) 작업 | `web/src/` — 빌드는 `cd web && npm run build` |
| 모듈 구현 진행상황 확인 | `docs/MODULES.md` |
| 전체 계획·단계 확인 | `PROJECT_PLAN.md` |

---

## 절대 규칙

- **구현된 척 속이는 짓 금지.** 기능이 실제로 동작하는지 직접 데이터로 확인하기 전까지 "작동한다"고 보고하지 말 것. 특히 RAG·TTS·STT 등 외부 의존성 있는 기능은 반드시 실제 데이터 흐름(벡터 스토어 조회 결과, 오디오 payload, 로그)을 확인 후 보고.
- **AI 모델의 자체 지식을 RAG 결과로 착각하지 말 것.** 벡터 스토어가 비어 있어도 LLM이 정답을 맞힐 수 있다. RAG 작동 여부는 반드시 (1) 벡터 스토어에 실제 문서가 있는지, (2) 쿼리 시 hit가 반환되는지, (3) 로그에 "Proactive RAG: N건 주입" 같은 실제 주입 기록이 있는지로만 판단할 것.
- **specs/M_NN_SPEC.md 없이 src/ 파일 생성 금지.**
- **REQUIREMENTS.md에 없는 기능 추가 금지.** 필요 시 `docs/CHANGE_REQUESTS.md` 작성 후 사용자 승인 먼저.
- **외부 네트워크 호출 금지** (인터넷 검색·임의 외부 API 등). fetch/requests는 127.0.0.1·localhost·사내 IP만 허용.
  - **단, LLM 공급자 호출은 예외(의도된 임시 대역).** 상용 타깃은 기관 인트라넷 중앙 GPU(대구 PPP존)이고, 그 인프라 구축 전까지는 ChatGPT(GPT-4o/GPT-5)를 "인트라넷에 올릴 OSS 모델"의 임시 대역으로 사용한다. 즉 conf.yaml의 LLM provider가 openai면 `api.openai.com` 호출이 발생하는 것은 **정상**이며 위반이 아니다. 이 규칙의 본래 의도는 인터넷 검색·웹 크롤링·임의 외부 서비스 호출 차단이다. (배포 비전 상세는 메모리 `project_deployment_vision` 참조. 향후 인트라넷 GPU 구축 시 base_url만 사내로 전환.)
- **웹 UI를 네트워크에 열 때는 반드시 인증을 켤 것.** `app.web.host`가 루프백이 아닌데 `auth_enabled: false`면 백엔드가 기동을 거부한다(M_21). 이 안전장치를 우회하지 말 것 — 앱에는 사용자·권한 개념이 없어 비밀번호가 유일한 접근 통제다.
- **변경 전 반드시 관련 소스 읽기.** 코드 동작 이해 없이 수정하면 회귀 발생.
- **버그를 발견하고 수정했으면 반드시 `docs/ERROR_HISTORY.md`에 기록할 것.** 증상·원인·수정 내용·교훈을 빠짐없이 작성. 같은 실수가 반복되지 않도록 하는 것이 목적이며, 기록 없이 수정만 하는 것은 금지.
- **백엔드 변경 후 반드시 프론트엔드까지 E2E 검증할 것.** 백엔드 테스트만으로 완료 보고 금지.
  - WebSocket 스크립트(`scripts/ws_test.py`)로 백엔드 응답 텍스트·오디오 payload 수신 확인.
  - UI까지 볼 때는 **헤드리스 브라우저(puppeteer 등)로 실제 페이지를 열어** 확인한다. CR-38 이전의 "브라우저 접속 금지" 규칙은 폐기됐다 — 이제 브라우저가 유일한 UI다.
  - **화면에 있는 텍스트로 응답을 판정하지 말 것.** 사이드바·타이틀에 "새싹이" 같은 문자열이 상시 존재해 키워드 매칭은 거짓 양성이 난다. 전송 직후 스냅샷과 비교해 *새로 늘어난* 텍스트만 응답으로 인정할 것.

---

## 반복 발생 사고 — 반드시 읽고 같은 실수 금지

### [사고 1] 웹 UI를 네트워크에 열면서 인증을 빠뜨림 (CR-38, 2026-07-29)

`app.web.host`를 `0.0.0.0`으로 바꾸면 사내 문서 RAG·파일 업로드·LLM·회의록이 전부
노출된다. 앱에는 로그인·권한 개념이 없어 비밀번호가 유일한 방벽이다.

**규칙**: 노출과 인증은 항상 같이 간다. `web_auth.validate_web_config()`가 루프백이 아닌
host + `auth_enabled: false` 조합에서 기동을 거부하도록 되어 있으니, 이 검증을 느슨하게
고치지 말 것. WebSocket(`/client-ws`)도 반드시 함께 막아야 한다 — 대화·TTS가 전부 그
경로로 흐르므로 HTTP만 막으면 사실상 아무것도 막지 못한다.

---

### [사고 2] 의도 분류기에 대형 모델을 물려 매 턴 타임아웃 (CR-39, 2026-07-29)

`app.ollama.model`만 128B로 바꿨더니 IntentClassifier가 그걸 따라가 **매 메시지마다 8초
타임아웃**이 나고 의도 판정이 상실됐다(`source=fallback_error`, conf=0.00). 폴백이 RAG를
뭉뚱그려 주입해서 겉보기엔 동작하므로 발견이 늦다.

**규칙**: 작업별 모델은 각자의 설정 항목으로 분리할 것.
`intent_gate`(가볍고 빨라야 함) / `graphrag.extraction_*`(정확도) /
`deep_research`(장문 추론) / `ollama.vision_model`. 채팅 모델을 키울 때
**`intent_gate.ollama_model`을 함께 지정**하지 않으면 이 사고가 재발한다.
`app.ollama.model`과 `agent_config.llm_configs.ollama_llm.model` **두 곳**을 같이 봐야 한다.

---

### [사고 3] `uv run`이 melotts를 지워 TTS·LLM이 통째로 죽음 (E-65, 2026-07-29)

`uv run`은 실행 전 환경을 `uv.lock`에 맞춰 동기화하면서 **락파일에 없는 패키지를 제거한다.**
melotts는 pypinyin 충돌 때문에 의도적으로 `pyproject.toml` 밖에 있고 bootstrap이 따로
설치하므로, 런처가 `uv run`을 쓰면 매 실행마다 지워졌다.

**규칙**: 파이썬 프로세스는 `.venv/bin/python`을 직접 부르거나 `uv run --no-sync`를 쓸 것.
"bootstrap은 됐는데 실행만 하면 전부 무반응"이면 이걸 먼저 의심한다.

---

### [사고 4] 테스트 시 백엔드 없이 프론트만 확인 (2026-04-26, CR-38 반영)

백엔드(uvicorn) 없이 UI만 열면 calendar·documents·LLM·TTS가 전부 실패해 앱이 고장난
것처럼 보인다. 실제로는 정상이다.

**규칙**: `./새싹이.sh`로 백엔드를 먼저 띄운 뒤 브라우저로 접속해 확인할 것.
이 스크립트 하나가 프론트 빌드·Ollama·Neo4j(graphrag 켠 경우)·백엔드·외부 접속 주소를
전부 처리한다. `--no-build`는 재빌드 생략, `--local`은 외부 주소 생략. 종료는 `./새싹이끄기.sh`.

**기능을 켜려고 사용자에게 추가 명령을 요구하지 말 것.** 설정으로 판단할 수 있는 것은
런처나 앱이 알아서 해야 한다(예: RAG 감시 최초 시딩은 백엔드가 첫 스캔 전에 자동 수행).

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
- `specs/M_21_WebAuth_SPEC.md` — 웹 인증 설계 (토큰·미들웨어·설정 안전장치)
- `docs/FRONTEND_CONSTRAINTS.md` — **(CR-38로 대부분 무효)** Electron 투명창·click-through 제약. TTS 주의사항만 유효
- `docs/ARCHITECTURE.md` — 전체 아키텍처
- `docs/MODULES.md` — 모듈별 상태 및 인터페이스 계약

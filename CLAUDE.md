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

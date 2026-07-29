# 새싹이 — 사내 오프라인 AI 비서

> 인터넷이 차단된 사내 인트라넷 환경에서 완전 오프라인으로 동작하는 AI 비서.  
> 캐릭터 "새싹이"가 음성으로 대화하고, 사내 문서를 검색하고, 회의록을 자동으로 작성합니다.  
> **브라우저로 접속하는 웹 앱**입니다 — 서버에 화면이 없어도 됩니다.

<div align="center">

| ![neutral](assets/character/saessagi/neutral.png) | ![happy](assets/character/saessagi/happy.png) | ![thinking](assets/character/saessagi/thinking.png) | ![writing](assets/character/saessagi/writing.png) | ![study](assets/character/saessagi/study.png) |
|:---:|:---:|:---:|:---:|:---:|
| 기본 | 기뻐요 | 생각 중 | 작성 중 | 공부 중 |

</div>

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **음성·텍스트 대화** | 마이크 또는 텍스트로 대화. 한국어 음성 인식 + 음성 합성 |
| **문서 RAG** | 사내 PDF·DOCX·HWPX 등을 벡터 검색해 출처 명시 답변 |
| **일정 관리** | 자연어로 일정 등록·조회. 10분 전 알림 자동 발송 |
| **회의록 자동 작성** | 음성 파일 업로드 → 전사 → 요약 → HWPX 결과보고서 3단계 자동화 |
| **화면 분석** | 화면 캡처 → 멀티모달 LLM 분석 |
| **사내망 공유** | 서버 한 대에 띄우고 팀원이 브라우저로 접속. 비밀번호 인증 |
| **완전 오프라인** | 모든 AI 추론을 로컬에서 수행. 외부 네트워크 호출 없음 |

---

## 기술 스택

```
Backend  : Python 3.12 · FastAPI · Ollama (Gemma 4) · faster-whisper · MeloTTS
           LanceDB (벡터 DB) · SQLite (캘린더) · pydantic v2
Frontend : React 18 · TypeScript · Vite (백엔드가 정적 서빙)
Base     : Open-LLM-VTuber (상속·확장)
```

---

## 빠른 시작

### 1. 설치

```bash
python3 scripts/bootstrap.py      # uv·venv·의존성·Ollama 모델·BGE-M3
cd web && npm install && cd ..
```

TTS·ASR 모델은 별도로 받아야 합니다 — [install.md 3단계](install.md) 참조.
(빠뜨리면 TTS 초기화 실패가 LLM 대화까지 죽입니다.)

### 2. 실행

```bash
./새싹이.sh
```

출력된 주소를 브라우저로 열면 됩니다. 기본은 로컬 전용(`127.0.0.1:12393`)입니다.

### 3. 사내망에 공개 (선택)

`conf.yaml`의 `app.web`에서 `host: 0.0.0.0` + `auth_enabled: true` +
`auth_password`를 설정합니다. **인증을 켜지 않으면 기동을 거부합니다** — 앱에
로그인 개념이 없어 비밀번호가 유일한 접근 통제이기 때문입니다.

> 마이크(음성 입력)는 브라우저 정책상 HTTPS에서만 동작합니다.
> 텍스트 대화와 음성 출력은 평문 HTTP에서도 정상입니다.

---

## 오프라인 USB 배포

인터넷이 차단된 환경을 위한 완전 오프라인 번들:

```bash
bash scripts/bundle_usb.sh /Volumes/USB명
```

USB에 Python·Ollama·모델·wheel이 모두 포함되며, 대상 서버에서 `install.sh` 하나로 설치됩니다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [사용자 매뉴얼](docs/USER_GUIDE.md) | 기능별 사용법 |
| [기술 개발 보고서](TECHNICAL_REPORT.md) | 아키텍처·모듈·버그 해결 이력 |
| [요구사항](REQUIREMENTS.md) | 기능·비기능 요구사항 정의 |
| [아키텍처](docs/ARCHITECTURE.md) | 전체 블록 다이어그램 |
| [에러 히스토리](docs/ERROR_HISTORY.md) | 버그 원인·해결·교훈 (E-01 ~ E-20) |
| [웹 인증 설계](specs/M_21_WebAuth_SPEC.md) | 비밀번호 인증·세션·미들웨어 |

---

## 개발 방식

멀티에이전트 파이프라인으로 개발했습니다:

| 역할 | 모델 | 담당 |
|---|---|---|
| Planner | Opus | 아키텍처 설계·모듈 스펙 |
| Builder | Sonnet | 구현 |
| Critic | Opus | 독립 적대적 리뷰 |
| Validator | Haiku | 테스트·린트·빌드 검증 |

---

*GitHub: https://github.com/soonkun/ai-assistant*

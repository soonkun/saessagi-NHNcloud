# 새싹이 — 사내 오프라인 AI 비서

> 인터넷이 차단된 사내 인트라넷 환경에서 완전 오프라인으로 동작하는 AI 비서.  
> 캐릭터 "새싹이"가 음성으로 대화하고, 사내 문서를 검색하고, 회의록을 자동으로 작성합니다.

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
| **펫 모드** | 투명 배경·항상 위·클릭 관통으로 바탕화면 위에 상주 |
| **완전 오프라인** | 모든 AI 추론을 로컬에서 수행. 외부 네트워크 호출 없음 |

---

## 기술 스택

```
Backend  : Python 3.12 · FastAPI · Ollama (Gemma 4) · faster-whisper · MeloTTS
           LanceDB (벡터 DB) · SQLite (캘린더) · pydantic v2
Frontend : Electron 35 · React 18 · TypeScript · Vite
Base     : Open-LLM-VTuber (상속·확장)
```

---

## 빠른 시작

### 1. 의존성 설치

```bash
# Python 환경
uv venv && uv pip install -e ".[dev]"

# Ollama 모델 (인터넷 연결 시 1회)
ollama pull gemma4:e4b

# 프론트엔드
cd web && npm install
cd ../frontend && npm install
```

### 2. 프론트엔드 빌드

```bash
cd web && ELECTRON_BUILD=1 npm run build
cd ../frontend && npm run build
```

### 3. 실행

```bash
# 백엔드 서버
bash start.sh        # macOS/Linux
start.cmd            # Windows

# Electron 앱 (개발)
cd frontend && npm run dev
```

> **주의**: 브라우저에서 `http://127.0.0.1:12393`을 직접 열지 마세요.  
> 새싹이는 Electron 앱 전용 UI입니다.

---

## 오프라인 USB 배포

인터넷이 차단된 환경을 위한 완전 오프라인 번들:

```bash
bash scripts/bundle_usb.sh /Volumes/USB명
```

USB에 Python·Ollama·모델·wheel이 모두 포함되며, 대상 PC에서 `install.bat` / `install.sh` 하나로 설치됩니다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [사용자 매뉴얼](docs/USER_GUIDE.md) | 기능별 사용법 |
| [기술 개발 보고서](TECHNICAL_REPORT.md) | 아키텍처·모듈·버그 해결 이력 |
| [요구사항](REQUIREMENTS.md) | 기능·비기능 요구사항 정의 |
| [아키텍처](docs/ARCHITECTURE.md) | 전체 블록 다이어그램 |
| [에러 히스토리](docs/ERROR_HISTORY.md) | 버그 원인·해결·교훈 (E-01 ~ E-20) |
| [프론트엔드 제약](docs/FRONTEND_CONSTRAINTS.md) | Electron 투명창 제약 사항 |

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

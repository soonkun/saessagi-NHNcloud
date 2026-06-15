# 새싹이 AI 비서 — 기술 개발 보고서

> **프로젝트**: 사내 인트라넷 오프라인 AI 비서 "새싹이(Saessagi)"  
> **최종 갱신**: 2026-04-26  
> **버전**: v1.1 (회의록 자동화 + 프론트엔드 v2 기준)

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [요구사항 요약](#2-요구사항-요약)
3. [기술 스택 선정](#3-기술-스택-선정)
4. [아키텍처 설계](#4-아키텍처-설계)
5. [모듈별 개발 히스토리](#5-모듈별-개발-히스토리)
6. [핵심 기술 문제와 해결](#6-핵심-기술-문제와-해결)
7. [배포 전략](#7-배포-전략)
8. [현재 상태 및 잔여 과제](#8-현재-상태-및-잔여-과제)

---

## 1. 프로젝트 개요

### 배경

사내 인트라넷 환경에서 동작하는 AI 비서 소프트웨어. 외부 인터넷이 차단된 완전 오프라인 환경이 전제 조건이다. 기존의 클라우드 기반 AI 서비스를 사용할 수 없으므로, 모든 추론을 로컬 GPU/CPU에서 직접 수행한다.

### 핵심 목표

- **음성으로 말을 걸면 음성으로 답하는** 멀티모달 AI 비서
- **사내 문서(PDF/DOCX/HWPX 등)를 검색**해 출처를 명시한 답변 제공
- **일정 관리**: 자연어로 일정 등록·조회·알림
- **회의록 자동 작성**: 음성 파일 업로드 → 전사 → 요약 → HWPX 결과보고서 3단계 자동화
- **캐릭터 아바타 "새싹이"**: 투명 배경의 펫 모드로 항상 화면 위에 표시
- **완전 오프라인**: 네트워크 호출 제로. 모든 모델·의존성 로컬 번들

### 개발 방식

`Open-LLM-VTuber`(이하 "upstream")를 베이스 프레임워크로 채택해 상속·확장하는 방식을 선택했다. 처음부터 다 만드는 대신 검증된 WebSocket 파이프라인(ASR → VAD → LLM → TTS)을 재사용하고, 사내 요구사항에 맞는 기능(RAG, 캘린더, 유휴 감지, 스프라이트 아바타, 펫 모드, 회의록)을 추가했다.

개발은 역할 분리된 멀티에이전트 구조로 진행됐다:

| 역할 | 담당 |
|---|---|
| Planner | 아키텍처 설계, 모듈 스펙 작성 |
| Builder | 실제 코드 구현 |
| Critic | 독립적 적대적 리뷰 (Builder와 세션 분리) |
| Validator | pytest·ruff·mypy 실행으로 검증 |
| Integrator | E2E 통합 테스트 |

---

## 2. 요구사항 요약

### 기능 요구사항

| 영역 | 주요 요구사항 |
|---|---|
| 음성 대화 | STT(한국어/영어), VAD, TTS(한국어 여성), Full-Duplex 끼어들기 |
| 텍스트 대화 | 채팅 UI, 음성과 동일 컨텍스트 공유 |
| 문서 RAG | PDF·DOCX·PPTX·HWP/HWPX·TXT·MD 파싱, 페이지 단위 인용, 원문 하이라이트 |
| 캐릭터 아바타 | 스프라이트 9종, 펫 모드(투명·클릭 관통·항상 위), 감정 연동 |
| 일정 관리 | 자연어 파싱 → 함수 호출 → SQLite 저장, 10분 전 알림, 아침 브리핑 |
| 유휴 감지 | 45분 무입력 → 휴식 권고, 2시간 연속 → 과로 경고 |
| 화면 인식 | 사용자 요청 시 스크린샷 → 멀티모달 LLM 분석 |
| **회의록 자동화** | **음성 업로드 → Whisper 전사 → LLM 요약 → HWPX 결과보고서 생성** |

### 비기능 요구사항

| 항목 | 목표 |
|---|---|
| 기동 시간 | 15초 이내 |
| 음성 응답 지연 | GPU 2초 / CPU-only 6초 이내 |
| 메모리(MIN 프로파일) | 14GB 이하 (Whisper medium int8) |
| 메모리(RECOMMENDED) | 20GB 이하 (Whisper large-v3) |
| 외부 네트워크 | 0건 (URL 화이트리스트 검증으로 강제) |
| OS | Windows 10/11 배포, macOS/Linux 개발 지원 |

### 모델 선정

| 용도 | 모델 | 비고 |
|---|---|---|
| LLM | Gemma 4 E4B (Ollama) | 멀티모달, 함수 호출, 131K 컨텍스트 |
| STT | faster-whisper large-v3-turbo | 한국어 int8 CPU, 최신 경량 버전 |
| TTS | MeloTTS Korean | 한국어 여성 음성, 오픈소스 |
| 임베딩 | BGE-M3 (sentence-transformers) | 다국어, 한국어 포함 |
| 벡터 DB | LanceDB (embedded) | 설치 불필요, 로컬 디스크 |

---

## 3. 기술 스택 선정

### 백엔드

```
Python 3.12
├── FastAPI 0.115     — WebSocket + REST 서버
├── asyncio           — 비동기 파이프라인
├── Ollama            — LLM 로컬 런타임
├── faster-whisper    — CTranslate2 기반 STT + 회의록 전사
├── MeloTTS           — 한국어 TTS
├── LanceDB           — 벡터 저장소 (임베드)
├── SQLite            — 캘린더 데이터
├── pydantic v2       — 설정 스키마 검증
└── python-hwpx       — HWPX 결과보고서 생성
```

### 프론트엔드

```
Electron 35
├── React 18          — UI 컴포넌트
├── Vite              — 번들러
├── TypeScript        — 타입 안전성
└── SSE (fetch API)   — 실시간 진행 스트리밍
```

### 개발 도구

```
uv              — Python 패키지 관리 (pip 대체)
ruff            — 린터·포매터
mypy            — 정적 타입 검사
pytest          — 단위·통합·E2E 테스트
```

---

## 4. 아키텍처 설계

### 전체 구조

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                    FRONTEND (Electron + React)                       │
 │  ┌────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
 │  │  Chat UI   │  │  Calendar   │  │  Documents  │  │  Meeting   │  │
 │  │ Text/Voice │  │    View     │  │    View     │  │    View    │  │
 │  └─────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘  │
 │        │                                              REST│(SSE)    │
 │        └──────────────────────────────────────────────────┘         │
 │               WebSocket + REST API client                           │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐    │
 │  │                  Sprite Avatar (새싹이)                      │    │
 │  │  표정 9종 · 펫 모드(투명·항상 위·클릭 관통·드래그 이동)      │    │
 │  └─────────────────────────────────────────────────────────────┘    │
 └───────────────────────────────┬─────────────────────────────────────┘
                                 │ ws://127.0.0.1:12393
 ┌───────────────────────────────▼─────────────────────────────────────┐
 │                   APPLICATION LAYER (FastAPI)                        │
 │  ┌──────────────────────────────────────────────────────────────┐   │
 │  │ AppServiceContext (upstream ServiceContext 확장)              │   │
 │  │  + RagService · CalendarService · IdleMonitor                │   │
 │  │  + AvatarState · ProactiveDispatcher                         │   │
 │  └──────┬──────────┬──────────┬────────────────────────────────┘   │
 │         │          │          │                                     │
 │     ┌───▼──┐  ┌────▼────┐  ┌──▼──────────────────────────────────┐ │
 │     │ ASR  │  │   LLM   │  │ MeetingMinutes (M_13)               │ │
 │     │(M_02)│  │(M_05+FC)│  │  /transcribe-stream  (Whisper)      │ │
 │     └───┬──┘  └────┬────┘  │  /summarize-stream   (LLM)          │ │
 │         │          │       │  /generate-stream    (python-hwpx)  │ │
 │     ┌───▼──┐  ┌────▼────────────────────────────────────────────┘ │
 │     │ VAD  │  │ ToolRouter → RAG (M_06/M_07) · Calendar (M_09)   │ │
 │     │(M_03)│  └───────────────────────────────────────────────────┘ │
 │     └──────┘                                                        │
 │                          ┌────▼────┐                               │
 │                          │   TTS   │                               │
 │                          │ (M_04)  │                               │
 │                          └─────────┘                               │
 └─────────────────────────────────────────────────────────────────────┘
```

### 핵심 설계 결정

**1. Upstream 상속 전략**  
처음부터 작성하는 대신 `ServiceContext`, `WebSocketHandler`, `ConversationOrchestrator`를 Python 상속으로 확장했다. upstream의 WebSocket 파이프라인(메시지 라우팅·오디오 스트리밍·인터럽트 처리)을 그대로 재사용하면서, 필요한 부분만 메서드 오버라이드로 교체했다.

**2. 완전 오프라인 강제**  
`url_guard.py`가 모든 외부 URL을 로드 타임에 검사한다. `127.0.0.1`·`localhost`·사내 IP가 아닌 URL이 설정 파일에 있으면 앱이 기동을 거부한다.

**3. 하드웨어 자동 적응**  
`src/app/hardware.py`가 시작 시 하드웨어를 감지해 최적 설정을 자동 선택한다.

| 환경 | Whisper 설정 | TTS |
|---|---|---|
| NVIDIA ≥16GB VRAM (RTX 4090) | large-v3-turbo / cuda / float16 | cuda |
| NVIDIA 6-16GB VRAM | large-v3-turbo / cuda / int8_float16 | cuda |
| Apple Silicon (MPS) | large-v3-turbo / cpu / int8 | auto (MPS) |
| CPU, RAM ≥16GB | large-v3-turbo / cpu / int8 | cpu |
| CPU, RAM <16GB | medium / cpu / int8 | cpu |

**4. LLM 사고 모드 제어**  
Gemma 4는 기본적으로 모든 응답에 Chain-of-Thought 추론(thinking)을 사용한다. 일상 대화에서 수 초의 불필요한 대기가 발생하므로, Ollama API 호출 시 `extra_body={"think": False}`를 주입해 기본적으로 비활성화했다.

**5. 회의록 3단계 SSE 파이프라인**  
회의록 자동화는 각 단계를 SSE(Server-Sent Events) 스트림으로 노출해 프론트엔드가 실시간 진행상황을 표시할 수 있도록 설계했다.

```
오디오 파일 (POST /transcribe-stream)
    → SSE: {"stage":"progress","message":"음성 파일 변환 중..."}
    → SSE: {"stage":"done","transcript":"..."}

전사 텍스트 (POST /summarize-stream)
    → SSE: {"stage":"progress","message":"회의 내용을 분석 중..."}
    → SSE: {"stage":"done","meeting_notes":"..."}

회의록 텍스트 (POST /generate-stream)
    → SSE: {"stage":"progress","message":"HWPX 문서 생성 중..."}
    → SSE: {"stage":"done","download_url":"/api/meeting-minutes/download/{id}"}
```

---

## 5. 모듈별 개발 히스토리

### 개발 순서 (의존성 그래프 순)

```
M_01 → M_02 → M_03 → M_04 → M_05 → M_05b
                                       ↓
M_09 → M_07 → M_06 → M_08 → M_10 → M_11 → M_12 → M_13
```

---

### M_01 — AppCore (EXTEND)

**역할**: FastAPI 앱 진입점, upstream ServiceContext 확장, WebSocket 라우팅

**주요 작업**:
- `AppServiceContext`가 upstream `ServiceContext`를 상속. `init_asr`, `init_tts`, `init_agent` 오버라이드로 프로젝트 고유 엔진(MeloTTS, GemmaChatAgent) 배선
- `AppWebSocketHandler`가 `_send_initial_messages` 오버라이드로 Live2D 없는 환경(None 처리) 대응
- `construct_system_prompt` 오버라이드로 Live2D 모델 없어도 페르소나 프롬프트 정상 적용
- URL 화이트리스트 검증 (`url_guard.py`)
- 구조화된 설정 스키마 (`AppConfig`, `FullConfig` — pydantic v2)

---

### M_02 — ASREngine (EXTEND)

**역할**: 한국어 faster-whisper 래퍼

- upstream `FasterWhisperASR`를 한국어 설정(`language="ko"`, `compute_type="int8"`)으로 초기화
- 모델: `large-v3-turbo` (최신 경량 버전, ~1.5GB, CPU 친화적)
- 하드웨어 적응형 설정 자동 적용

---

### M_03 — VADEngine (REUSE)

**역할**: 발화 구간 감지 (Voice Activity Detection)

upstream `SileroVAD`를 그대로 사용. 설정값(`prob_threshold=0.4`, `db_threshold=60`)만 조정.

---

### M_04 — TTSEngine (NEW)

**역할**: 한국어 음성 합성 (MeloTTS)

- `MeloTTSEngine`: upstream `TTSInterface` 구현
- 동기 `generate_audio()` + 비동기 `async_generate_audio()` 양쪽 지원
- asyncio.Lock으로 동시 합성 요청 직렬화
- REST API `/api/tts/speak` 엔드포인트 — 회의록 단계 완료 시 TTS 안내음성 재생에도 사용

---

### M_05 — LLMAgent (EXTEND)

**역할**: Gemma 4 E4B 기반 대화 에이전트

```
GemmaChatAgent          ← 프로젝트 고유 Ollama 래퍼
    │
    └── BasicMemoryAgentAdapter  ← upstream AgentInterface 호환 어댑터
            │
            └── upstream BasicMemoryAgent  ← 대화 히스토리 관리
```

- `GemmaChatAgent`가 OpenAI 호환 Ollama API를 직접 호출, 스트리밍 응답 생성
- `BasicMemoryAgentAdapter`가 upstream `AgentInterface`를 만족하면서 `SentenceOutput` 타입으로 변환
- Thinking 모드 비활성화: `extra_body={"think": False}` 주입
- Tool call 이벤트(`ToolCallStart`, `ToolCallResult`)를 dict로 변환해 upstream 파이프라인에 전달

---

### M_05b — ToolRouter (NEW)

**역할**: LLM 함수 호출 디스패처

Gemma 4의 function calling 출력을 파싱해 `search_docs`, `add_event`, `screenshot` 등 로컬 도구로 라우팅. JSON Schema 기반 도구 등록, 실행 결과를 컨텍스트에 주입.

---

### M_06 — DocumentIngest (NEW)

**역할**: 사내 문서 파싱·청킹·임베딩

**지원 포맷**: PDF, DOCX, PPTX, HWPX, TXT, MD

- `doc_id = SHA-256(절대경로)[:32]` — 경로 기반 중복 방지
- HWPX: 네임스페이스 `2011/hwpml/2.0`·`2016/hwpml/2.1` 양쪽 지원
- XXE 방어: `defusedxml` 적용
- 페이지 번호·섹션·bbox 메타데이터 보존

---

### M_07 — VectorSearch (NEW)

**역할**: BGE-M3 임베딩 + LanceDB 벡터 검색 + 인용 포매터

- `sentence-transformers` + `BGE-M3`으로 다국어(한국어 포함) 임베딩
- LanceDB embedded 모드 — 서버 없이 로컬 디스크에 직접 저장
- 인용 포매팅: `"이 내용은 {문서명} {페이지}페이지에 있습니다"` 형식
- `rag_min_score=0.35` 임계값 미만 결과 필터링

---

### M_08 — AvatarState (NEW)

**역할**: LLM 출력에서 감정 태그 파싱 → 스프라이트 상태 이벤트 송신

LLM 응답의 `[emotion:happy]` 같은 태그를 감지해 프론트엔드에 `set-avatar-expression` 메시지 전송. 표정 세트: neutral·happy·surprised·sad·worried·thinking·tired·study·writing (9종).

---

### M_09 — CalendarService (NEW)

**역할**: SQLite 기반 일정 CRUD + 자연어 파싱 연동

Gemma 4의 함수 호출로 `add_event(title, start_dt, duration_min)`, `list_events(date)`, `delete_event(id)` 실행. 10분 전 알림 스케줄링은 M_11 ProactiveDispatcher와 연동.

---

### M_10 — IdleMonitor (NEW)

**역할**: 마우스·키보드 유휴 상태 감지

**3계층 폴백 구조**:
1. `pynput` (크로스플랫폼 글로벌 훅)
2. `win32api.GetLastInputInfo` (Windows 전용, 더 정확)
3. 폴링 폴백 (제한 환경)

45분 무입력 → `idle_rest` 이벤트, 2시간 연속 활동 → `overwork` 이벤트 발행.

---

### M_11 — ProactiveDispatcher (NEW)

**역할**: 능동적 메시지 발송 (아침 브리핑, 일정 알림, 휴식 권고)

**APScheduler 기반 스케줄링**:
- 아침 브리핑: 매일 09:00 (설정 가능)
- 일정 알림: 이벤트 10분 전 자동 발송
- 유휴/과로 이벤트 수신 → 쿨다운 로직 적용 후 메시지 생성

---

### M_12 — Frontend (FORK+NEW)

**역할**: Electron + React 기반 GUI

**5단계 개발**:

| 단계 | 내용 |
|---|---|
| P1 Foundation | upstream-Web 반입, Live2D 제거, WebSocket 통합, 기반 인프라 |
| P2 | SpriteAvatarRenderer 구현 — 스프라이트 스왑, 200ms crossfade, 아이들 애니메이션 |
| P3 | 펫 모드 — 투명 배경, 항상 위, 클릭 관통, 드래그 이동, IPC 배선 |
| P4 | pdf.js 인용 뷰어 — 원문 페이지 열기, 하이라이트, bbox 포지셔닝 |
| P5 | RSS 문서 감시, 적대적 테스트, E2E skeleton, NSIS 설치 패키지 |

**주요 UI 구성**:
- 탭 5종: 채팅 / 캘린더 / 문서 / 회의록 / 설정
- ChatPanel — 모든 탭 컨텐츠의 컨테이너, 모드(window/pet) 연동
- MeetingView — 3단계 회의록 wizard (항상 마운트, CSS display:none으로 state 보존)
- CharacterWidget — 감정 스프라이트 + 드래그 이동 + 클릭 토글

---

### M_13 — MeetingMinutes (NEW)

**역할**: 3단계 회의록 자동화 파이프라인

**구성 파일**:
- `src/meeting_minutes/generator.py` — LLM 기반 회의록 요약 + HWPX 생성
- `src/meeting_minutes/prompts.py` — 회의 결과 보고서 프롬프트 템플릿
- `src/app/meeting_minutes_routes.py` — FastAPI SSE 라우트 3종

**3단계 흐름**:

```
Step 1 — 전사 (Transcribe)
  POST /api/meeting-minutes/transcribe-stream
  · 오디오 파일(wav/mp3/m4a 등) 수신
  · faster-whisper large-v3-turbo로 한국어 전사
  · SSE로 진행 메시지 스트리밍 → 완료 시 transcript 반환
  · 완료 TTS: "전사가 완료되었어요. 확인해 보시고 회의록 작성을 시작해주세요."

Step 2 — 요약 (Summarize)
  POST /api/meeting-minutes/summarize-stream
  · transcript + pages(1/2) 수신
  · Gemma 4로 개조식 회의록 텍스트 생성
  · pages=1: max_tokens=2048, A4 1페이지 분량 지시
  · pages=2: max_tokens=3000, A4 2페이지 분량 지시
  · 완료 TTS: "회의록 작성이 완료되었습니다. 확인해보시고 결과보고서 작성을 시작하세요."

Step 3 — 보고서 생성 (Generate)
  POST /api/meeting-minutes/generate-stream
  · meeting_notes 수신 → JSON 파싱 (MeetingMinutesModel)
  · python-hwpx로 .hwpx 문서 생성
  · UUID 기반 임시 파일 → /download/{file_id} 엔드포인트로 제공 (24h)
  · 완료 TTS: "결과보고서를 완성하였습니다. 내용을 검토하세요."
```

**JSON 출력 스키마 (`MeetingMinutesModel`)**:

```python
class MeetingMinutesModel(BaseModel):
    title: str
    date: str              # 정규식: r'^\d{4}\.\d{2}\.\d{2}\.$'
    datetime_place: str
    attendees: list[str]
    agenda_items: list[AgendaItem]
    decisions: list[str]
    action_items: list[ActionItem]
    next_steps: list[str]
```

---

## 6. 핵심 기술 문제와 해결

### 문제 1: MeloTTS 한국어 합성 실패 (macOS)

**증상**: `g2pkk`(한국어 자소 변환 라이브러리)가 `import mecab`에 실패해 `AttributeError: 'NoneType' object has no attribute 'pos'`

**원인 분석**:
- `python-mecab-ko` v1.3.7은 C 확장(`_mecab.so`)만 제공하고, 예전 버전이 제공하던 `mecab/` Python 패키지를 더 이상 포함하지 않음
- `mecab-python3` v1.0.12는 `MeCab/` (대문자) 패키지를 제공하지만, macOS HFS+ 파일시스템이 대소문자 비구분이라 `os.listdir()`은 `MeCab`으로 반환함 — Python의 import 시스템은 대소문자를 구분하므로 `import mecab` (소문자)이 실패함

**해결책**:
- `vendor/mecab_shim/mecab/` — HFS+ 외부의 별도 경로에 소문자 `mecab` 패키지 생성
- `mecab_shim.pth` — site-packages에 `.pth` 파일로 해당 경로를 sys.path에 추가
- `mecab/__init__.py` — `__getattr__`을 이용한 지연 임포트로 순환 임포트 차단

```python
# mecab/__init__.py — 지연 임포트로 순환 차단
def __getattr__(name: str) -> object:
    if name in ("MeCab", "MeCabError", "mecabrc_path"):
        from MeCab.mecab import MeCab as _C, MeCabError as _E, mecabrc_path as _p
        globals()["MeCab"] = _C
        # ...
        return globals()[name]
    raise AttributeError(f"module 'mecab' has no attribute {name!r}")
```

---

### 문제 2: Upstream SentenceOutput 타입 불일치

**증상**: LLM 응답이 UI에 표시되지 않음

**원인**: upstream `process_single_conversation`이 agent 스트림에서 `SentenceOutput` 타입만 처리하고 `str`은 무시함. 프로젝트의 `BasicMemoryAgentAdapter.chat()`이 raw string을 yield했음.

**해결**: `TextChunk` 이벤트를 누적해 전체 텍스트를 조립한 뒤 `SentenceOutput(display_text=..., tts_text=...)` 하나로 yield.

---

### 문제 3: Gemma 4 Thinking 모드로 인한 응답 지연

**증상**: "안녕"에 8~15초 응답 지연. Ollama 로그에 `<think>...</think>` 블록이 대량 생성됨.

**해결**: Ollama의 비공개 파라미터 `think`를 `false`로 설정.

```python
stream = await self._llm.client.chat.completions.create(
    messages=messages, model=self._llm.model, stream=True,
    temperature=self._llm.temperature,
    extra_body={"think": False},  # Gemma 4 thinking 비활성화
)
```

---

### 문제 4: E-17 — transcribe-stream UploadFile 조기 소멸

**증상**: M4A 파일 업로드 후 `{"stage":"error","message":"read of closed file"}`

**원인**: `await audio_file.read()`를 `StreamingResponse` 제너레이터 내부에서 호출했는데, FastAPI 런타임이 라우트 핸들러가 `return StreamingResponse(...)`를 반환하는 시점에 `UploadFile`을 닫아버림. 제너레이터가 실제로 실행되는 시점(스트리밍 시작 시)에는 파일이 이미 소멸됨.

**해결**: `audio_bytes = await audio_file.read()`와 `suffix` 추출을 라우트 핸들러 본체(제너레이터 생성 전)에서 수행. 클로저로 참조.

```python
async def transcribe_stream(audio_file: UploadFile = File(...)):
    audio_bytes = await audio_file.read()      # ← 제너레이터 생성 전에 읽기
    suffix = Path(audio_file.filename or "audio.wav").suffix.lower()
    async def run():
        # audio_bytes, suffix를 클로저로 참조
        ...
    return StreamingResponse(run(), media_type="text/event-stream")
```

---

### 문제 5: E-18 — LLM이 날짜 플레이스홀더 "YYYY.MM.DD." 그대로 반환

**증상**: 날짜 정보가 없는 녹취록으로 Step 3 실행 시 JSON Schema 위반: `'YYYY.MM.DD.' does not match '^\d{4}\.\d{2}\.\d{2}\.$'`

**원인**: 프롬프트 템플릿에 `"date": "YYYY.MM.DD."` 예시를 포함했는데, 녹취록에 날짜가 없으면 LLM이 예시를 그대로 출력함.

**해결**: `generator.py`에서 `today_date = datetime.date.today().strftime("%Y.%m.%d.")`를 계산해 프롬프트에 주입. "날짜를 알 수 없으면 오늘 날짜를 사용하세요" 지시 추가.

```python
today = datetime.date.today().strftime("%Y.%m.%d.")
user_prompt = USER_PROMPT_TEMPLATE.format(
    pages=pages, volume_guide=volume_guide,
    transcript=transcript, today_date=today,
)
```

---

### 문제 6: E-19/E-20 — React 조건부 렌더링으로 회의록 작업 state 소실

**증상**:
- 캐릭터를 드래그하면 패널이 닫히고, 다시 열면 모든 회의록 작업 내용 초기화
- 탭을 전환했다가 회의록 탭으로 돌아와도 모든 내용 초기화

**원인**:
- `{chatOpen && <ChatPanel />}` — 패널 닫힐 때 ChatPanel 언마운트
- `{chatTab === "meeting" && <MeetingView />}` — 탭 전환 시 MeetingView 언마운트
- React 컴포넌트 언마운트 → 모든 useState 초기화

**해결**: 두 곳 모두 항상 마운트하고 CSS `display:none`으로만 숨김 처리.

```tsx
// App.tsx — 패널 오픈/클로즈
<div style={{ display: chatOpen ? undefined : "none", pointerEvents: chatOpen ? undefined : "none" }}>
  <ChatPanel charPosition={charPosition} charSize={charSize} />
</div>

// ChatPanel.tsx — 탭 전환
<div style={{
  display: chatTab === "meeting" ? "flex" : "none",
  flexDirection: "column", flex: 1, overflow: "hidden", minHeight: 0,
}}>
  <MeetingView />
</div>
```

**교훈**: 다단계 wizard 컴포넌트는 조건부 렌더링 대신 CSS display:none을 써야 함. 패널 오픈/클로즈와 탭 전환 모두 동일하게 적용.

---

### 문제 7: Electron will-download 핸들러가 모든 다운로드에 .hwpx 필터 적용

**증상**: 전사 결과·회의록 텍스트를 .txt로 저장하려 해도 .hwpx 저장 대화상자가 열림

**원인**: `will-download` 이벤트 핸들러에서 파일 확장자를 확인하지 않고 무조건 `.hwpx` 필터를 적용했음

**해결**: `item.getFilename()`으로 파일명을 먼저 읽어 `.txt` 여부를 판단하고 필터 분기.

```typescript
window.webContents.session.on('will-download', (_event, item) => {
    const filename = item.getFilename() || '';
    const isTxt = filename.toLowerCase().endsWith('.txt');
    const savePath = dialog.showSaveDialogSync(window, {
        defaultPath: filename || (isTxt ? '파일.txt' : '회의결과보고서.hwpx'),
        filters: isTxt
            ? [{ name: '텍스트 파일', extensions: ['txt'] }]
            : [{ name: '한글 문서', extensions: ['hwpx'] }],
    });
    ...
});
```

---

### 문제 8: 첨부 자료 기반 업무노트가 "추측성"으로 작성됨 (E-54·E-55)

**증상**: 한글 문서(.hwpx)나 스크린샷을 첨부하고 "업무노트로 정리해줘"라고 해도, 노트에 실제 문서/이미지 내용이 담기지 않고 제목만 그럴듯한 빈껍데기 노트가 저장됨.

**원인 (복합)**:
1. **노트 강제 저장 폴백(`_force_save_note`)이 1차 소스를 안 봄** — LLM이 도구 호출을 건너뛴 note_save 턴에서, 폴백이 *사용자의 짧은 한 줄 + 비서 답변*만 요약했다. 첨부 문서의 실제 청크를 근거로 넣지 않아, 답변이 두루뭉술하면 노트도 빈약해졌다.
2. **로컬 모델(gemma4)의 이미지 OCR 부재** — `gemma4:latest`는 capabilities에 `vision`이 있어도 실제 한글 스크린샷 텍스트를 읽지 못했다(어두운 배경/추상 패턴으로 인식). 반면 `qwen2.5vl:7b`는 동일 이미지를 정확히 판독.

**해결**:
- (E-55) `_force_save_note`가 첨부 doc_id의 원문 청크(`per_doc_limit=30`)를 직접 가져와 "추측 말고 이 내용을 근거로" 노트를 작성하도록 보강.
- (E-54) **비전 모델 라우팅** 도입 — `conf.yaml`에 `app.ollama.vision_model`(예: `qwen2.5vl:7b`) 추가. 이미지가 첨부된 턴은 **전사 전용 모델이 OCR → 추출 텍스트를 메인 모델(gemma4)에 주입 → gemma4가 페르소나·도구로 답변·노트 작성**(2단계, A안). 모델 vision 플래그 ≠ 실제 OCR 품질이므로 실제 데이터로 검증 후 채택.

```python
# gemma_chat_agent.chat — 이미지 턴은 전사 후 텍스트 턴으로 전환
if batch.images and self._vision_model:
    batch = await self._transcribe_images_into_batch(batch)  # qwen OCR → 텍스트 주입
messages = self._inner._to_messages(batch)
raw_stream = self._inner._openai_tool_interaction_loop(messages, tools)  # gemma4 + 도구
```

---

### 문제 9: Ollama OpenAI-호환 엔드포인트가 `think:false`를 무시 (E-53)

**증상**: 채팅 모델을 thinking 계열(`gemma4:latest`)로 바꾸자 비전·장문 응답에서 `content`가 빈 문자열로 반환 → "응답 없음"·AgentError.

**원인**: 추론을 끄려고 `NoThinkLLM`이 `extra_body={"think": False}`를 주입했으나, **Ollama의 `/v1/chat/completions`(OpenAI 호환) 엔드포인트는 `think` 파라미터를 무시**한다(네이티브 `/api/chat`에서만 동작). 추론 토큰이 출력 예산을 잠식해 content가 비었다.

**해결**: `/v1`이 실제로 존중하는 `reasoning_effort="none"`을 함께 주입. (네이티브/`/v1` 직접 비교로 검증: `/v1`+`think:false`는 finish=length·빈 content, `/v1`+`reasoning_effort:none`은 finish=stop·정상.)

---

### 문제 10: 의도 분류기(로컬) 콜드스타트 타임아웃 → 첨부 노트 유실 (E-57)

**증상**: 첨부 파일/이미지로 노트 작성을 요청해도 산발적으로 답변만 나오고 노트가 저장되지 않음(아이콘도 작성 모드로 전환 안 됨).

**원인**: 의도 분류기를 로컬 모델(gemma4)로 전환한 뒤, **콜드스타트/모델 경합으로 첫 분류가 8초 기본 타임아웃을 초과** → `fallback_error` → 의도가 `chat`으로 강등 → note_save가 아니라 강제 저장이 동작하지 않음. (로그상 텍스트 질의는 ~1.5초, 첨부 턴은 정확히 8초에 타임아웃.)

**해결**:
1. `intent_gate.timeout_seconds: 8 → 30` (로컬 모델 콜드스타트 여유).
2. **안전망** — 분류가 실패(`fallback_error`)했더라도 첨부(문서/이미지)가 있으면 `note_save`로 강제 라우팅해 노트 유실 방지.

**교훈**: 외부 API 기준 타임아웃은 로컬 모델에 부족하다. 분류 실패 시 의도가 chat으로 떨어져 부가 동작이 통째로 사라지므로, 핵심 신호(첨부 등)에는 폴백 안전망을 둔다.

---

### 문제 11: 회의록 날짜 placeholder + rag_folders 유실 (E-52·E-56)

- **E-52**: 회의결과보고서 `next_steps[].date` 예시가 `"M.DD."`(문자 placeholder)라 LLM이 `M.15.`를 출력 → 스키마 위반 하드 실패. → 정규화 단계에서 형식 위반 날짜는 빈 문자열로 흡수(하드 실패 금지), 프롬프트 예시를 실제 숫자(`6.15.`)로 교정.
- **E-56**: RAG 폴더 정의(`rag_folders.json`)가 **비원자적 쓰기**라 프로세스 중단 시 빈 파일로 손상 → 모든 문서가 "미분류"로 떨어짐. → temp 파일 + `fsync` + `os.replace`로 **원자적 쓰기** 전환.

---

### UX 개선: 파일 링크 클릭 시 기본 앱으로 바로 열기

답변의 참고자료·노트의 관련 자료·문서 탭의 파일 링크를 클릭하면 "저장 위치 묻기" 대신 **기본 앱으로 즉시 열린다**. Electron main의 `shell:openDocument` IPC가 백엔드 loopback URL을 임시 폴더로 받아 `shell.openPath`로 연다(임시 사본을 열어 RAG 원본은 보존).

### 운영 편의: 런처가 Ollama 자동 기동

`새싹이.cmd` 실행 시 Ollama(11434)가 떠 있지 않으면 자동으로 `ollama serve`를 시작하고 준비될 때까지 대기한 뒤 백엔드를 띄운다. 기존엔 Ollama가 꺼져 있으면 백엔드가 `Ollama unreachable`로 시작 직후 종료됐다.

---

## 7. 배포 전략

### 오프라인 USB 번들

인터넷이 차단된 환경이므로 모든 의존성을 USB에 사전 패키징한다.

**번들 구조**:
```
USB (32GB 이상)/
├── shared/
│   ├── ai-assistant/        ← 소스 코드 (OS 공통)
│   └── models/              ← AI 모델 파일 (OS 공통, ~5GB)
│       ├── Whisper large-v3-turbo
│       └── BGE-M3 임베딩 모델
├── macos/
│   ├── install.sh           ← macOS 원클릭 설치
│   ├── python-3.12.pkg
│   └── wheels/
│       ├── arm64/           ← Apple Silicon
│       └── x86_64/          ← Intel Mac
├── windows/
│   ├── install.bat          ← Windows 원클릭 설치
│   ├── python-3.12-amd64.exe
│   └── wheels/
│       ├── cpu/
│       └── cuda/            ← NVIDIA GPU
└── ollama/
    ├── Ollama-macos.dmg
    ├── OllamaSetup.exe
    └── models/              ← Ollama 모델 파일 (~2GB)
```

**번들 생성**: `bash scripts/bundle_usb.sh /Volumes/USB명`

### 설치 자동화

**Windows (`install.bat`)**:
1. Python 3.12 설치 확인 → 없으면 USB의 설치 파일 실행
2. Ollama 설치 (`/S` 조용한 설치)
3. `nvidia-smi` 감지 → RTX GPU면 CUDA wheels, 없으면 CPU wheels 선택
4. venv 생성 → `pip install --find-links` 로컬 설치
5. MeloTTS + eunjeon (Windows 한국어 형태소 분석기) 설치
6. Ollama 모델 복사
7. 바탕화면 바로가기 자동 생성

**macOS (`install.sh`)**:
1. Python 3.12 확인 → 없으면 `.pkg` 설치
2. Ollama DMG 마운트·설치
3. venv 생성 → pip 로컬 설치
4. `post_install.py` 실행 → mecab_shim 적용, MeCab Tagger 스텁 패치
5. Ollama 모델 복사

### 서버 실행

```bash
# macOS/Linux
bash start.sh

# Windows
start.cmd
```

---

## 8. 현재 상태 및 잔여 과제

### 완료 현황

| 모듈 | 상태 | Critic |
|---|---|---|
| M_01 AppCore | ✅ DONE | PASS |
| M_02 ASREngine | ✅ DONE | PASS |
| M_03 VADEngine | ✅ DONE | PASS |
| M_04 TTSEngine | ✅ DONE | PASS (4차) |
| M_05 LLMAgent | ✅ DONE | PASS |
| M_05b ToolRouter | ✅ DONE | PASS |
| M_06 DocumentIngest | ✅ DONE | PASS (2차) |
| M_07 VectorSearch | ✅ DONE | PASS |
| M_08 AvatarState | ✅ DONE | PASS |
| M_09 CalendarService | ✅ DONE | PASS |
| M_10 IdleMonitor | ✅ DONE | PASS (2차) |
| M_11 ProactiveDispatcher | ✅ DONE | PASS (3차) |
| M_12 Frontend | ✅ DONE | PASS (3차) |
| M_13 MeetingMinutes | ✅ DONE | 실기기 검증 완료 |

**현재 버전 추가 기능 (2026-06 갱신)**:

| 기능 | 상태 | 비고 |
|---|---|---|
| 멀티모달 노트 — 이미지/스크린샷 OCR | ✅ | 비전 모델(`qwen2.5vl`) 전사 → gemma4 정리 (A안 2단계) |
| 멀티모달 노트 — 첨부 문서(.hwpx 등) 내용 기반 | ✅ | 폴백이 원문 청크를 직접 근거로 작성 (E-55) |
| 모달리티별 모델 라우팅 | ✅ | `vision_model` 설정으로 OCR 전용 모델 분리 |
| 첨부 노트 의도 안전망 | ✅ | 분류 실패 시에도 첨부 있으면 note_save (E-57) |
| 파일 링크 바로 열기 | ✅ | 다운로드 대화상자 없이 기본 앱 실행 |
| 로컬 LLM thinking 비활성화 | ✅ | `/v1` `reasoning_effort:none` (E-53) |
| 런처 Ollama 자동 기동 | ✅ | `새싹이.cmd`가 Ollama 헬스체크 후 자동 시작 |

> 누적 버그/교훈 기록: `docs/ERROR_HISTORY.md` (E-01 ~ E-57)

### 동작 확인 (Mac Mini M 시리즈 기준)

| 기능 | 상태 |
|---|---|
| 텍스트 채팅 | ✅ 정상 동작 |
| LLM 응답 (Gemma 4 E4B) | ✅ 정상, thinking 비활성화 |
| TTS (MeloTTS 한국어) | ✅ 합성 확인 (WAV 출력) |
| 하드웨어 자동 감지 | ✅ Apple MPS 감지, Whisper cpu/int8 설정 |
| 회의록 전사 (Step 1) | ✅ M4A 45분 파일 → 32,918자 전사 확인 |
| 회의록 요약 (Step 2) | ✅ pages=2 → 3,836자 회의록 생성 확인 |
| HWPX 생성 (Step 3) | ✅ 149.9KB .hwpx 다운로드 확인 |
| TTS 단계 안내음성 | ✅ 각 단계 완료 시 한국어 안내음성 재생 |
| 작업 state 보존 | ✅ 패널 닫기·탭 전환 모두 state 유지 확인 |
| .txt/.hwpx 다운로드 분기 | ✅ 파일 형식에 따라 올바른 저장 대화상자 |

### 잔여 과제

| 항목 | 내용 |
|---|---|
| Windows QA | M_12 Electron 앱의 펫 모드·인용 뷰어·NSIS 설치 패키지 Windows 실기기 검증 |
| USB 번들 생성 | `bundle_usb.sh` 실행 + Ollama/Python 설치 파일 수동 추가 |
| XTTS v2 법무 검토 | Coqui CPML 라이선스 사내 법무 승인 후 활성화 가능 |
| ASR 브라우저 E2E | 마이크 → STT → LLM → TTS 전체 파이프라인 실기기 테스트 |

---

## 부록: 주요 파일 구조

```
ai-assistant/
├── src/
│   ├── app/              # M_01 AppCore (FastAPI 진입점, 설정, 하드웨어 감지)
│   ├── asr/              # M_02 ASREngine (faster-whisper 래퍼)
│   ├── tts/              # M_04 TTSEngine (MeloTTS, XTTS v2)
│   ├── agent/            # M_05 LLMAgent (GemmaChatAgent, 어댑터)
│   ├── tool_router/      # M_05b ToolRouter
│   ├── ingest/           # M_06 DocumentIngest
│   ├── vector_search/    # M_07 VectorSearch (BGE-M3, LanceDB)
│   ├── avatar/           # M_08 AvatarState
│   ├── calendar/         # M_09 CalendarService
│   ├── idle/             # M_10 IdleMonitor
│   ├── proactive/        # M_11 ProactiveDispatcher
│   └── meeting_minutes/  # M_13 MeetingMinutes (generator, prompts)
├── frontend/             # M_12 Electron 메인 프로세스 (IPC, 윈도우 관리)
├── web/                  # M_12 React 웹앱 (UI 컴포넌트)
├── upstream/             # Open-LLM-VTuber (수정 금지)
├── vendor/
│   └── mecab_shim/       # macOS mecab 호환 패키지 (HFS+ 대소문자 우회)
├── deploy/               # 배포 스크립트 (install.sh, install.bat, start.*)
├── scripts/              # 개발 도구 (bundle_usb.sh 등)
├── tests/                # 단위·통합·E2E 테스트
├── assets/
│   ├── character/        # 새싹이 스프라이트 PNG 9종
│   └── models/           # AI 모델 파일 (git 제외)
├── docs/
│   ├── ERROR_HISTORY.md  # 버그 원인·해결·교훈 (E-01 ~ E-57)
│   ├── FRONTEND_CONSTRAINTS.md
│   ├── ARCHITECTURE.md
│   └── USER_GUIDE.md
├── conf.yaml             # 서버 설정
└── REQUIREMENTS.md       # 요구사항 단일 진실 공급원
```

---

*GitHub: https://github.com/soonkun/ai-assistant*

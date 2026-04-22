# 새싹이 AI 비서 — 기술 개발 보고서

> **프로젝트**: 사내 인트라넷 오프라인 AI 비서 "새싹이(Saessagi)"  
> **작성일**: 2026-04-23  
> **버전**: v1.0 (초도 완성 기준)

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
- **캐릭터 아바타 "새싹이"**: 투명 배경의 펫 모드로 항상 화면 위에 표시
- **완전 오프라인**: 네트워크 호출 제로. 모든 모델·의존성 로컬 번들

### 개발 방식

`Open-LLM-VTuber`(이하 "upstream")를 베이스 프레임워크로 채택해 상속·확장하는 방식을 선택했다. 처음부터 다 만드는 대신 검증된 WebSocket 파이프라인(ASR → VAD → LLM → TTS)을 재사용하고, 사내 요구사항에 맞는 기능(RAG, 캘린더, 유휴 감지, 스프라이트 아바타, 펫 모드)을 추가했다.

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
| 캐릭터 아바타 | 스프라이트 7종, 펫 모드(투명·클릭 관통·항상 위), 감정 연동 |
| 일정 관리 | 자연어 파싱 → 함수 호출 → SQLite 저장, 10분 전 알림, 아침 브리핑 |
| 유휴 감지 | 45분 무입력 → 휴식 권고, 2시간 연속 → 과로 경고 |
| 화면 인식 | 사용자 요청 시 스크린샷 → 멀티모달 LLM 분석 |

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
├── FastAPI 0.115     — WebSocket 서버
├── asyncio           — 비동기 파이프라인
├── Ollama            — LLM 로컬 런타임
├── faster-whisper    — CTranslate2 기반 STT
├── MeloTTS           — 한국어 TTS
├── LanceDB           — 벡터 저장소 (임베드)
├── SQLite            — 캘린더 데이터
└── pydantic v2       — 설정 스키마 검증
```

### 프론트엔드

```
Electron 35
├── React 18          — UI 컴포넌트
├── Vite              — 번들러
├── TypeScript        — 타입 안전성
└── Playwright        — E2E 자동화 테스트
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
 ┌─────────────────────────────────────────────────────────┐
 │              FRONTEND (Electron + React)                 │
 │  ┌─────────────┐  ┌───────────────┐  ┌───────────────┐  │
 │  │  Chat UI    │  │ Sprite Avatar │  │   Pet Mode    │  │
 │  │ Text Input  │  │  (새싹이)     │  │  (투명 위젯)  │  │
 │  └──────┬──────┘  └───────┬───────┘  └───────┬───────┘  │
 │         └─────────────────┴──────────────────┘          │
 │                     WebSocket client                     │
 └──────────────────────────┬──────────────────────────────┘
                            │ ws://127.0.0.1:12393
 ┌──────────────────────────▼──────────────────────────────┐
 │                APPLICATION LAYER (FastAPI)               │
 │  ┌─────────────────────────────────────────────────┐    │
 │  │ AppServiceContext (upstream ServiceContext 확장) │    │
 │  │  + RagService · CalendarService · IdleMonitor   │    │
 │  │  + AvatarState · ProactiveDispatcher            │    │
 │  └──────┬──────────┬──────────┬───────────────────┘    │
 │         │          │          │                         │
 │     ┌───▼──┐  ┌────▼────┐  ┌──▼───────────────────┐   │
 │     │ ASR  │  │   LLM   │  │ ProactiveDispatcher   │   │
 │     │(M_02)│  │(M_05+FC)│  │ (M_11, APScheduler)  │   │
 │     └───┬──┘  └────┬────┘  └──────────────────────┘   │
 │         │          │                                    │
 │     ┌───▼──┐  ┌────▼──────────────────────────────┐   │
 │     │ VAD  │  │ ToolRouter (M_05b)                 │   │
 │     │(M_03)│  │  search_docs → RAG (M_06/M_07)    │   │
 │     └──────┘  │  add_event  → Calendar (M_09)     │   │
 │               │  screenshot → 화면분석             │   │
 │               └───────────────────────────────────┘   │
 │                          │                             │
 │                     ┌────▼────┐                        │
 │                     │   TTS   │                        │
 │                     │ (M_04)  │                        │
 │                     └─────────┘                        │
 └─────────────────────────────────────────────────────────┘
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
Gemma 4는 기본적으로 모든 응답에 Chain-of-Thought 추론(thinking)을 사용한다. 일상 대화에서 수 초의 불필요한 대기가 발생하므로, Ollama API 호출 시 `extra_body={"think": False}`를 주입해 기본적으로 비활성화했다. 필요 시 tool call 내부에서만 활성화하는 방식으로 속도와 품질을 양립시켰다.

---

## 5. 모듈별 개발 히스토리

### 개발 순서 (의존성 그래프 순)

```
M_01 → M_02 → M_03 → M_04 → M_05 → M_05b
                                       ↓
M_09 → M_07 → M_06 → M_08 → M_10 → M_11 → M_12
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

**해결한 문제**:
- upstream의 `ServiceContext`가 `app_config`를 `init_agent` 내부에서 참조하는데, `load_from_config` 이전에 주입이 필요했음. lifespan 함수에서 `ctx.app_config = full_config.app`을 먼저 실행하는 방식으로 해결.
- ASR 모델 미배치 시 앱 전체가 기동 실패하는 문제: `init_asr`를 try/except로 감싸 graceful degradation 구현 (음성 없이 텍스트 채팅만 동작)

---

### M_02 — ASREngine (EXTEND)

**역할**: 한국어 faster-whisper 래퍼

**주요 작업**:
- upstream `FasterWhisperASR`를 한국어 설정(`language="ko"`, `compute_type="int8"`)으로 초기화
- 모델: `large-v3-turbo` (최신 경량 버전, ~1.5GB, CPU 친화적)
- 하드웨어 적응형 설정 자동 적용

**모델 다운로드**:
- HuggingFace Hub에서 `mobiuslabsgmbh/faster-whisper-large-v3-turbo` 다운로드
- `assets/models/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/` 로컬 배치

---

### M_03 — VADEngine (REUSE)

**역할**: 발화 구간 감지 (Voice Activity Detection)

upstream `SileroVAD`를 그대로 사용. 설정값(`prob_threshold=0.4`, `db_threshold=60`)만 조정.

---

### M_04 — TTSEngine (NEW)

**역할**: 한국어 음성 합성 (MeloTTS)

**주요 작업**:
- `MeloTTSEngine`: upstream `TTSInterface` 구현
- 동기 `generate_audio()` + 비동기 `async_generate_audio()` 양쪽 지원
- asyncio.Lock으로 동시 합성 요청 직렬화
- Path traversal 방어 (출력 파일명 basename 강제)
- XTTS v2 옵션 코드 포함 (법무 승인 대기 중, Python < 3.12 + Windows 전용)

**까다로웠던 문제** (아래 §6에서 상세 설명):
- MeloTTS 의존성 체인이 복잡해 단순 `pip install`로 설치 불가
- macOS에서 `import mecab`이 실패 — HFS+ 대소문자 비구분 문제

---

### M_05 — LLMAgent (EXTEND)

**역할**: Gemma 4 E4B 기반 대화 에이전트

**구조**:
```
GemmaChatAgent          ← 프로젝트 고유 Ollama 래퍼
    │
    └── BasicMemoryAgentAdapter  ← upstream AgentInterface 호환 어댑터
            │
            └── upstream BasicMemoryAgent  ← 대화 히스토리 관리
```

**주요 작업**:
- `GemmaChatAgent`가 OpenAI 호환 Ollama API를 직접 호출, 스트리밍 응답 생성
- `BasicMemoryAgentAdapter`가 upstream `AgentInterface`를 만족하면서 `SentenceOutput` 타입으로 변환 (upstream의 `process_single_conversation`이 string을 무시하고 `SentenceOutput`만 처리함)
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

**주요 설계**:
- `doc_id = SHA-256(절대경로)[:32]` — 경로 기반 중복 방지 (mtime 포함 시 파일 수정마다 청크가 누적되는 결함이 R1 Critic에서 발견, path-only로 수정)
- HWPX: 네임스페이스 `2011/hwpml/2.0`·`2016/hwpml/2.1` 양쪽 지원
- XXE 방어: `defusedxml` 적용
- 페이지 번호·섹션·bbox 메타데이터 보존

---

### M_07 — VectorSearch (NEW)

**역할**: BGE-M3 임베딩 + LanceDB 벡터 검색 + 인용 포매터

**주요 작업**:
- `sentence-transformers` + `BGE-M3`으로 다국어(한국어 포함) 임베딩
- LanceDB embedded 모드 — 서버 없이 로컬 디스크에 직접 저장
- 인용 포매팅: `"이 내용은 {문서명} {페이지}페이지에 있습니다"` 형식
- `rag_min_score=0.35` 임계값 미만 결과 필터링

---

### M_08 — AvatarState (NEW)

**역할**: LLM 출력에서 감정 태그 파싱 → 스프라이트 상태 이벤트 송신

LLM 응답의 `[emotion:happy]` 같은 태그를 감지해 프론트엔드에 `set-avatar-expression` 메시지 전송. 표정 세트: neutral·happy·surprised·sad·worried·thinking·tired (7종).

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
| P3 | 펫 모드 — 투명 배경, 항상 위, 클릭 관통, 드래그 이동 |
| P4 | pdf.js 인용 뷰어 — 원문 페이지 열기, 하이라이트, bbox 포지셔닝 |
| P5 | RSS 문서 감시, 적대적 테스트, E2E skeleton, NSIS 설치 패키지 |

**Windows QA 항목** (실기기 미수행):  
Playwright E2E 3종(펫 모드·인용 뷰어·기본 채팅), click-through hover IPC, CSP 실차단, electron-builder NSIS 패키지 검증.

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
- `mecab/__init__.py` — `__getattr__`을 이용한 지연 임포트로 순환 임포트 차단 (`MeCab/mecab.py`가 로딩 중일 때 `mecab.types`를 요구하는데, 이 시점에 `mecab/__init__.py`가 다시 `MeCab`을 임포트하면 순환이 발생)
- `MeCab/__init__.py` — `Tagger` 스텁 클래스 추가 (MeloTTS가 일본어 모듈 로딩 시 모듈 레벨에서 `MeCab.Tagger()`를 호출함 — Korean 전용 배포에서는 실제로 호출되지 않으므로 스텁으로 충분)
- **Windows 대응**: Windows에서 g2pkk는 mecab 대신 `eunjeon`을 사용하므로 해당 문제가 없음

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

### 문제 4: MagicMock 메타클래스 문제 (Python 3.12 테스트)

**증상**: Python 3.12에서 `ServiceContext`를 `MagicMock`으로 만들고 상속받으면 서브클래스 자체가 MagicMock이 됨

**해결**: `conftest.py`에서 `_ServiceContextStub` 클래스를 별도 생성하고, MagicMock으로 실제 upstream 클래스를 대체하는 대신 `__spec__`을 `ModuleSpec`으로 설정.

---

### 문제 5: 크로스플랫폼 Python 패키지 의존성

**문제**: `tokenizers==0.13.3` (MeloTTS 의존)이 Python 3.12에서 Rust 컴파일 필요 — 빌드 실패

**해결**: `pip install melo --no-deps`로 설치 후 누락된 의존성을 수동으로 하나씩 설치.

**최종 추가 의존성 목록**: librosa, cn2an, mecab-python3, num2words, pykakasi, fugashi, g2p-en, anyascii, jamo, g2pkk, gruut, eng-to-ipa, unidecode, langid, tensorboard, txtsplit, cached-path, python-mecab-ko

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
│   ├── python-3.12.pkg      ← Python 설치 파일
│   └── wheels/              ← macOS pip wheels
│       ├── arm64/           ← Apple Silicon
│       └── x86_64/          ← Intel Mac
├── windows/
│   ├── install.bat          ← Windows 원클릭 설치
│   ├── python-3.12-amd64.exe
│   └── wheels/
│       ├── cpu/             ← CPU 전용
│       └── cuda/            ← NVIDIA GPU (RTX 시리즈)
└── ollama/
    ├── Ollama-macos.dmg
    ├── OllamaSetup.exe
    └── models/              ← Ollama 모델 파일 (~2GB)
```

**번들 생성**: `bash scripts/bundle_usb.sh /Volumes/USB명`  
→ pip download로 각 플랫폼별 wheel 자동 다운로드, rsync로 소스·모델 복사

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
# macOS
bash deploy/start.sh

# Windows
deploy\start.bat
```

브라우저에서 `http://127.0.0.1:12393` 접속.

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

모든 모듈에 대해 단위 테스트(정상 ≥5, 엣지 ≥5, 적대 ≥3), ruff 린트, mypy 타입 검사, Critic 독립 리뷰가 완료됐다.

### 동작 확인 (Mac Mini M 시리즈 기준)

| 기능 | 상태 |
|---|---|
| 텍스트 채팅 | ✅ 정상 동작 |
| LLM 응답 (Gemma 4 E2B) | ✅ 정상, thinking 비활성화 |
| TTS (MeloTTS 한국어) | ✅ 합성 확인 (WAV 출력) |
| 하드웨어 자동 감지 | ✅ Apple MPS 감지, Whisper cpu/int8 설정 |
| 페르소나 (새싹이) | ✅ 간결한 답변 지침 적용 |
| ASR (Whisper) | 모델 다운 완료, 브라우저 마이크 E2E 미확인 |

### 잔여 과제

| 항목 | 내용 |
|---|---|
| ASR E2E 확인 | 브라우저 마이크 → STT → LLM → TTS 전체 파이프라인 실기기 테스트 |
| Windows QA | M_12 Electron 앱의 펫 모드·인용 뷰어·NSIS 설치 패키지 Windows 실기기 검증 |
| USB 번들 생성 | `bundle_usb.sh` 실행 + Ollama/Python 설치 파일 수동 추가 |
| XTTS v2 법무 검토 | Coqui CPML 라이선스 사내 법무 승인 후 활성화 가능 |

---

## 부록: 주요 파일 구조

```
ai-assistant/
├── src/
│   ├── app/          # M_01 AppCore (FastAPI 진입점, 설정, 하드웨어 감지)
│   ├── asr/          # M_02 ASREngine (faster-whisper 래퍼)
│   ├── tts/          # M_04 TTSEngine (MeloTTS, XTTS v2)
│   ├── agent/        # M_05 LLMAgent (GemmaChatAgent, 어댑터)
│   ├── tools/        # M_05b ToolRouter
│   ├── ingest/       # M_06 DocumentIngest
│   ├── vector_search/# M_07 VectorSearch (BGE-M3, LanceDB)
│   ├── avatar/       # M_08 AvatarState
│   ├── calendar/     # M_09 CalendarService
│   ├── idle/         # M_10 IdleMonitor
│   └── proactive/    # M_11 ProactiveDispatcher
├── upstream/         # Open-LLM-VTuber (수정 금지)
├── vendor/
│   └── mecab_shim/   # macOS mecab 호환 패키지 (HFS+ 대소문자 우회)
├── deploy/           # 배포 스크립트 (install.sh, install.bat, start.*)
├── scripts/          # 개발 도구 (bundle_usb.sh 등)
├── tests/            # 단위·통합·E2E 테스트
├── assets/
│   ├── character/    # 새싹이 스프라이트 PNG 7종
│   └── models/       # AI 모델 파일 (git 제외)
├── conf.yaml         # 서버 설정
└── REQUIREMENTS.md   # 요구사항 단일 진실 공급원
```

---

*이 문서는 프로젝트 완성 시점(2026-04-23) 기준으로 작성됐습니다.*  
*GitHub: https://github.com/soonkun/ai-assistant*

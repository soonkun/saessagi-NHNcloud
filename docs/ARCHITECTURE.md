# ARCHITECTURE — 사내 오프라인 AI 비서 "새싹이"

본 문서는 Phase 1 설계 산출물이다. `REQUIREMENTS.md`, `docs/GAP_ANALYSIS.md`, `docs/research/*.md`를
근거로 작성되었으며, Phase 2 이후 모든 모듈 구현의 단일 근거로 사용된다.

- 대상 OS: Windows 10/11 전용
- 베이스: `upstream/Open-LLM-VTuber` (이하 "upstream")
- 런타임: Python 3.12 + FastAPI + WebSocket + Ollama(로컬) + LanceDB(임베드) + SQLite
- 외부 네트워크: 0건 (엄격 금지, REQUIREMENTS.md §9)

---

## 1. 전체 블록 다이어그램

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         FRONTEND (Electron + React)                     │
 │  ┌───────────────────────┐  ┌────────────────┐  ┌───────────────────┐   │
 │  │ Chat UI / Text Input  │  │ Sprite Avatar  │  │ Pet Mode Window   │   │
 │  │  (fork upstream-Web)  │  │  (M_08 NEW)    │  │  (transparent,    │   │
 │  │                       │  │                │  │   always-on-top,  │   │
 │  │                       │  │                │  │   click-through)  │   │
 │  └───────────┬───────────┘  └────────┬───────┘  └─────────┬─────────┘   │
 │              │                       │                    │             │
 │              └───────────────────────┼────────────────────┘             │
 │                                      ▼                                  │
 │                         WebSocket client (JSON messages)                │
 └──────────────────────────────────────┼──────────────────────────────────┘
                                        │ ws://127.0.0.1:12393/client-ws
 ┌──────────────────────────────────────▼──────────────────────────────────┐
 │                   APPLICATION LAYER (FastAPI + asyncio)                 │
 │                                                                         │
 │  ┌───────────────────────────────────────────────────────────────────┐  │
 │  │ WebSocketHandler (REUSE upstream/websocket_handler.py)            │  │
 │  │  routes: text-input, mic-audio-*, interrupt-signal,               │  │
 │  │          ai-speak-signal, screenshot-trigger(NEW)                 │  │
 │  └─────┬───────────────────────────────────────────┬─────────────────┘  │
 │        │                                           │                    │
 │  ┌─────▼───────────────────────────┐   ┌───────────▼──────────────────┐ │
 │  │ ServiceContext (EXTEND)         │   │ ProactiveDispatcher (M_10)   │ │
 │  │  + asr, vad, tts, agent         │   │  (스케줄 10분전 알림,        │ │
 │  │  + rag_service (M_06)           │   │   휴식 권고, 아침 브리핑)    │ │
 │  │  + calendar_service (M_09)      │   │                              │ │
 │  │  + idle_monitor (M_10)          │   └──────────────┬───────────────┘ │
 │  │  + avatar_state (M_08)          │                  │                 │
 │  └─────┬──────────┬────────┬───────┘                  │                 │
 │        │          │        │                          │                 │
 │        ▼          ▼        ▼                          ▼                 │
 │  ┌─────────────────────────────────────────────────────────────────┐    │
 │  │ ConversationOrchestrator (EXTEND upstream/single_conversation)  │    │
 │  │   1) ASR (M_02)  2) Agent(LLM + FC) (M_05)  3) TTS (M_04)       │    │
 │  │   2a) RAG retrieval if tool_call==search_docs (M_06/M_07)       │    │
 │  │   2b) Calendar op if tool_call==add_event/... (M_09)            │    │
 │  └─────────────────────────────────────────────────────────────────┘    │
 └─────────────────────────────────────────────────────────────────────────┘
                │                   │                    │
                ▼                   ▼                    ▼
 ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
 │   SERVICE LAYER      │ │   SERVICE LAYER      │ │   SERVICE LAYER      │
 │                      │ │                      │ │                      │
 │ M_02 ASR             │ │ M_05 LLM Agent       │ │ M_06 Document Ingest │
 │  faster-whisper      │ │  Ollama HTTP client  │ │  PDF: pypdfium2      │
 │  (EXTEND upstream)   │ │  gemma4:e4b          │ │  DOCX: python-docx   │
 │                      │ │  native tool calling │ │  PPTX: python-pptx   │
 │ M_03 VAD             │ │  (M_05 EXTEND)       │ │  HWPX: zipfile+lxml  │
 │  silero-vad          │ │                      │ │  (M_06 NEW)          │
 │  (REUSE upstream)    │ │ M_05b Tool Router    │ │                      │
 │                      │ │  add_event,          │ │ M_07 Vector Search   │
 │ M_04 TTS             │ │  get_events,         │ │  BGE-M3 embedder     │
 │  MeloTTS (default)   │ │  search_docs,        │ │  LanceDB store       │
 │  XTTS v2 (optional)  │ │  take_screenshot,    │ │  citation formatter  │
 │  (M_04 NEW adapter,  │ │  filesystem tools    │ │  (M_07 NEW)          │
 │   REUSE TTSInterface)│ │  (M_05b NEW)         │ │                      │
 └──────────────────────┘ └──────────────────────┘ └──────────────────────┘
                                     │
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       INFRASTRUCTURE LAYER                              │
 │                                                                         │
 │  ┌────────────────────────────┐   ┌──────────────────────────────────┐  │
 │  │ Ollama (native process)    │   │ Local Filesystem (Windows)       │  │
 │  │  gemma4:e4b (8.5 GB RAM)   │   │  assets/models/ (GGUF, ONNX)     │  │
 │  │  131K context, tool calls  │   │  data/vector_store/ (LanceDB, 유일한 벡터 스토어) │  │
 │  │  dev  : 192.168.219.109    │   │  data/calendar.db (SQLite)       │  │
 │  │  prod : 127.0.0.1:11434    │   │  data/chat_history/ (JSON)       │  │
 │  └────────────────────────────┘   │  data/logs/ (loguru, 7d retain)  │  │
 │                                   └──────────────────────────────────┘  │
 │  ┌────────────────────────────┐   ┌──────────────────────────────────┐  │
 │  │ Windows API (M_10)         │   │ MCP Servers (REUSE upstream/mcpp)│  │
 │  │  pynput: keyboard/mouse    │   │  filesystem MCP (allowed roots)  │  │
 │  │  mss: screenshot           │   │  ddg-search: disabled offline    │  │
 │  │  pywin32: always-on-top    │   │  time: enabled                   │  │
 │  └────────────────────────────┘   └──────────────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 레이어 구조

| 레이어 | 책임 | 주요 모듈 | 업스트림 활용도 |
|---|---|---|---|
| UI | Electron 프론트엔드, 텍스트 채팅, 스프라이트 아바타, 펫 모드 창 | M_08, M_12 | FORK (upstream-Web 브랜치 `build`) + 스프라이트 렌더러 신규 교체 |
| 오케스트레이터 | WebSocket 수신, ServiceContext, ConversationOrchestrator, ProactiveDispatcher | M_01, M_11 | REUSE + EXTEND (ServiceContext 확장) |
| 서비스 | ASR / VAD / TTS / LLM Agent / RAG / Calendar / Idle | M_02~M_07, M_09, M_10 | REUSE 3 (VAD, ASR 골격, MCP), EXTEND 4 (TTS, Agent, ServiceContext, Orchestrator), NEW 5 (RAG, Calendar, Idle, Screenshot, Sprite) |
| 인프라 | Ollama, LanceDB, SQLite, Windows API, 파일 시스템 | (서비스가 직접 호출) | 외부 바이너리(Ollama), 라이브러리 의존만 |

---

## 3. 데이터 흐름 (주요 시나리오)

### 3.1 음성 대화 + 인터럽트

```
[Mic] → frontend(AudioWorklet) --ws:mic-audio-data(float32 chunks)--> WebSocketHandler
      → SileroVAD.feed()
         ├─ <|PAUSE|> (speech start)  → send {type:"control",text:"interrupt"}
         └─ <|RESUME|> (speech end)   → ASR.transcribe(buf) → Agent.chat(text)
                                                           → TTS.synthesize(stream)
                                                           → ws:audio (chunked)
      [사용자 다시 발화] → VAD가 다시 <|PAUSE|> 내면 Agent.handle_interrupt()
                         → TTS 큐 drain, WebSocket 오디오 송신 취소
```

재사용 근거: `upstream/src/open_llm_vtuber/vad/silero.py`(StateMachine 완성),
`upstream/src/open_llm_vtuber/agent/agents/basic_memory_agent.py`의 `handle_interrupt()`,
`upstream/src/open_llm_vtuber/conversations/conversation_handler.py`의 `<|PAUSE|>` 라우팅이 이미 존재.

### 3.2 RAG 질의응답 (인용 포함)

```
text-input("승인 절차 알려줘")
  → Agent.chat() → Gemma native tool call
      → {"tool":"search_docs","arguments":{"query":"승인 절차","top_k":5}}
  → M_07 VectorSearch.retrieve(query) → BGE-M3 embed → LanceDB ANN → top_k chunks
  → Agent가 chunks+citations를 context로 받아 최종 응답 생성
  → 응답 페이로드: {"answer": str, "citations":[{doc, page, section, chunk_id}, ...]}
  → frontend: 답변 텍스트 + 인용 배지 렌더, 클릭 시 PDF viewer(M_12)에서 해당 페이지 열기
```

관련 없음 판정: LanceDB 상위 1건의 cosine score가 임계치(0.35, BGE-M3 기본값 기준) 미달이면
"등록된 문서에서 답을 찾지 못했습니다"로 고정 응답. REQUIREMENTS.md §2.2 "추측 금지" 대응.

### 3.3 일정 등록 (자연어 → function call)

```
text-input("내일 오후 3시에 마케팅 팀 회의 있어")
  → Agent.chat() → Gemma native tool call
      → {"tool":"add_event",
         "arguments":{"title":"마케팅 팀 회의","start":"2026-04-19T15:00:00","duration_minutes":60}}
  → M_09 CalendarService.add_event(**args) → SQLite INSERT
  → Agent가 성공 응답을 한국어 자연어로 반환 ("내일 15시 마케팅 팀 회의 등록했어요")
```

스파이크 결과 근거: `docs/research/gemma_function_calling_spike.md` — 함수 선택 10/10, 인자 정확도 100%.

### 3.4 프로액티브 발화 (휴식 권고 + 아침 브리핑 + 알림)

```
M_10 IdleMonitor (pynput 훅)
  ├─ 45분 무입력 → ProactiveDispatcher.emit("idle_rest")
  ├─ 2시간 연속 입력 → ProactiveDispatcher.emit("overwork")
  └─ DND 모드 ON → 이벤트 drop

M_11 ProactiveDispatcher (APScheduler)
  ├─ 매일 09:00 → emit("morning_briefing")
  ├─ 일정 10분 전 SQLite 폴링 → emit("event_reminder", event_id)
  └─ 쿨다운(동일 이벤트 30분) 위반 시 drop

emit() → WebSocket send_text({"type":"ai-speak-signal","text":<prompt>})
       → ConversationOrchestrator가 upstream의 프로액티브 경로 재사용하여
         Agent→TTS→ws:audio 파이프라인 구동
```

upstream 근거: `websocket_handler.py`의 `ai-speak-signal` 핸들러와
`conversation_handler.py`의 프로액티브 발화 분기가 이미 존재.

### 3.5 화면 인식

```
text-input("화면 봐줘") or UI 버튼
  → frontend: 명시적 사용자 트리거 확인 → ws:screenshot-trigger
  → M_05b ScreenshotTool(mss.mss()) → PNG bytes → base64
  → Agent가 BatchInput{text, images:[ImageSource.SCREEN]}로 Ollama 멀티모달 호출
  → 텍스트 응답
```

연속 모드: `start-continuous-capture(interval=N)` 이벤트 수신 후 서버가 N초마다 emit.
개인정보 경고 텍스트를 시작 시 1회 표출(REQUIREMENTS.md §6).

---

## 4. upstream 재사용·확장 분류 (요약)

자세한 GAP은 `docs/GAP_ANALYSIS.md` 참고. 본 섹션은 아키텍처 결정만 요약.

| 컴포넌트 | 결정 | 비고 |
|---|---|---|
| FastAPI 서버 골격 (`server.py`, `routes.py`) | REUSE | 엔드포인트 `/client-ws` 유지. 앱 래퍼만 신규. |
| WebSocketHandler | REUSE + 메시지 타입 2개 추가 | `screenshot-trigger`, `start-continuous-capture` 추가. |
| ServiceContext | EXTEND | `rag_service`, `calendar_service`, `idle_monitor`, `avatar_state` 필드 신설. |
| ConversationOrchestrator | EXTEND | RAG 호출 루프와 tool call 결과 주입 경로 추가. |
| ASR (`faster_whisper_asr.py`) | REUSE + 설정 | `language="ko"`, `compute_type="int8"`, `model_size_or_path=large-v3`. |
| VAD (`vad/silero.py`) | REUSE (무수정) | 설정만 튜닝. |
| TTS Interface | REUSE, 구현체는 교체 | `MeloTTSEngine`(M_04) 신규. Piper 한국어 모델 부재로 upstream Piper 구현 폐기. |
| LLM (`ollama_llm.py`) | EXTEND (BasicMemoryAgent 경유) | `model="gemma4:e4b"`, `base_url` dev/prod 분기. |
| MCP 프레임워크 (`mcpp/`) | REUSE | 필터: `ddg-search` 오프라인 비활성, `filesystem-mcp` 추가. |
| Live2D 모델 / 렌더러 | DROP | 스프라이트 스왑(M_08)으로 전면 교체. `extract_emotion()`만 함수 수준 REUSE. |
| 채팅 히스토리 | REUSE | `chat_history_manager.py` 그대로. |
| 프론트엔드 (`frontend` 서브모듈) | FORK | 체크아웃 후 펫 모드·렌더러 구조 조사(M_12). |

---

## 5. 핵심 설계 결정 (Decision Records)

### D-01: 벡터 DB는 LanceDB를 채택한다 (Qdrant 아님)

- **결정**: `lancedb` 파이썬 패키지를 단일 벡터 저장소로 사용한다.
- **근거**:
  - 파일 기반 임베드 DB. 외부 프로세스 불필요 → 오프라인 인스톨러 단순화.
  - Qdrant는 별도 바이너리 또는 Docker 필요. 사내 Windows PC에 Docker Desktop 전제 불가.
  - Arrow 포맷 네이티브 저장으로 BGE-M3 1024차원 float32 벡터를 메타데이터(`doc_id`, `page`, `section`, `chunk_id`, `bbox`)와 함께 한 테이블에 두기 적합.
  - MIT 라이선스 (상업 사용 제한 없음).
- **대안 비교**:
  - Qdrant embedded: Python 바인딩 있으나 Windows 휠 경험 부족, 리소스 오버헤드 ↑.
  - FAISS: 메타데이터 저장 별도 스키마 수작업 필요. 운영 비용 ↑.
  - Chroma: SQLite 기반, 단 대용량 ANN 성능 하락 보고. 오프라인 배포 난이도는 LanceDB와 동급.

### D-02: LLM은 Gemma 4 E4B + Ollama 네이티브 tool calling을 채택한다

- **결정**: `gemma4:e4b` 모델을 Ollama로 서빙하고, OpenAI 호환 `tools` 파라미터로 함수를 호출한다.
- **근거**: `docs/research/gemma_function_calling_spike.md` 10/10, 인자 정확도 100%.
- **대안 비교**:
  - fallback JSON 프롬프트 기반 호출: 스파이크 통과로 채택 불필요.
  - 다른 모델(Qwen2.5 등): 멀티모달(§6) + 131K 컨텍스트 조건을 동시에 만족하는 Ollama 호환 옵션이 현재 제한적이며, REQUIREMENTS.md §8이 Gemma 4 E4B를 명시.
- **위험**: RISKS.md R-01(CPU 추론 지연), R-06(E4B vision 변형 확인) 참고.

### D-03: TTS는 MeloTTS(한국어) 기본, XTTS v2를 옵션으로 둔다

- **결정**: `melotts` 파이썬 패키지를 한국어 기본 TTS로 사용한다. 음성 클로닝이 필요할 때만 XTTS v2를 수동 활성화.
- **근거**:
  - Piper 공식 저장소에 한국어 모델 없음 (`docs/research/piper_korean_tts.md`).
  - 대체 한국어 Piper 모델은 CC-BY-NC-SA-4.0으로 상업 사용 금지.
  - MeloTTS(MyShell, MIT 라이선스)는 한국어 포함 다국어 지원, ONNX 런타임 CPU에서 실행 가능.
  - XTTS v2(Coqui)는 화자 참조 음성 3~6초로 zero-shot 클로닝 가능. 라이선스(CPML)는 비상업 기본이나 사내 내부 사용은 협의 대상.
- **대안 비교**:
  - Piper 한국어 자체 학습: 공수 과다, V1 범위 밖.
  - CosyVoice 2: 별도 서버 + Gradio 의존. 사내 설치 난이도 ↑.
- **정책**: XTTS v2는 기본 번들에서 다운로드하지 않고, 사용자가 화자 참조 오디오를 등록한 시점에만 모델 로드를 트리거(`assets/models/xtts_v2/`에 미리 배치 필요).

### D-04: HWPX 파싱은 zipfile + lxml 직접 구현 (LibreOffice 의존 제거)

- **결정**: `zipfile` + `lxml.etree`로 `Contents/section*.xml`을 직접 파싱한다.
- **근거**: `docs/research/hwpx_spike.md` 3/3 PASS. 회사 환경은 HWPX 전용(구 HWP 미사용).
- **대안 비교**:
  - LibreOffice headless → PDF → PyMuPDF: 설치 바이너리 ~400MB, 변환 지연 수 초. 의존성 최소화 원칙 위배.
  - pyhwp(AGPL-3.0): 라이선스 전염성 문제 + 유지보수 불확실.
  - hwplib(Java): JVM 번들 필요, 타이트한 메모리 상한과 충돌.
- **한계**: 바운딩 박스는 HWPX 스펙상 렌더러 계산으로, 파서 단계 추출 불가 → 단락 인덱스를 인용 단위로 사용.

### D-05: 리랭커(Qwen3-Reranker-8B)는 V1에서 제외한다

- **결정**: BGE-M3 단일 검색으로 V1 출시. 리랭커는 optional, default off.
- **근거**:
  - 8B 파라미터 리랭커는 RAM 약 8GB 추가 소모 → 12GB 상한 위배(R-02).
  - 한국어 리랭킹 성능 수치 미확인(`docs/research/bge_m3_korean.md`).
  - 단일 검색 정밀도는 운용 후 실측으로 평가 → V2 이후 검토.
- **대안**: top_k를 넉넉히(10~15) 뽑고 LLM이 citation 판정하는 구조로 커버.

### D-06: 아바타 렌더러는 스프라이트 스왑(PNG 7종)으로 결정, Live2D는 DROP

- **결정**: `AvatarRenderer`(M_08) 인터페이스 아래 `SpriteSwapRenderer`만 구현. Live2D 관련 upstream 코드는 감정 태그 파싱(`extract_emotion`) 함수만 복사.
- **근거**:
  - REQUIREMENTS.md §3.2가 "V1: 스프라이트 스왑 방식"을 명시.
  - Live2D Cubism Core는 Live2D Inc. 라이선스(비상업 제한 있음)로 사내 상업 사용 검토 부담.
  - 스프라이트는 crossfade + CSS 애니메이션으로 단순 구현, 렌더 비용 낮음(CPU GPU 무관).
- **확장 지점**: `AvatarRenderer` 인터페이스만 유지하면 V2에서 다른 렌더러(레이어 합성, Live2D 등) 추가 가능. 다만 본 문서 범위는 V1 스프라이트뿐.

### D-07: 개발/배포 Ollama 엔드포인트는 환경변수로 분기한다

- **결정**: `OLLAMA_BASE_URL` 환경변수를 최우선으로 읽는다. 미설정 시 설정 파일(`conf.yaml`)의 값, 그래도 없으면 `http://127.0.0.1:11434`.
- **근거**:
  - 개발 환경은 현재 `http://192.168.219.109:11434` 사용(WSL2 또는 원격 GPU 머신).
  - 배포 환경은 사내 인트라넷 PC 로컬 Ollama로 고정.
  - 외부 네트워크 호출 금지 규칙(CLAUDE.md)과 충돌하지 않도록, 배포 빌드 시 검증 스크립트가 `OLLAMA_BASE_URL`이 사설 IP/loopback임을 강제 확인.

---

## 6. 비기능 요구사항 대응

### 6.0 하드웨어 프로파일

REQUIREMENTS.md §9 기준으로 두 가지 배포 프로파일을 지원한다. `conf.yaml`의 `profile` 키(`min` / `recommended`)로 분기.

| 항목 | MIN 프로파일 | RECOMMENDED 프로파일 |
|---|---|---|
| **대상 PC** | 개발 노트북 (RAM 16GB) | 사내 PC 발주 스펙 (RAM 32GB) |
| **STT 모델** | Whisper medium (int8, ~600 MB) | Whisper large-v3 (int8, ~1.6 GB) |
| **리랭커** | 비적재 (완전 비활성) | optional, default off |
| **Electron UI** | 최소 UI (스프라이트 + 채팅) | 전체 UI (PDF viewer 포함) |
| **예상 상주 RSS** | ~12.6 GB | ~13.6 GB (리랭커 OFF 기준) |
| **RAM 여유** | ~3.4 GB (배경 앱 공유) | ~18.4 GB |
| **SLA 목표 (GPU)** | 2초 이내 | 2초 이내 |
| **SLA 목표 (CPU-only)** | 6초 이내 | 6초 이내 |

### 6.1 메모리 예산표 (RSS, 가정치)

| 컴포넌트 | 상주 방식 | MIN | RECOMMENDED | 근거 |
|---|---|---|---|---|
| Python 프로세스 + FastAPI + asyncio | 상주 | 250 MB | 250 MB | 일반 FastAPI + uvicorn 관측치 |
| faster-whisper ASR | 지연 로드 (첫 STT 시) | **600 MB** (medium) | **1.6 GB** (large-v3) | int8 양자화 가중치 + 오디오 버퍼 |
| BGE-M3 (float16 on CPU) | 상주 | 2.2 GB | 2.2 GB | `docs/research/bge_m3_korean.md` |
| Silero VAD (ONNX) | 상주 | 40 MB | 40 MB | 1.8MB 가중치 + onnxruntime 오버헤드 |
| MeloTTS (ONNX, 한국어 1 화자) | 상주 | 450 MB | 450 MB | ONNX Runtime CPU + 음향 모델 + 보코더 |
| Ollama `gemma4:e4b` 프로세스 | 별도 프로세스, 상주 | 8.5 GB | 8.5 GB | REQUIREMENTS `gemma4:e4b` 크기 |
| LanceDB(임베드) | 메모리 매핑 | 300 MB | 300 MB | 1만 청크 × 1024dim × 4B ≈ 40MB + 인덱스 |
| SQLite (calendar.db) | 파일 + 캐시 | 20 MB | 20 MB | 이벤트 수천건 수준 |
| Electron 프론트엔드 | 별도 프로세스 | 250 MB | 250 MB | 단일 윈도우 + 렌더러 1개 |
| **합계 (리랭커 OFF)** | | **~12.6 GB** | **~13.6 GB** | |
| Qwen3-Reranker-8B (optional) | 요청 시 로드 | — | +8 GB | RECOMMENDED에서 opt-in 시만 |

> **XTTS v2**는 두 프로파일 모두 기본 OFF. 화자 참조 WAV 등록 시에만 추가 로드(+1.8 GB).
> **Ollama `keep_alive=300`** (5분) 설정 권장 — 무대화 시 모델 언로드 → 상시 상주 ~4 GB 수준으로 감소.

### 6.2 응답 지연 예산 (GPU 유/무)

| 단계 | GPU 포함(RTX 4070 급) | CPU only (i7-12700) |
|---|---|---|
| VAD → ASR 시작 지연 | 50 ms | 50 ms |
| ASR (MIN: medium / REC: large-v3, 4초 발화) | 0.6 s | 1.0 s / 2.5 s |
| RAG 검색(선택 경로) | 0.15 s | 0.3 s |
| Gemma 4 E4B TTFT(첫 토큰, 스트리밍) | 0.5 s | **10~30 s** (스파이크 관측치) |
| TTS 첫 청크(MeloTTS, 스트리밍) | 0.4 s | 0.8 s |
| **합계(첫 음성까지)** | **≈ 1.7 s** | **≈ 12~34 s** |

- **REQUIREMENTS.md §9 SLA 목표**: GPU 2초 / CPU-only 6초 (스트리밍 TTS 첫 청크 기준).
- GPU 환경에서만 2초 목표 달성 가능. CPU-only 6초는 LLM TTFT 10s+ 로 인해 현재 미달 — R-01(HIGH) 참고.
- CPU 완화책: "생각 중…" 자막 즉시 표출 + `faster_first_response=True`(upstream 옵션)로 첫 문장 TTS 조기화.

### 6.3 기동 시간 (15초 이내, REQUIREMENTS.md §9)

- 지연 로드 정책: ASR/TTS/LLM은 첫 사용 시점까지 모델 로드 연기.
- 상주만 하는 것: BGE-M3(RAG 즉시 사용), Silero VAD(마이크 즉시 시작), FastAPI 서버.
- 초기 화면 = Electron 창 + "연결됨" 상태. 2~4초 예상.

### 6.4 프라이버시

- 네트워크 허용 타겟: `127.0.0.1`, `localhost`, 그리고 환경변수 `OLLAMA_BASE_URL`가 가리키는 사설 IP뿐.
- CI 단계에서 `grep -rE "https?://"` 패턴 검사로 외부 호스트 차단(CLAUDE.md 금지 규칙 준수).
- 로그는 `data/logs/app-YYYY-MM-DD.log`. loguru 커스텀 필터로 전화번호·이메일·주민번호 정규식 마스킹.
- 보관 7일: loguru rotation + retention 설정.

---

## 7. 오프라인 배포 아키텍처

```
ai-assistant-installer/
├── python/                       # Embedded CPython 3.12 (winpython slim)
├── venv/                         # 사전 빌드된 venv, wheels-only install
│   └── site-packages/ ...
├── ollama/                       # Ollama Windows installer + 사전 풀링된 manifest
│   └── models/gemma4-e4b/
├── assets/
│   ├── models/
│   │   ├── whisper-large-v3-int8/     # faster-whisper 양자화 가중치
│   │   ├── silero-vad/*.onnx
│   │   ├── bge-m3/                    # sentence-transformers 포맷
│   │   ├── melotts-ko/                # ONNX + 보코더
│   │   └── xtts_v2/ (선택)             # 사용자 옵트인 시 유효
│   └── character/saessagi/            # PNG 7종 + 메타데이터 JSON
├── frontend/                     # Electron 빌드 산출물
├── scripts/
│   ├── install.ps1               # 서비스 등록, 단축아이콘 생성
│   ├── verify_offline.ps1        # 외부 네트워크 차단 후 기동 검증
│   └── bundle_deps.sh            # 빌드 머신에서 휠/모델 수집
└── src/                          # 본 프로젝트 소스
```

- Python 휠: `pip download --platform win_amd64 --python-version 3.12 --only-binary=:all:` 사전 수집.
- Ollama 모델: 빌드 머신에서 `ollama pull gemma4:e4b` 후 모델 manifest·블롭을 통째로 번들에 복사. 런타임 네트워크 풀링 금지.
- npm 패키지: Electron 빌드는 빌드 머신에서 수행, 산출물만 탑재.
- 설치 후 첫 실행 시 `verify_offline.ps1`이 방화벽 규칙과 Ollama 바인드 주소가 사설 대역임을 확인.

---

## 8. 디렉토리 구조 (구현 후 예상)

```
ai-assistant/
├── REQUIREMENTS.md
├── PROJECT_PLAN.md
├── CLAUDE.md
├── pyproject.toml
├── docs/
│   ├── ARCHITECTURE.md         (본 문서)
│   ├── MODULES.md
│   ├── MILESTONES.md
│   ├── RISKS.md
│   ├── GAP_ANALYSIS.md
│   ├── CHARACTER_SAESSAGI.md
│   ├── CHANGE_REQUESTS.md (필요 시)
│   └── research/ ...
├── specs/
│   └── M_NN_<module>_SPEC.md    (각 모듈 착수 전 생성)
├── reviews/
│   └── M_NN_<module>_REVIEW.md  (Critic 산출)
├── src/
│   ├── app/                    # FastAPI 엔트리, ServiceContext 확장
│   ├── asr/                    # M_02
│   ├── vad/                    # M_03 (얇은 래퍼)
│   ├── tts/                    # M_04
│   ├── agent/                  # M_05 (+ tools/)
│   ├── rag/                    # M_06, M_07
│   ├── calendar/               # M_09
│   ├── proactive/              # M_10, M_11
│   ├── avatar/                 # M_08 (백엔드 상태 관리만; 렌더 자체는 frontend)
│   ├── screenshot/             # M_05b 세부, Windows API 호출
│   └── common/                 # config, logging, utils
├── tests/
│   └── <module>/
├── assets/                     # (배포 시만 채워짐)
├── data/                       # (런타임 생성)
├── scripts/
└── upstream/Open-LLM-VTuber/   # 서브트리/서브모듈(참조 전용, 직접 수정 금지)
```

- `src/<module>` 규약은 CLAUDE.md "파일 규칙"과 일치.
- upstream 수정은 **하지 않는다**. 필요한 경우 `src/` 안에서 상속·래핑해 EXTEND한다.

---

## 9. 미해결·후속 검토 항목

본 아키텍처 문서 범위에서 결정한다고 한 것 외에는 모두 RISKS.md에 기재한다. 구현 중 추가 결정이 필요할 경우
`docs/CHANGE_REQUESTS.md`를 통해 사용자 승인 경로로 처리한다. 임의 결정 금지.

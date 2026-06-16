# 새싹이 AI 비서 — 기술 개발 보고서

> 사내 오프라인 환경을 위한 데스크톱 AI 비서. 음성·텍스트·이미지로 대화하고, 사내 문서를 근거로 답하며(RAG), 업무 노트·일정·회의 결과보고서(HWPX)를 자동 작성하는 멀티모달 에이전트.
>
> 최종 갱신: 2026-06 · GitHub: https://github.com/soonkun/ai-assistant

---

## 목차

1. 프로젝트 개요
2. 시스템 아키텍처
3. 모델 구성
4. 에이전트 체계 (요청 처리 흐름)
5. 핵심 기능 서브시스템
6. 프론트엔드 (Electron 펫/창 모드)
7. 주요 기술 문제와 해결
8. 배포·운영
9. 개발 방식
10. 현재 상태 및 잔여 과제

---

## 1. 프로젝트 개요

### 배경

기관 인트라넷은 외부 인터넷이 차단된 폐쇄망이다. 상용 클라우드 AI(ChatGPT 등)를 그대로 쓸 수 없으므로, **로컬에서 동작하는 오프라인 AI 비서**가 필요하다. 새싹이는 직원의 데스크톱에 설치되어 인터넷 없이 동작하는 것을 목표로 한다.

### 핵심 목표

- **완전 오프라인 동작** — LLM·임베딩·ASR·TTS 등 모든 추론을 로컬(Ollama·로컬 모델)에서 수행
- **사내 문서 기반 응답(RAG)** — 업로드한 규정·보고서·업무편람을 근거로 출처와 함께 답변
- **업무 자동화** — 업무 노트 정리, 일정 등록, 회의 녹취 → 한글(HWPX) 결과보고서 생성
- **멀티모달** — 텍스트·음성·이미지(스크린샷) 입력 처리
- **친근한 UX** — 바탕화면에 떠 있는 펫 캐릭터(새싹이)로 상태·감정을 시각 표현

> **상용 타깃 인프라**: 기관 인트라넷 중앙 GPU(대구 PPP존). 그 인프라 구축 전까지는 OpenAI(GPT-5/4o)를 "인트라넷에 올릴 OSS 모델"의 임시 대역으로 사용한다. `conf.yaml`의 LLM provider를 `openai`↔`ollama`로 전환할 수 있으며, 인트라넷 GPU 구축 시 base_url만 사내로 돌리면 된다.

### 핵심 특징 요약

| 영역 | 내용 |
|---|---|
| 형태 | Electron 데스크톱 앱(창 모드 + 바탕화면 펫 모드) + Python 백엔드 |
| 대화 | 의도 자동 분류 → RAG/도구/노트/일정으로 라우팅하는 에이전트 |
| 입력 | 음성(STT) · 텍스트 · 이미지/스크린샷(비전 OCR) |
| 산출물 | 답변(출처 인용) · 업무 노트(마크다운) · 일정 · 회의 결과보고서(.hwpx) |
| 기반 | Open-LLM-VTuber 포크 + 사내 요구사항 모듈(M_05b ~ M_18) 신규 개발 |

---

## 2. 시스템 아키텍처

### 전체 구조

```
┌──────────────────────────── Electron 데스크톱 앱 ────────────────────────────┐
│  frontend/ (main process)              web/ (React UI)                        │
│  · 창 모드 / 펫 모드(투명창·click-through·드래그)                              │
│  · IPC: shell:openDocument, pet:*, get-display …                              │
│  · 탭: 새싹이(대화)·일정표·노트·문서·회의록·설정                              │
└───────────────┬───────────────────────────────────┬──────────────────────────┘
                │ WebSocket (ws://127.0.0.1:12393/client-ws)   │ HTTP (/api/*)
                ▼                                               ▼
┌──────────────────────────── Python 백엔드 (FastAPI / uvicorn, :12393) ───────┐
│  대화 파이프라인:  IntentGate → RAG 증강 → (비전 전사) → ToolLoop → 폴백      │
│  ─ GemmaChatAgentAdapter (의도분류·RAG·도구상태·노트 폴백)                    │
│  ─ GemmaChatAgent (upstream BasicMemoryAgent 컴포지션 + 도구 루프)            │
│  서비스: RAG · Knowledge(노트) · Calendar · MeetingMinutes · Proactive ·      │
│          IdleMonitor · AvatarState · ASR · TTS · VAD                          │
└──────┬───────────────┬──────────────────┬───────────────────┬────────────────┘
       │               │                  │                   │
       ▼               ▼                  ▼                   ▼
   Ollama(:11434)   LanceDB           SQLite              파일시스템
   gemma4/qwen2.5vl  (벡터 스토어)    (calendar.db)       data/knowledge/*.md
   임베딩·리랭커는                                         data/rag_originals/
   로컬 sentence-transformers
```

- **프로세스 분리**: Electron(프론트) ↔ FastAPI 백엔드(별도 프로세스) ↔ Ollama(별도 서버). 런처(`새싹이.cmd`)가 Ollama 헬스체크 → 자동 기동 → 백엔드 → Electron 순으로 띄운다.
- **upstream**: `Open-LLM-VTuber`를 포크(`upstream/`, 직접 수정 금지·패치 관리). 대화 에이전트는 upstream `BasicMemoryAgent`를 **컴포지션**으로 감싸고 `chat()`·도구 루프만 본 프로젝트에서 재구현한다.
- **오프라인 원칙**: 외부 네트워크 호출 금지(127.0.0.1·사내 IP만). 단, LLM provider가 `openai`일 때 `api.openai.com` 호출은 의도된 임시 대역.

### 디렉토리 구조

```
ai-assistant/
├── src/
│   ├── app/                  # FastAPI 앱·라우트·service_context(DI)·config
│   ├── agent/                # GemmaChatAgent, upstream_adapter, builder, no_think_llm
│   ├── agent_prompts/        # 런타임 편집 가능한 지침(페르소나·답변·노트 가이드)
│   ├── intent_gate/          # 의도 분류기(IntentClassifier) + 라우팅 결정
│   ├── tool_router/          # 도구 스키마·디스패치(add_event, save_knowledge_note 등)
│   ├── vector_search/        # RAG: BGE-M3 임베더, LanceDB, BM25, RRF, bge-reranker
│   ├── document_ingest/      # 문서 파싱(pdf/docx/pptx/hwpx/txt) + 청크 분할
│   ├── knowledge/            # 업무 노트(마크다운 + 임베딩 + 그래프)
│   ├── calendar_service/     # 일정(SQLite)
│   ├── meeting_minutes/      # 회의록: generator, prompts, schemas, hwpx_writer
│   ├── asr/                  # faster-whisper 한국어 STT
│   ├── tts/                  # MeloTTS / 시스템 TTS
│   ├── vad/                  # silero VAD
│   ├── avatar_state/         # 캐릭터 감정 상태
│   ├── idle_monitor/         # 입력 유휴 감지(pynput)
│   └── proactive/            # 능동 알림(APScheduler)
├── frontend/                 # Electron 메인 프로세스(IPC, 윈도우/펫 관리)
├── web/                      # React 웹앱(UI 컴포넌트, dist는 빌드 산출물)
├── upstream/Open-LLM-VTuber/ # 포크(수정 금지)
├── assets/models/            # 로컬 AI 모델(git 제외): bge-m3, faster-whisper 등
├── data/                     # 런타임 데이터(git 제외): vector_store, calendar.db, knowledge, rag_originals
├── docs/                     # ARCHITECTURE, ERROR_HISTORY, USER_GUIDE, FRONTEND_CONSTRAINTS …
├── conf.yaml                 # 런타임 설정(API 키 포함 → git 제외)
└── conf.example.yaml         # 설정 템플릿
```

---

## 3. 모델 구성

새싹이는 단일 모델이 아니라 **역할별로 분리된 모델 집합**을 조합한다. 모든 모델은 기본적으로 로컬에서 동작하며, 채팅 모델만 OpenAI 임시 대역으로 전환 가능하다.

| 역할 | 기본 모델 | 엔진 / 위치 | 비고 |
|---|---|---|---|
| **대화·도구 호출** | `gemma4:latest` | Ollama `/v1` | 에이전트 본체. thinking은 `reasoning_effort:none`으로 비활성(E-53). OpenAI 전환 시 `gpt-5` |
| **비전 / 이미지 OCR** | `qwen2.5vl:7b` | Ollama | 이미지 첨부 턴 전용. 한글 스크린샷 판독. `app.ollama.vision_model`로 지정·교체 |
| **의도 분류** | `gemma4:latest` | Ollama (또는 OpenAI `gpt-4o-mini`) | `intent_gate.provider`로 선택. 타임아웃 30s |
| **임베딩** | `BGE-M3` | sentence-transformers (로컬) | 문서·노트 청크 벡터화 |
| **리랭커** | `bge-reranker-v2-m3` | cross-encoder (로컬) | 검색 결과 재정렬 (GPU 시 FP16) |
| **음성 인식(STT)** | `faster-whisper large-v3-turbo` | 로컬 (ko) | 회의 전사·음성 입력 |
| **음성 합성(TTS)** | `MeloTTS (KR)` / 시스템 TTS | 로컬 | 사용자 선택 |
| **음성 활동 감지(VAD)** | `silero_vad` | 로컬 | 발화 끊김 감지 |

> **모달리티별 라우팅**: gemma4는 도구 호출·한국어 대화에 강하지만 이미지 OCR 능력이 부족하다(capabilities에 vision이 있어도 실제 판독 실패). 따라서 이미지가 들어오면 OCR은 qwen2.5vl이 전담하고, 그 결과 텍스트를 gemma4가 받아 답변·도구를 수행한다(§4 멀티모달). "한 모델이 모든 모달리티를 잘하지 못하면 모달리티별로 모델을 분리한다"는 설계 원칙.

---

## 4. 에이전트 체계 (요청 처리 흐름)

새싹이 대화는 단순 LLM 호출이 아니라, **의도 분류 → 라우팅 → 컨텍스트 증강 → (필요 시) 모달리티 전환 → 도구 실행 → 후처리**로 이어지는 에이전트 파이프라인이다. 핵심은 `GemmaChatAgentAdapter`(외곽 오케스트레이션)와 `GemmaChatAgent`(LLM·도구 루프)의 2계층 구성이다.

### 한 턴의 처리 단계

```
[프론트] WS text-input(+images)
   │
   ▼
① 의도 분류 (IntentGate)
   IntentClassifier(LLM 1회) → 6개 라벨 중 하나 + confidence
   라벨: calendar_add · calendar_query · doc_query · work_query · note_save · chat
   decide_with_confidence() → RoutingDecision{ inject_rag, rag_source, tool_hint,
                                                answer_guide, autonomous }
   · 저신뢰(임계 0.55 미만) → 자율 폴백
   · (안전망 E-57) 분류 실패 + 첨부 존재 → note_save로 강제
   │
   ▼
② 의도 → 캐릭터 연출
   note_save→[note_writing]"업무 노트를 작성할게요!", doc_query/work_query→[study],
   calendar_add→[writing], calendar_query→[thinking]  + 진행상태 말풍선
   │
   ▼
③ 컨텍스트 증강 (_augment_with_rag)
   · 이미지 첨부 시: "보이는 텍스트·표·수치를 그대로 전사" 판독 지침 주입
   · 첨부 문서(doc_id) 있으면: 원문 청크 직접 주입(per_doc_limit=30)
   · inject_rag면: 하이브리드 RAG 검색(§5.1) → 상위 청크 + [[doc:id]] 인용 마커
   · tool_hint / answer_guide(사용자 편집 지침)를 사용자 메시지 앞에 prepend
   │
   ▼
④ 모달리티 전환 (이미지 턴, A안)
   batch.images 있고 vision_model 설정됨 →
     qwen2.5vl이 이미지를 "전사 전용 프롬프트"로 OCR → 추출 텍스트를
     "[첨부 이미지에서 추출한 내용]" 블록으로 batch에 주입, images 제거
   → 이후는 일반 텍스트 턴과 동일(gemma4가 페르소나·도구로 처리)
   │
   ▼
⑤ LLM + 도구 루프 (GemmaChatAgent)
   upstream _to_messages → _openai_tool_interaction_loop(gemma4, 도구 6종)
   도구: add_event · get_events · search_docs · take_screenshot ·
         save_knowledge_note · create_meeting_minutes
   (도구 없는 단답은 _simple_stream)
   │
   ▼
⑥ 후처리 / 폴백
   · 도구 호출/결과 이벤트 → 프론트로 중계(작성 중 표시 등)
   · (E-45/E-55) note_save 턴인데 도구 미호출/실패 → 강제 저장 폴백:
       첨부 문서 원문 청크 + 답변을 근거로 complete_json → save_knowledge_note
   · 답변 텍스트에서 LLM이 끼운 마커 정리, 권위 인용 마커 부착
   │
   ▼
⑦ 출력
   SentenceOutput(display_text + tts_text) → TTS(melo/시스템) →
   프론트: 말풍선 + [emotion] 연출 + [[doc:]]/[[note:]] 인용 칩 + 오디오 재생
```

### 도구 (ToolRouter)

| 도구 | 역할 |
|---|---|
| `add_event` / `get_events` | 일정 등록 / 조회 (SQLite) |
| `search_docs` | 사내 문서 RAG 검색(출처 반환) |
| `save_knowledge_note` | 업무 노트 생성·저장(마크다운 + 임베딩) |
| `create_meeting_minutes` | 회의 녹취 → 한글 결과보고서(.hwpx) 생성 |
| `take_screenshot` | 화면 캡처(비전 분석용) |

도구는 OpenAI function-calling 스펙으로 정의되며, `use_mcpp=False`여도 `extra_tool_specs`(ToolRouter)로 도구 루프를 사용한다.

### 의도 게이트가 핵심인 이유

새싹이의 "에이전틱"함은 **모든 입력을 LLM에 자유 위임하지 않고, 먼저 의도를 분류해 결정론적으로 라우팅**하는 데 있다. 이 덕분에:
- "오늘 ~~ 처리했어"(과거 보고) → 노트 저장, "내일 2시 회의"(미래) → 일정 등록을 안정적으로 구분
- 문서 질문이면 RAG를 강제 주입, 단순 인사면 도구 없이 빠르게 응답
- 분류기가 실패해도(타임아웃 등) 첨부 신호로 note_save를 보장(안전망)

### 멀티모달 처리(이미지)의 설계 결정 — "A안"

이미지가 들어오면 두 가지 선택지가 있었다.
- **B안(기각)**: 비전 모델이 이미지 턴 전체(답변까지)를 처리 — 답변 톤이 페르소나와 달라지고, 비전 모델이 도구를 못 부른다.
- **A안(채택)**: 비전 모델은 **OCR 전사만** 담당 → 추출 텍스트를 메인 모델(gemma4)에 주입 → gemma4가 평소 페르소나·도구로 답변·노트 작성.

A안은 "OCR 엔진(qwen) ↔ 비서 두뇌(gemma4)"를 분리해 일관된 UX와 도구 사용을 보장한다.

---

## 5. 핵심 기능 서브시스템

각 기능은 독립 모듈로 분리되어 있고, 회의록·노트 등은 대화 파이프라인과도 분리되어 단독 동작한다.

### 5.1 RAG (사내 문서 검색)

```
등록:  파일(pdf/docx/pptx/hwpx/txt) → 파싱 → 청크 분할 → BGE-M3 임베딩 → LanceDB 저장
검색:  질의 → ┌ 벡터 검색(의미) ┐
              └ BM25(키워드) ──┘ → RRF 융합(k=60) → bge-reranker 재정렬 → 상위 N
```

- **하이브리드 + 리랭킹(M_18)**: 의미 검색과 키워드 검색을 RRF로 융합해 과제번호·고유명사도 잡고, cross-encoder 리랭커가 질의 관련도로 재정렬.
- 폴더별 버킷(`rag_originals/<folder_id>/`)에 원본 보관, 폴더 정의는 `rag_folders.json`(원자적 쓰기, E-56).
- 답변에는 `[[doc:doc_id]]` 인용 마커가 붙고, 프론트에서 출처 칩으로 표시되며 클릭 시 원본이 기본 앱으로 열린다.

### 5.2 업무 노트 (Knowledge)

- 마크다운 파일(`data/knowledge/<slug>.md`)로 로컬 저장 + 벡터 임베딩(`__knowledge__:slug`)으로 검색 가능.
- 생성 경로: 채팅에서 자료/이미지와 함께 요청 → 의도 note_save → 도구 호출 또는 강제 폴백으로 저장.
- **내용 기반 작성(E-54/E-55)**: 첨부 문서는 원문 청크를, 이미지는 비전 전사를 근거로 작성. 제목도 내용 대표형으로 생성.
- 노트 간 연결 그래프, 블록 에디터(창 모드, Notion 스타일).

### 5.3 일정 (Calendar)

SQLite 기반. add/get/update/delete 이벤트. 자연어 일정("내일 오후 2시")을 ISO 8601(+09:00)로 변환해 등록.

### 5.4 회의록 자동 작성 (대화와 분리된 3단계 파이프라인)

```
Step 1 전사:    오디오 → faster-whisper(ko) → 녹취 텍스트
Step 2 회의록:  녹취 → [개조식 회의록 TEXT]  (complete_text, MEETING_STYLE_RULES + TEXT_OUTPUT_FORMAT)
Step 3 보고서:  텍스트 → [MeetingDraft JSON]  (complete_json, JSON 강제) → hwpx_writer → .hwpx
```

- **출력 형식과 작성 스타일의 분리(E-58)**: 작성 스타일 규칙(`MEETING_STYLE_RULES` — 위계 ○/-/*, 글자수, 명사형 종결, 수치 보존)은 형식과 무관하게 공유하고, Step 2는 텍스트 레이아웃을, Step 3은 JSON 스펙을 각각 덧붙인다. 한 덩어리로 섞으면 약한 로컬 모델이 텍스트 자리에 JSON을 토출하는 커플링이 생긴다.
- Step 3는 JSON Schema(Draft 2020-12)로 검증하되, 형식 위반은 하드 실패가 아니라 정규화로 흡수(예: 향후계획 날짜 placeholder → 빈 값, E-52).

### 5.5 능동 기능 (Proactive)

- **IdleMonitor**: pynput으로 키/마우스 유휴 감지.
- **ProactiveDispatcher**(APScheduler): 아침 브리핑(cron 09:00), 일정 임박 알림(interval, 토픽별 쿨다운 30분), 장시간 작업 휴식 권고.

### 5.6 아바타 상태 (AvatarState)

대화 의도·도구 단계에 따라 캐릭터 표정/영상을 전환: `neutral`·`thinking`·`study`(자료 찾기)·`note_writing`·`writing`·`uploading`(RAG 등록 영상) 등. 백엔드가 대화 채널로 `[emotion]` 태그를 보내면 프론트가 스프라이트를 바꾼다.

---

## 6. 프론트엔드 (Electron 펫/창 모드)

- **창 모드**: 일반 데스크톱 창. 탭 — 새싹이(대화)·일정표·노트·문서·회의록·설정.
- **펫 모드**: 투명 배경의 바탕화면 캐릭터. click-through(캐릭터 외 영역은 클릭이 바탕화면으로 통과), 드래그 이동, always-on-top.
- **입력**: 음성(push-to-talk STT)·텍스트·이미지(클립보드 붙여넣기/파일 첨부).
- **IPC**: `shell:openDocument`(원본 바로 열기), `pet:*`(펫 모드 제어), `get-display` 등.
- **빌드 주의**: Electron은 `file://`로 로드하므로 `web/dist`는 반드시 `ELECTRON_BUILD=1`로 빌드해 상대경로(`./assets/...`)를 생성해야 한다(절대경로면 흰 화면, E-22).
- **설정**: LLM 공급자(ollama/openai)·모델, 비전 모델, 의도 분류기, 지침(프롬프트) 편집, TTS 엔진/속도, 펫 모드, 글씨 크기.

---

## 7. 주요 기술 문제와 해결 (발췌)

상세 이력은 `docs/ERROR_HISTORY.md`(E-01 ~ E-58)에 증상·원인·해결·교훈으로 기록된다. 최근 핵심:

| # | 문제 | 해결 |
|---|---|---|
| E-53 | Ollama `/v1`가 `think:false`를 무시 → thinking 모델이 빈 응답 | `/v1`이 존중하는 `reasoning_effort:none` 주입 |
| E-54 | gemma4가 한글 이미지 OCR 불가 → 추측성 노트 | 비전 모델(qwen2.5vl) 라우팅 도입(A안) |
| E-55 | 노트 강제 폴백이 1차 소스(문서) 미반영 | 폴백이 첨부 원문 청크를 직접 근거로 작성 |
| E-56 | `rag_folders.json` 비원자적 쓰기로 폴더 정의 유실 | temp+fsync+`os.replace` 원자적 쓰기 |
| E-57 | 로컬 의도 분류기 콜드스타트 8s 타임아웃 → 첨부 노트 유실 | 타임아웃 30s + 분류 실패 시 첨부면 note_save 안전망 |
| E-58 | 회의록 Step 2가 텍스트 대신 JSON 출력 | 작성 스타일 ↔ 출력 형식 분리(관심사 분리) |
| E-22 | `web/dist` 절대경로 빌드 → Electron 흰 화면 | `ELECTRON_BUILD=1` 강제(런처 자동) |

**관통하는 교훈**: ① 로컬 LLM은 외부 API와 특성이 다르다(콜드스타트·thinking·OCR 한계) — 실제 데이터로 검증할 것. ② 프롬프트에서 "스타일"과 "출력 형식"을 섞지 말 것. ③ 형식 위반은 하드 실패 대신 정규화로 흡수. ④ 모델 능력 플래그 ≠ 실제 품질.

---

## 8. 배포·운영

### 런처(`새싹이.cmd`)

1. `conf.yaml`/프론트 빌드 점검(`check-rebuild.mjs`, 필요 시 `ELECTRON_BUILD=1` 재빌드)
2. **Ollama 자동 기동** — 미기동 시 `ollama serve` 시작 후 준비될 때까지 대기(최대 30s)
3. **좀비 백엔드 정리** — 12393 포트를 쥔 이전 백엔드 종료(중복 바인딩 WinError 10048 방지)
4. 백엔드(uvicorn) 기동 → 준비 폴링(상한 180s) → Electron 실행

### 오프라인 USB 번들

인터넷 차단 환경을 위해 소스·로컬 모델(Whisper·BGE-M3 등)·OS별 wheel을 USB에 사전 패키징하고 원클릭 설치 스크립트를 제공한다.

---

## 9. 개발 방식

- **멀티에이전트 개발 프로세스**(런타임 아님): planner(설계)·builder(구현)·validator(테스트/린트)·critic(적대적 리뷰)로 역할 분리.
- **스펙 우선**: `specs/M_NN_SPEC.md` 없이 `src/` 생성 금지. 요구사항은 `REQUIREMENTS.md`가 단일 진실 공급원.
- **회귀 방지**: 버그는 반드시 `docs/ERROR_HISTORY.md`에 원인·해결·교훈 기록.
- **품질 게이트**: `ruff format · ruff check · mypy src/ · pytest tests/`.

---

## 10. 현재 상태 및 잔여 과제

### 완료 (모듈)

| 모듈 | 내용 |
|---|---|
| M_01~M_05 | AppCore · ASR · VAD · TTS · LLMAgent |
| M_05b | ToolRouter(도구 6종) |
| M_06 / M_07 / M_18 | DocumentIngest · VectorSearch · 하이브리드+리랭커 |
| M_08~M_11 | AvatarState · Calendar · IdleMonitor · Proactive |
| M_12 | Electron 프론트(창/펫 모드) |
| M_13 | MeetingMinutes(전사·회의록·HWPX) |
| M_15 | Knowledge(업무 노트 + 그래프) |
| M_16 / M_17 | IntentGate(의도 분류·라우팅) · 런타임 편집 지침 |

### 현재 버전 추가 (멀티모달·안정화, 2026-06)

- 이미지/스크린샷 OCR 노트(비전 모델 라우팅), 첨부 문서 내용 기반 노트, 모달리티별 모델 분리
- 의도 분류 안전망, 로컬 thinking 비활성화, 파일 링크 바로 열기, 런처 Ollama 자동 기동
- 회의록 출력 형식/스타일 분리

### 잔여 과제

- 인트라넷 중앙 GPU(대구 PPP존) 인프라 구축 시 LLM base_url 사내 전환 및 OSS 모델 검증
- 비전 모델 한글 OCR 품질의 모델별 벤치마크
- 회의록 Step 2 사용자 커스텀 스타일 지침 반영(현재 Step 3 JSON 지침만 conf 연동)

---

*GitHub: https://github.com/soonkun/ai-assistant · 상세 모듈/아키텍처: `docs/ARCHITECTURE.md`, `docs/MODULES.md` · 버그 이력: `docs/ERROR_HISTORY.md`*

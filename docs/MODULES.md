# MODULES — 모듈 인터페이스 계약

Phase 1 산출물. 구현은 Phase 2에서 이 계약대로만 이루어진다.
각 모듈은 `specs/M_NN_<module>_SPEC.md`가 작성·승인된 후에만 `src/<module>/`에 착수한다(CLAUDE.md 규칙).

- 분류 표기: **REUSE** = upstream 코드를 그대로 사용, **EXTEND** = upstream 코드를 상속·래핑해 확장, **NEW** = 처음부터 작성.
- 상태 표기: 🔲 TODO, 🚧 WIP, ✅ DONE.
- 모든 공개 API는 `async def`를 기본으로 한다(FastAPI/asyncio 상호운용). 동기 함수인 경우만 명시.
- 타입 힌트는 Python 3.12 표준(`list`, `dict`, `X | None`). `pydantic.BaseModel`은 `BaseModel`로 축약 표기.

---

## 의존성 그래프

```
                         ┌───────────────────────┐
                         │  M_01 AppCore         │
                         │  (ServiceContext ext, │
                         │   WebSocket routes)   │
                         └─────────┬─────────────┘
                                   │
     ┌────────────┬────────────┬───┼────────────┬──────────────┬──────────────┐
     ▼            ▼            ▼   ▼            ▼              ▼              ▼
 M_02 ASR    M_03 VAD    M_04 TTS  M_05 LLM   M_08 Avatar   M_10 Idle    M_11 Proactive
                                   Agent       State         Monitor      Dispatcher
                                   │                                        │
                              ┌────┼────────────┐                           │
                              ▼    ▼            ▼                           │
                        M_05b     M_06         M_09                         │
                        Tools     DocIngest    Calendar ──────────────┐     │
                        (FC)        │          (SQLite)               │     │
                                    ▼                                 ▼     ▼
                               M_07 VectorSearch                 M_12 Frontend
                                    (BGE-M3, LanceDB,             (Electron, sprite,
                                     citation)                     pet mode)
```

구현 우선순위(하위 의존성 먼저): M_01 → M_02 → M_03 → M_04 → M_05(M_05b 병렬) → M_06 → M_07 → M_09 → M_10 → M_11 → M_08 → M_12.

---

## 모듈 목록

### M_01 AppCore (FastAPI + ServiceContext 확장)

- **분류**: EXTEND
- **상태**: ✅ DONE
- **목적**: FastAPI 엔트리, upstream `ServiceContext` 상속 확장, WebSocket 라우팅. 새로운 메시지 타입(`screenshot-trigger`, `start-continuous-capture`, `stop-continuous-capture`) 2종 추가. CR-03(2026-04-19): `AppServiceContext.init_agent` 오버라이드로 `BasicMemoryAgentAdapter` + `CompositeToolExecutor` 배선 완료.
- **upstream 근거**: `src/open_llm_vtuber/server.py`, `routes.py`, `websocket_handler.py`, `service_context.py`.
- **공개 API (초안)**
  ```python
  class AppServiceContext(ServiceContext):  # upstream ServiceContext 상속
      rag_service: RagService | None
      calendar_service: CalendarService | None
      idle_monitor: IdleMonitor | None
      avatar_state: AvatarState | None
      proactive_dispatcher: ProactiveDispatcher | None

      async def load_from_config(self, config: Config) -> None: ...

  def create_app(config_path: str) -> FastAPI: ...
  ```
- **의존**: upstream. 하위 모듈 전체를 조립만 한다.

### M_02 ASREngine (한국어 STT)

- **분류**: EXTEND
- **상태**: ✅ DONE
- **목적**: faster-whisper large-v3 int8을 한국어/영어에 맞춰 초기화. upstream `FasterWhisperASR` 설정만 교체.
- **공개 API**
  ```python
  from upstream.asr.asr_interface import ASRInterface  # 논리적 표기

  class KoreanWhisperASR(ASRInterface):
      def __init__(self,
                   model_path: str,
                   language: str = "ko",
                   compute_type: str = "int8",
                   device: str = "auto") -> None: ...
      async def async_transcribe_np(self, audio: np.ndarray) -> str: ...
  ```
- **에러**: 모델 경로 부재 → `ASRInitError`. 오디오 길이 0 → 빈 문자열.
- **의존**: `faster-whisper`, upstream `ASRInterface`.

### M_03 VADEngine (Silero VAD 래퍼)

- **분류**: REUSE
- **상태**: ✅ DONE
- **목적**: upstream `silero.py` 그대로 사용. 본 프로젝트에서는 설정 (`threshold`, `min_silence_duration_ms`)만 노출.
- **공개 API**: upstream `VADInterface` 그대로. 신규 코드 최소화.
  ```python
  from upstream.vad.vad_interface import VADInterface
  # 팩토리 분기만: VADFactory.get_vad_engine("silero_vad", **config)
  ```
- **의존**: upstream `vad/silero.py`.

### M_04 TTSEngine (MeloTTS 한국어 + XTTS v2 옵션)

- **분류**: NEW (upstream TTSInterface 준수)
- **상태**: ✅ DONE (Critic PASS 4차, reviews/M_04_TTSEngine_REVIEW.md; 잔존 minor m21~m24는 릴리즈 차단 아님 — R-05 MeloTTS 한국어 QA·CR-01 패키지 설치·CR-02 XTTS 법무 승인은 빌드/배포 마일스톤에서 처리)
- **목적**: 한국어 TTS 엔진 구현. 기본 MeloTTS, 사용자가 옵트인 시 XTTS v2로 전환. 화자 참조(WAV 3~6초) 업로드 API 포함.
- **공개 API**
  ```python
  from upstream.tts.tts_interface import TTSInterface

  class MeloTTSEngine(TTSInterface):
      def __init__(self, model_dir: str, speaker_id: int = 0,
                   sample_rate: int = 24000, speed: float = 1.0) -> None: ...
      async def async_generate_audio(self, text: str,
                                      file_name_no_ext: str | None = None) -> str: ...
      # 반환: 생성된 WAV 파일 절대 경로

  class XttsV2Engine(TTSInterface):
      def __init__(self, model_dir: str, speaker_wav: str,
                   language: str = "ko") -> None: ...
      async def async_generate_audio(self, text: str,
                                      file_name_no_ext: str | None = None) -> str: ...
  ```
- **에러**: 모델 파일 누락 → `TTSInitError`. 화자 참조 WAV가 3초 미만이면 `ValueError`.
- **의존**: `melotts`, `TTS` (Coqui XTTS), upstream `TTSInterface`, 라이브러리 로딩은 lazy import.

### M_05 LLMAgent (Gemma 4 E4B, tool calling 포함)

- **분류**: EXTEND
- **상태**: ✅ DONE
- **목적**: upstream `BasicMemoryAgent` + `OpenAICompatibleLLM`을 Gemma 4 E4B에 맞춰 구성한다. tool calling 결과를 M_05b 툴 라우터로 전달.
- **공개 API**
  ```python
  class GemmaChatAgent:
      def __init__(self,
                   base_url: str,     # 환경변수 OLLAMA_BASE_URL override
                   model: str = "gemma4:e4b",
                   system_prompt: str = "",
                   tool_manager: ToolManager | None = None,
                   tool_executor: ToolExecutor | None = None,
                   temperature: float = 0.7,
                   max_context_tokens: int = 131_000) -> None: ...
      async def chat(self, batch: BatchInput) -> AsyncIterator[AgentEvent]: ...
      async def handle_interrupt(self, heard_text: str) -> None: ...
  ```
  - `AgentEvent` 종류: `TextChunk`, `ToolCallStart(name, args)`, `ToolCallResult(name, result)`, `EndOfTurn`.
  - `BatchInput`은 upstream `agent/input_types.py`를 REUSE(`text`, `images`, `image_sources`).
- **에러**: Ollama 미연결 → 3회 재시도 후 `AgentBackendError`. 비어있는 응답 → 사용자 친화 메시지 반환.
- **의존**: upstream `OpenAICompatibleLLM`, `BasicMemoryAgent`, `ToolManager`, `ToolExecutor`, M_05b.

### M_05b ToolRouter (function calling 핸들러)

- **분류**: NEW
- **상태**: ✅ DONE
- **목적**: Gemma의 tool call을 실제 파이썬 핸들러로 디스패치. 4종 기본 툴 등록.
- **등록 툴(초기 4종)**: `add_event`, `get_events`, `search_docs`, `take_screenshot`.
- **공개 API**
  ```python
  ToolSpec = dict[str, Any]  # OpenAI function-calling JSON schema

  class ToolRouter:
      def __init__(self,
                   calendar: CalendarService,
                   rag: RagService,
                   screenshot: ScreenshotService) -> None: ...
      def tool_specs(self) -> list[ToolSpec]: ...
      async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...

  @dataclass
  class ToolResult:
      ok: bool
      payload: dict[str, Any]
      error: str | None = None
  ```
- **에러**: 알 수 없는 `name` → `ToolResult(ok=False, error="unknown_tool")`. 인자 스키마 위반 → `ok=False` + JSON Schema validator 에러 메시지.
- **의존**: M_06, M_07, M_09, M_05b 내부의 ScreenshotService(간단 래퍼).
- **비고**: filesystem MCP 등 외부 MCP 툴은 upstream `ToolManager`가 별도로 처리하며 여기서 중복 등록하지 않는다.

### M_06 DocumentIngest (파서 + 청킹 + 임베딩)

- **분류**: NEW
- **상태**: ✅ DONE (Critic PASS R2, 2026-04-23; R1 FAIL 이력은 reviews/M_06_DocumentIngest_REVIEW.md 참조). `data/Documents/` 단일 문서 루트. HWPX 네임스페이스 2011/2016 양쪽 지원. doc_id = SHA-256(path-only)[:32] (R1 검수에서 mtime 포함 → 중복 누적 결함 확정, path-only로 수정).
- **목적**: PDF/DOCX/PPTX/HWPX/TXT/MD 파일을 구조화된 `DocumentChunk` 배열로 변환하고 BGE-M3로 임베딩해 LanceDB에 upsert.
- **폴더 기반 카테고리 등록**: 사용자는 `docs/` 루트 아래 유형별 하위 폴더에 파일을 두고 `ingest_directory("docs/")` 한 번으로 전체 등록. 상위 폴더명이 자동으로 `category`가 된다.
  ```
  docs/
  ├── 규정/       → category="규정"
  ├── 매뉴얼/     → category="매뉴얼"
  ├── 회의록/     → category="회의록"
  └── 공지/       → category="공지"
  ```
  - `ingest_file(path)`은 `category=None`으로 저장 (폴더 컨텍스트 없이 단일 파일 등록 시).
  - `ingest_directory(path, category_from_subdirs=True)`가 기본값. `False`로 설정 시 `category=None` 일괄 저장.
  - V1에서 카테고리 필터 검색 UI는 없으나 LanceDB에 필드로 저장해 V2 필터링(`retrieve(query, category="규정")`) 을 대비한다.
- **지원 포맷과 라이브러리**
  - PDF → `pypdfium2` (페이지·텍스트 박스·좌표)
  - DOCX → `python-docx` (단락·제목 스타일)
  - PPTX → `python-pptx` (슬라이드 번호·제목·도형 텍스트)
  - HWPX → zipfile + `lxml` (스파이크 전략 확정, `docs/research/hwpx_spike.md`)
  - TXT / MD → 표준 `pathlib`, MD는 `markdown-it-py` 헤더 분리
- **공개 API**
  ```python
  @dataclass
  class DocumentChunk:
      doc_id: str            # SHA-256 of source path + mtime
      doc_name: str
      category: str | None   # 상위 폴더명 자동 추출. 단일 파일 등록 시 None.
      page: int | None       # PDF/PPTX에서 의미
      section: str | None    # HWPX 섹션, MD 헤더
      chunk_id: str          # UUIDv4
      text: str
      bbox: tuple[float,float,float,float] | None  # PDF만 채움
      source_path: str

  class DocumentIngest:
      def __init__(self, embedder: Embedder, store: VectorStore,
                   chunk_chars: int = 800, overlap_chars: int = 100) -> None: ...
      async def ingest_file(self, path: str,
                            category: str | None = None) -> int: ...  # 반환: upsert된 청크 수
      async def ingest_directory(self, path: str, recursive: bool = True,
                                 category_from_subdirs: bool = True) -> int: ...
      async def remove_document(self, doc_id: str) -> int: ...
  ```
- **에러**: 지원 포맷 외 → `UnsupportedFormatError`. 손상 파일 → 로그 경고 + skip(배치에서 전체 실패 금지).
- **의존**: M_07의 `Embedder`, `VectorStore` 인터페이스.

### M_07 VectorSearch (BGE-M3 + LanceDB + 인용 포매터)

- **분류**: NEW
- **상태**: ✅ DONE
- **목적**: 임베딩/저장/검색/인용 포맷까지 전담. 관련 문서 없음 판정 로직 포함.
- **공개 API**
  ```python
  class Embedder:
      def __init__(self, model_dir: str, device: str = "cpu",
                   batch_size: int = 32, normalize: bool = True) -> None: ...
      def embed_passages(self, texts: list[str]) -> np.ndarray: ...  # (N, 1024) float32
      def embed_query(self, text: str) -> np.ndarray: ...            # (1024,) float32

  class VectorStore:
      def __init__(self, db_path: str, table: str = "chunks") -> None: ...
      def upsert(self, chunks: list[DocumentChunk],
                 vectors: np.ndarray) -> int: ...          # sync
      def search(self, query_vec: np.ndarray,
                 top_k: int = 8,
                 category: str | None = None) -> list[SearchHit]: ...  # sync
          # category 지정 시 해당 카테고리 내에서만 ANN 검색 (LanceDB where절)
      def delete_by_doc_id(self, doc_id: str) -> int: ...              # sync

  @dataclass(frozen=True)
  class SearchHit:
      """평면 dataclass — chunk: DocumentChunk 중첩 금지 (specs/M_07 §15.2).
      ToolRouter router.py가 hit.doc_name 등 평면 접근을 사용한다.
      """
      doc_id: str
      doc_name: str
      category: str | None
      page: int | None
      section: str | None
      chunk_id: str
      text: str
      bbox: tuple[float, float, float, float] | None
      source_path: str
      score: float   # cosine similarity 0..1

  class RagService:
      def __init__(self, embedder: Embedder, store: VectorStore,
                   min_score: float = 0.35, top_k_max: int = 20) -> None: ...
      def retrieve(self, query: str, top_k: int = 8,
                   category: str | None = None) -> RetrievalResult: ...
          # sync 함수 확정 (specs/M_07 §15.1). ToolRouter가 run_in_executor로 감싸 호출.
          # category 지정 시 VectorStore.search에 그대로 전달
      def format_citation(self, hit: SearchHit) -> str: ...
          # 예: "`예산지침.pdf` 12페이지, '예산 승인 절차' 섹션"

  @dataclass(frozen=True)
  class RetrievalResult:
      hits: list[SearchHit]
      found: bool              # 최상위 score가 min_score 이상인가
      no_match_reason: str | None
  ```
- **에러**: LanceDB I/O 실패 → `VectorStoreError`. 모델 로드 실패 → `EmbedderError`.
- **의존**: `sentence-transformers`, `lancedb`, `pyarrow`, `numpy`.
- **비고**: `async def retrieve` → `def retrieve` 정정 (specs/M_07_VectorSearch_SPEC.md §15.1 결정 사항). `SearchHit`은 평면 dataclass로 확정 (§15.2). Critic PASS 후 상태 ✅ DONE으로 갱신.

### M_08 AvatarState (스프라이트 스왑 백엔드)

- **분류**: NEW (+ 부분 REUSE: upstream `Live2dModel.extract_emotion` 함수 로직만 이식)
- **상태**: ✅ DONE (Critic PASS R3, 2026-04-19; R1/R2 FAIL 이력은 reviews/M_08_*_REVIEW{,_R2,_R3}.md 참조)
- **목적**: LLM 출력에서 `[emotion:happy]` 태그를 추출하고 현재 스프라이트 상태를 프론트에 송신.
- **공개 API**
  ```python
  Emotion = Literal["neutral","happy","surprised","sad","worried","thinking","sleepy","study"]
  # study 포함 8종. LLM 발화 파싱은 7종(_SPOKEN_EMOTIONS, study 제외).

  @dataclass(frozen=True, slots=True)
  class AvatarEvent:
      emotion: Emotion
      crossfade_ms: int = 250  # 200~300, 범위 밖 ValueError (clamp 금지)
      speaking: bool = False   # 립싱크 opacity 펄스 토글

  SendTextCallback = Callable[[dict[str, Any]], Awaitable[None]]

  class AvatarState:
      def __init__(self, default: Emotion = "neutral") -> None: ...
      def extract_emotion(self, text: str) -> tuple[str, Emotion | None]: ...
          # 반환: (태그 제거된 텍스트, 추출된 감정)
          # 미지/비발화 키(study 포함) → "neutral" 폴백 + logger.warning
      async def push_event(self, event: AvatarEvent, send_text: SendTextCallback) -> None: ...
      @property
      def current_emotion(self) -> Emotion: ...
      @property
      def is_speaking(self) -> bool: ...
      def make_event(self, emotion: Emotion, *, crossfade_ms: int = 250, speaking: bool = False) -> AvatarEvent: ...
  ```
- **에러**: 알 수 없는 감정 태그 → `neutral`로 폴백 + 로그 경고.
- **의존**: 표준 라이브러리(re, dataclasses, asyncio) + loguru. 새 외부 의존성 없음.
- **비고**: 실제 PNG 렌더는 프론트엔드(M_12) 담당. 백엔드는 상태 이벤트만 송신.
  §16 참조: Emotion 8종(study 추가), SendTextCallback dict 컨벤션, current_emotion/is_speaking/make_event 신설.

### M_09 CalendarService (SQLite 일정 CRUD)

- **분류**: NEW
- **상태**: ✅ DONE
- **목적**: `add_event`, `get_events`, `update_event`, `delete_event`를 제공. SQLite 단일 파일(`data/calendar.db`).
- **공개 API**
  ```python
  @dataclass(frozen=True)
  class Event:
      id: int
      title: str
      start: datetime      # timezone-aware, default Asia/Seoul
      duration_minutes: int
      description: str | None
      created_at: datetime

  class CalendarService:
      def __init__(self, db_path: str, *, default_tz: ZoneInfo = _KST) -> None: ...
      def add_event(self, title: str, start: datetime,
                    duration_minutes: int,
                    description: str | None = None) -> Event: ...
      def get_events(self, start: datetime, end: datetime) -> list[Event]: ...
      def get_event(self, event_id: int) -> Event | None: ...
      def update_event(self, event_id: int, **fields: Any) -> Event: ...
      def delete_event(self, event_id: int) -> bool: ...
      def events_due_within(self, minutes: int) -> list[Event]: ...
      def close(self) -> None: ...
  ```
  > **동기 API 확정**: 호출자(M_05b/M_11)가 `run_in_executor`로 sync 함수를 기대하며,
  > SQLite 표준 라이브러리는 async를 지원하지 않으므로 sync API로 확정
  > (specs/M_09_CalendarService_SPEC.md §4.3).
- **에러**: `duration_minutes<=0` → `CalendarValidationError`. 동일 `(title, start)` 중복 → 경고 + 새 id 부여(중복 허용).
- **의존**: 표준 `sqlite3`, `datetime`, `zoneinfo` (추가 패키지 없음).

### M_10 IdleMonitor (Windows 유휴 감지)

- **분류**: NEW
- **상태**: ✅ DONE (Critic PASS R2, 2026-04-19; R1 FAIL 이력은 reviews/M_10_*_REVIEW{,_R2}.md 참조)
- **목적**: 마우스·키보드 입력 이벤트를 관찰해 유휴(`idle`)·과로(`overwork`) 상태 전이를 방출. **쿨다운·DND 정책은 M_11 책임**(specs/M_10 §D-1).
- **공개 API** (specs/M_10 §4 결정 반영)
  ```python
  IdleEvent = Literal["idle_rest", "overwork"]

  class IdleMonitor:
      def __init__(self,
                   idle_threshold_min: int = 45,
                   overwork_threshold_min: int = 120,
                   active_gap_seconds: int = 60,
                   poll_interval_seconds: float = 1.0,
                   clock: Callable[[], datetime] = datetime.now,
                   backend: _IdleBackend | None = None) -> None: ...
      def start(self) -> None: ...           # 멱등 (D-11)
      async def stop(self) -> None: ...       # async (D-12)
      def set_dnd(self, enabled: bool) -> None: ...
      def on_event(self, callback: IdleEventCallback | None) -> None: ...   # 단일 슬롯, None 해제 (D-2)
      def last_input_at(self) -> datetime: ...
      def seconds_since_last_input(self) -> float: ...
      def _tick(self, now: datetime) -> None: ...  # 테스트 가시 (D-12)
  ```
  > **초안 대비 변경**: `cooldown_min`·`dnd_enabled` 생성자 인자 제거(D-1). `active_gap_seconds`/`poll_interval_seconds`/`clock`/`backend` DI 추가(D-12). 3계층 백엔드(PynputBackend→Win32IdleBackend→NoopBackend) 자동 폴백 — R-10 필수(D-9).
- **에러**: `pynput` 훅 초기화 실패 → 로그 경고, 서비스 자체는 no-op로 계속 기동(기능 비활성).
- **의존**: `pynput`, `asyncio`.

### M_11 ProactiveDispatcher (스케줄러 + 쿨다운 + DND)

- **분류**: NEW
- **상태**: ✅ DONE (Critic PASS R2 2026-04-19, R3 fast-follow PASS 2026-04-19, MINOR coverage PASS 2026-04-21; 리뷰 reviews/M_11_*_REVIEW{,_R2,_R3,_MINOR_REVIEW}.md 참조)
- **목적**: APScheduler cron(매일 09:00 KST) + interval(1분) 잡과 M_10 IdleMonitor 콜백을
  받아 토픽별 쿨다운·DND 정책을 적용한 뒤 upstream `ai-speak-signal` 경로로 WebSocket 발화 지시 발송.
- **공개 API**
  ```python
  ProactiveTopic = Literal["morning_briefing", "event_reminder", "idle_rest", "overwork"]
  SendTextCallback = Callable[[dict[str, Any]], Awaitable[None]]

  class ProactiveDispatcher:
      def __init__(self, *,
                   calendar: CalendarService,
                   idle_monitor: IdleMonitor,
                   send_text: SendTextCallback,
                   morning_time: str = "09:00",
                   timezone: ZoneInfo = ZoneInfo("Asia/Seoul"),
                   reminder_lead_minutes: int = 10,
                   reminder_check_interval_seconds: int = 60,
                   cooldown_min: int = 30,
                   dnd_enabled: bool = False,
                   clock: Callable[[], datetime] = datetime.now,
                   scheduler: BaseScheduler | None = None) -> None: ...
      async def start(self) -> None: ...
      async def stop(self) -> None: ...
      async def emit(self, topic: ProactiveTopic,
                     context: dict[str, Any] | None = None) -> bool: ...
          # 반환 False: 쿨다운/DND로 드롭된 경우
      def set_dnd(self, enabled: bool) -> None: ...
  ```
  > **페이로드 4키**: `{type: "ai-speak-signal", text: str, topic: ProactiveTopic, context: dict}`.
  > 쿨다운은 토픽별 독립. DND는 M_10/M_11 이중 체크(본 모듈이 M_10에 전파).
- **에러**: APScheduler 초기화 실패 → `_enabled=False` 강등 + logger.error, 앱 기동 계속.
  send_text 예외 → logger.error + return False (다음 틱 재시도).
- **의존**: `APScheduler>=3.10,<4`, M_09 CalendarService, M_10 IdleMonitor.
- **비고**: 공개 API는 Critic PASS 대기, 시그니처는 specs/M_11 §4 기준.

### M_12 Frontend (Electron + 스프라이트 렌더러)

- **분류**: FORK (upstream-Web) + NEW (스프라이트 렌더러, 펫 모드 검증)
- **상태**: ✅ DONE — P1(WS·Avatar·기반) → P2(SpriteAvatarRenderer·PetWindow·vitest) → P3(CitationViewer·bbox·FallbackCard) → P4(CitationViewer 회귀·PDF 파이프라인) → P5(RSS 감시·적대적 테스트·E2E skeleton·NSIS 구성·DoD) 완료. Critic PASS 조건 달성. **Windows QA pending**(reviews/M_12_Frontend_P5_DOD.md §66): `PetWindowController.enable()` E2E, click-through hover IPC, CSP 실차단, RSS 350MB·펫 모드 CPU 2% 벤치, electron-builder NSIS, Playwright 3종(pet-mode/citation/basic) — 모두 WSL 제약으로 skeleton만 준비, Windows 실기기에서 수행.
- **목적**: 채팅 UI, 스프라이트 아바타 렌더, 펫 모드 창(투명 + 항상 위 + 클릭 관통), 드래그 이동, PDF viewer로 인용 링크 열기.
- **주요 책임**
  - WebSocket 연결 유지, upstream 메시지 타입 호환(`docs/research/frontend_structure.md` 표).
  - 아바타 렌더러(`AvatarRenderer` JS 인터페이스): 스프라이트 PNG 7종 crossfade 200~300ms, 숨쉬기 scaleY 1.0→1.02 2s, 깜빡임 5~10s, 말할 때 opacity 펄스.
  - 펫 모드: Electron `BrowserWindow({ transparent: true, frame: false, alwaysOnTop: true, webPreferences:{ contextIsolation: true }})` + `setIgnoreMouseEvents(true, { forward: true })`.
  - 인용 클릭: 로컬 PDF를 pdf.js로 열어 해당 페이지 스크롤 + 바운딩 박스 하이라이트(bbox 있을 때만).
- **공개 API(프론트 내부)**
  ```ts
  interface AvatarRenderer {
      setEmotion(emotion: Emotion, crossfadeMs: number): void;
      setSpeaking(on: boolean): void;
      mount(container: HTMLElement): void;
      dispose(): void;
  }
  ```
- **의존**: upstream `frontend` 서브모듈(FORK), Electron 30.x, React 18, pdf.js.

---

## 모듈별 상태 표

| ID | 이름 | 분류 | 상태 | 의존 |
|---|---|---|---|---|
| M_01 | AppCore | EXTEND | ✅ DONE | upstream |
| M_02 | ASREngine | EXTEND | ✅ DONE | M_01 |
| M_03 | VADEngine | REUSE | ✅ DONE | M_01 |
| M_04 | TTSEngine | NEW | ✅ DONE | M_01 |
| M_05 | LLMAgent | EXTEND | ✅ DONE | M_01, M_05b |
| M_05b | ToolRouter | NEW | ✅ DONE | M_06, M_07, M_09 |
| M_06 | DocumentIngest | NEW | ✅ DONE | M_07 |
| M_07 | VectorSearch | NEW | ✅ DONE | — |
| M_08 | AvatarState | NEW | ✅ DONE | M_01 |
| M_09 | CalendarService | NEW | ✅ DONE | specs/M_09_CalendarService_SPEC.md |
| M_10 | IdleMonitor | NEW | ✅ DONE | — |
| M_11 | ProactiveDispatcher | NEW | ✅ DONE | M_09, M_10 |
| M_12 | Frontend | FORK+NEW | ✅ DONE | M_01 메시지 스키마, CR-10/CR-11 |
| M_13 | MeetingMinutes | NEW | 🚧 IN_PROGRESS | M_05 (GemmaChatAgent.complete_json), CR-MM-A |

---

## 공통 규약

- 모든 모듈은 `specs/M_NN_*.md`에 다음을 포함해야 한다(CLAUDE.md "산출물 체크리스트"): 공개 API 시그니처, 에러 정책, 성능·메모리 요구, 테스트 케이스(정상 ≥5, 엣지 ≥5, 적대적 ≥3), DoD.
- 모든 `async def`는 취소(`asyncio.CancelledError`) 전파 허용. 자원 정리는 `try/finally`로.
- 모든 네트워크 호출은 `127.0.0.1`/`localhost`/`OLLAMA_BASE_URL` 환경변수(사설 IP 검증됨)만 허용.
- 새 의존성 추가 시 `pyproject.toml` 수정 + `scripts/bundle_deps.sh` 업데이트 필수.
- **개발 환경**: Windows 10/11 · macOS 14+ (Apple Silicon) · Linux (x86-64/ARM) (CR-12, 2026-04-22). 배포 타깃은 Windows 전용 유지.

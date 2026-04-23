# M_13 MeetingMinutes Critic Review

**검수자**: critic (opus, fresh session)
**검수 일시**: 2026-04-23
**대상 커밋 범위**: M_13 신규 모듈 + 통합 배선
**검수 방식**: 적대적 리뷰 (스펙 §16 DoD 기준 + 비기능 가드 + 통합 정합성)

## 판정: REJECT

근거: BLOCKER 3건 + MAJOR 4건. 특히 (1) 스펙이 명시적으로 요구하는 APScheduler cleanup 잡이 **존재하지 않음** (2) `mypy` 타입 체크 3건 실패로 §16.4 무조건 통과 요구사항 불충족 (3) 예외 경로에서 아바타가 "writing" 상태로 영구 고착 가능. 하나라도 BLOCKER가 있으면 REJECT 판정. 재구현·재검수 필요.

---

## 심각도별 결함 목록

### BLOCKER (REJECT 사유)

#### B-1. APScheduler cleanup 잡이 전혀 존재하지 않음 (스펙 §8.4 / §9.2 / §16.1 위반)

- **스펙 §8.4 명시**:
  > `MeetingMinutesService.cleanup_expired()`를 APScheduler interval(1시간)로 호출.
  > 등록 위치: ... M_01 `AppServiceContext.load_app_services`에서 별도 `BackgroundScheduler` 1개를 시작한다. **결정**: M_01에 `_temp_cleanup_scheduler` 슬롯 추가.
- **스펙 §9.2 명시**:
  > `AppServiceContext.close()`:
  > 1. self.meeting_minutes_service.aclose() (sync 코드만, 1ms 미만)
  > 2. self._temp_cleanup_scheduler.shutdown(wait=True)

- **현실**:
  ```bash
  $ grep -rn "_temp_cleanup_scheduler\|cleanup_scheduler\|cleanup_expired" src/app/
  # → 일치 없음
  ```
  - `src/app/service_context.py`에는 `_temp_cleanup_scheduler` 슬롯 부재.
  - `MeetingMinutesService.cleanup_expired()` 메서드는 정의되어 있으나, **어디에서도 스케줄링되지 않는다**.
  - 결과: 24시간 후에도 임시 HWPX 파일이 영구히 누적 → R-MM-4(디스크 풀) 실현. 운영 환경에서 7일이면 디스크 고갈.

- **REQUIREMENTS.md 영향**: §0(완전 오프라인 단일 사용자) — 사용자가 1일 5회 회의록 생성 × 2장(2MB) × 7일 = 70MB 누적 → SSD 여유 적은 인트라넷 PC에서 배포 사고 가능.
- **심각도**: BLOCKER. 스펙 DoD §16.1 "src/app/service_context.py::load_app_services에 조립 1블록 추가" + §9.2 close 순서 두 항목 모두 미충족.
- **권고 조치**:
  1. `AppServiceContext.__init__`에 `self._temp_cleanup_scheduler: AsyncIOScheduler | None = None` 슬롯 추가.
  2. `load_app_services` M_13 블록에서 `AsyncIOScheduler` 인스턴스 생성 + `add_job(self.meeting_minutes_service.cleanup_expired, IntervalTrigger(hours=1), ...)` + `start()`.
  3. `close()`에 `_temp_cleanup_scheduler.shutdown(wait=True)` 추가.
  4. `tests/meeting_minutes/test_service.py`에 스케줄러 통합 테스트 1건 추가(테스트는 `MemoryJobStore` + 즉시 실행 타이머).

#### B-2. mypy 타입 체크 3건 실패 — DoD §16.4 무조건 통과 요구사항 미충족

- **현실**:
  ```bash
  $ uv run mypy src/meeting_minutes/tool.py
  src/meeting_minutes/tool.py:77: error: Argument "error_code" to "ToolResult" has incompatible type "Literal['invalid_llm_response']"; expected "Literal['unknown_tool', 'invalid_arguments', 'service_unavailable', 'handler_exception', 'screenshot_failed', 'continuous_already_running', 'continuous_not_running'] | None"  [arg-type]
  src/meeting_minutes/tool.py:84: error: ... "schema_violation" ...
  src/meeting_minutes/tool.py:91: error: ... "hwpx_write_failed" ...
  Found 3 errors in 1 file
  ```
- **원인**: `src/tool_router/types.py::ToolErrorCode` Literal에 `"invalid_llm_response"`, `"schema_violation"`, `"hwpx_write_failed"` 미등록. `tool.py` 핸들러는 이 3가지 코드를 사용한다.
- **CLAUDE.md 명시**:
  > Validator는 구현 완료 판정 전에 위 네 가지(`ruff format`, `ruff check`, `mypy src/`, `pytest`)를 모두 실행한다. **하나라도 실패하면 FAIL.**
- **스펙 §10 에러 처리 정책 표가 명시한 코드와 ToolErrorCode Literal 정의가 어긋남** — 스펙 §10이 추가될 때 `tool_router/types.py`가 동시 갱신되었어야 한다.
- **심각도**: BLOCKER. CLAUDE.md DoD 위반.
- **권고 조치**: `src/tool_router/types.py::ToolErrorCode`에 3개 리터럴 추가 + `tests/tool_router/`에 회귀 테스트.

#### B-3. 예외 경로에서 아바타가 "writing" 감정에 영구 고착 (스펙 §10 + 사용자 체크리스트 #6 위반)

- **위치**: `src/meeting_minutes/tool.py:63~92`
- **결함**:
  ```python
  await _set_writing_state(avatar_state)         # writing 진입
  try:
      result = await service.generate(...)
      await _restore_neutral_state(avatar_state)  # 성공 시만 복귀
      return ToolResult(ok=True, ...)
  except MeetingDraftError as exc:               # ← _restore_neutral_state 호출 누락
      return ToolResult(ok=False, ...)
  except MeetingDraftValidationError as exc:     # ← 누락
      return ToolResult(ok=False, ...)
  except MeetingMinutesError as exc:             # ← 누락
      return ToolResult(ok=False, ...)
  except Exception as exc:
      await _restore_neutral_state(avatar_state)  # 마지막 catch-all에만 있음
      return ToolResult(ok=False, ...)
  ```
- **결과**: LLM 호출 실패(`MeetingDraftError`), Schema 위반(`MeetingDraftValidationError`), HWPX 쓰기 실패(`MeetingMinutesError`) **3가지 정상적인 비즈니스 예외에서 모두** 아바타가 "writing" 상태로 멈춘다. 사용자는 다음 채팅을 칠 때까지 아바타가 멈춰 있는 것을 본다.
- **사용자 체크리스트 #6 명시**: "`push_emotion("writing")` → `push_emotion("neutral")` 복귀가 예외 경로에서도 보장되는가?" — 답: **NO**.
- **심각도**: BLOCKER (UX critical, 스펙 §10 정상 동작 가정 위배).
- **권고 조치**: `try/except/finally` 패턴으로 재구성:
  ```python
  await _set_writing_state(avatar_state)
  try:
      result = await service.generate(...)
      return ToolResult(ok=True, payload=result)
  except MeetingDraftError as exc:
      ...
  finally:
      await _restore_neutral_state(avatar_state)
  ```
  + `tests/meeting_minutes/test_tool.py` 신설하여 4가지 예외 경로 모두에서 `push_emotion("neutral")` 호출 검증.

---

### MAJOR (CONDITIONAL_PASS 조건이지만 BLOCKER 동반으로 REJECT 가산)

#### M-1. 1page fixture가 1page 분량 한도(14줄)를 초과 — 테스트가 의도와 다른 코드 경로 검증

- **위치**: `tests/meeting_minutes/conftest.py:28~91` (`valid_draft_dict_1page`)
- **계산**: summary 4개 + detail 3개. 줄 수 합 = 4+2+1+2 + 4+2+1 = **16줄** (한도 14줄 초과).
- **영향**:
  - `test_n1_generate_1page`는 `complete_json` mock이 항상 같은 fixture를 반환하므로, generator는 `_check_length_violations`에서 위반을 검출 → 재시도 → 같은 fixture 재반환 → max_retries 소진 → 경고 로그 후 통과한다.
  - 즉 **N-1 정상 케이스 테스트가 실제로는 "위반 후 통과(degraded)" 경로를 검증**한다. 정상 흐름 검증이 아님.
  - 테스트 본문의 코멘트("1장 fixture가 14줄 한도를 초과하므로 generator가 재시도 후 통과(경고 로깅).")가 이 사실을 자인하면서도 **수정 없이 통과시켰다**.
- **심각도**: MAJOR. 정상 케이스 테스트 #1이 정상 경로를 커버하지 않음.
- **권고 조치**: `valid_draft_dict_1page`를 14줄 이하로 축소(예: summary 3개+detail 2개, 부연 1~2개씩). 별도 fixture `valid_draft_dict_1page_violation`을 만들어 E-5 retry 테스트에만 사용.

#### M-2. `MeetingMinutesService(agent=None, ...)` 후 monkey-patch 배선 — 깨지기 쉬운 조립 패턴

- **위치**: `src/app/service_context.py:319~325` + `:250~252`
  ```python
  self.meeting_minutes_service = MeetingMinutesService(
      agent=None,                # type: ignore[arg-type]
      template_path=...,
      ...
  )
  # 이후 init_agent에서:
  self.meeting_minutes_service._generator._agent = gemma_agent  # type: ignore[attr-defined]
  ```
- **문제**:
  1. `agent=None`으로 생성된 직후 `MeetingMinutesService.generate()`가 호출되면(예: init_agent 실패, init_agent 미호출) **`AttributeError: 'NoneType' object has no attribute 'complete_json'`** — 정의되지 않은 비즈니스 예외.
  2. private 속성(`_generator._agent`)을 외부에서 덮어쓰는 건 캡슐화 위반. spec §4.5 시그니처가 `agent: Any`(필수 인자)인데 None을 강제 주입.
  3. `init_agent` 실패(Ollama unreachable) 시 `meeting_minutes_service`가 좀비 상태(템플릿은 로드됐으나 LLM 호출 불가).
- **심각도**: MAJOR. 운영 안정성 저해. M_05 의존 순서가 잘못 설계됨(스펙 §15 옵션 A는 "M_05 complete_json CR가 머지된 후에만 builder 착수"라고 명시 — agent 없이 service를 만든다는 의미가 아님).
- **권고 조치**:
  - 옵션 a: `load_app_services`를 `init_agent` 이후에 호출하도록 부트 순서 재정렬, agent를 정상 인자로 전달.
  - 옵션 b: `MeetingMinutesService`에 `set_agent(agent)` 공개 메서드 추가 + 내부에서 `agent is None`이면 `MeetingMinutesError("agent not initialized")` raise.
  - `agent=None` 직접 주입은 즉시 제거.

#### M-3. `HwpxWriter._placeholder_cache`가 전혀 사용되지 않음 — 스펙 §11.3 최적화 위반

- **위치**: `src/meeting_minutes/hwpx_writer.py:126~144` (`__init__`에서 캐시 구축) vs `:181~191` (`write()`에서 다시 트리 순회)
- **스펙 §11.3 명시**:
  > placeholder 노드 캐시는 `__init__`에서 트리 1회 순회 후 dict로 보관(매 호출마다 재탐색 회피).
- **현실**: `write()`는 매 호출마다 `etree.fromstring` + `root.findall(".//hp:p", ns)` 재실행 + 11개 placeholder 전체 재탐색. `__init__`에서 만든 `_placeholder_cache`는 누락 검증에만 쓰이고 폐기.
- **이유**: lxml `_Element` 노드는 deepcopy/serialize 후 reparse하면 새 객체가 되므로 원본 노드 참조를 cross-write 재사용 불가. 따라서 의도 자체는 합리적이지만, 스펙 §11.3은 그 사실을 인지하지 못한 채 "캐시 보관"을 명시했다.
- **심각도**: MAJOR (스펙 위반은 사실이지만 lxml 특성상 단순 캐시는 불가능).
- **권고 조치**: 스펙 §11.3을 현실에 맞게 보정하거나, 템플릿 bytes 자체를 `__init__`에서 한 번만 파싱한 후 `_template_root_pristine`을 깊게 복제(`copy.deepcopy`)해 `write()`마다 재사용하는 방식으로 재설계. 양자택일을 명시적으로 결정해야 한다.

#### M-4. M_13 통합 단위 테스트 부재 — `tool.py`, ToolRouter 분기, ws_handler 배선 검증 0건

- **누락된 테스트**:
  1. `tests/meeting_minutes/test_tool.py` 부재. `handle_create_meeting_minutes`의 4가지 예외 분기, avatar_state push_emotion 호출 횟수, error_code 매핑이 전혀 검증되지 않음. (BLOCKER B-3 발견의 직접 원인)
  2. `tests/tool_router/test_router.py`(기존)에 `create_meeting_minutes` 분기 테스트 부재. `ToolRouter.dispatch("create_meeting_minutes", ...)` 경로가 회귀 보호받지 못함.
  3. `tests/app/test_service_context.py`(있다면)에 M_13 조립 블록 검증(특히 loopback 검증, agent monkey-patch) 부재.
  4. `tests/app/test_ws_handler.py`에 `set_send_text` 호출 회귀 테스트 부재.
- **스펙 §16.3**: "정상 ≥ 6, 엣지 ≥ 6, 적대적 ≥ 4" — 합계는 만족하지만 통합 표면적이 비어 있다.
- **심각도**: MAJOR. Critic이 BLOCKER B-3을 발견할 수 있었던 유일한 이유는 코드를 직접 읽었기 때문이다 — 자동 회귀 보호 0.
- **권고 조치**: 위 4건 테스트 추가 후 재제출.

---

### MINOR (권고)

#### m-1. `test_e6_download_base_url_non_loopback`이 `asyncio.get_event_loop().run_until_complete()` 사용

- **위치**: `tests/meeting_minutes/test_routes.py:115`
- **문제**: Python 3.12+에서 `DeprecationWarning: There is no current event loop`. Python 3.13+에서는 RuntimeError 가능성. `pytest-asyncio` `@pytest.mark.asyncio` 데코레이터를 써야 함.
- **권고**: 함수를 `async def`로 변환 + `@pytest.mark.asyncio` 적용.

#### m-2. `ToolRouter.tool_specs()` docstring "4개 툴" → "5개 툴"

- **위치**: `src/tool_router/router.py:84~90`
- **현실**: 5종(`add_event`, `get_events`, `search_docs`, `take_screenshot`, `create_meeting_minutes`).
- **권고**: 단순 docstring 수정.

#### m-3. `_set_para_text`가 placeholder가 단락의 첫 `<hp:t>`에 없을 때 silent fail

- **위치**: `src/meeting_minutes/hwpx_writer.py:63~70`
  ```python
  def _set_para_text(p, ns, text):
      t_elems = p.findall(".//hp:t", ns)
      if not t_elems:
          return        # ← 무음 종료
      t_elems[0].text = text
      ...
  ```
- **시나리오**: 템플릿 단락의 첫 번째 `<hp:t>`가 빈 문자열(스타일 prefix 등)이고 placeholder가 두 번째 `<hp:t>`에 있는 경우, placeholder 검색은 `_get_full_text`로 통과하지만 `_set_para_text`는 첫 번째 `<hp:t>`만 갱신하므로 placeholder가 잔존한다. N-3 테스트는 현재 템플릿에서만 통과하며 다른 placeholder 배치에 취약.
- **권고**: placeholder가 위치한 정확한 `<hp:t>`를 찾아 그 노드만 갱신하거나, 모든 `<hp:t>`의 text를 합쳐 placeholder만 치환 후 첫 노드에 합쳐 넣고 나머지를 빈 문자열로.

#### m-4. `MeetingDraftGenerator.generate` `except Exception` 광범위 캐치

- **위치**: `src/meeting_minutes/generator.py:181`
- **위험**: `asyncio.CancelledError`도 잡히지 않는지 확인 필요. Python 3.8+에서 CancelledError는 BaseException이라 안전하지만, `KeyboardInterrupt` 등은 잡히지 않으므로 의도와 다른 동작.
- **권고**: `except (TimeoutError, OSError, RuntimeError) as exc:` 등 구체화 또는 명시적 주석 추가.

#### m-5. `DnD` 표 누락 / 24h TTL `path.unlink(missing_ok=True)` race

- **위치**: `service.py:108~114`
- **문제**: 만료 파일을 즉시 삭제하는 로직이 있는데, 동시 다운로드 중인 다른 핸들러가 해당 파일을 읽고 있을 때 OSError(특히 Windows)가 발생할 수 있다. `missing_ok=True`만으로는 부족.
- **권고**: Linux/Windows에서 race 검증 + 필요 시 try/except OSError 추가.

#### m-6. `download_base_url` loopback 검증이 `https://` 와 `localhost`까지만 — 사설 IP(`10.x`, `192.168.x`) 미허용

- **위치**: `service_context.py:303~308`
- **스펙 §8.3**: "외부 노출 IP를 막기 위해 **`127.0.0.1` 또는 사설 IP만 허용**"
- **현실**: `127.0.0.1`/`localhost`만 허용. 사설 IP(예: 사내망 `192.168.10.5:12393`)는 강등됨.
- **권고**: `app/url_guard.py::enforce_private_url` 같은 기존 헬퍼 재사용해 RFC1918 사설 대역 허용.

#### m-7. 스펙 §16.6 "외부 네트워크 호출 0건 검증" 문서 갱신 누락

- **현실**: `grep -rE "https?://" src/meeting_minutes` → loopback URL과 JSON Schema `$schema` URL만 — 통과. 그러나 검증 로그/문서가 reviews에 없음.
- **권고**: 본 review 통과 후 reviews 또는 docs에 명시.

---

## 스펙 vs 구현 매핑 검증

| 스펙 항목 | 구현 위치 | 상태 |
|---|---|---|
| §4.1 MeetingDraft + 4종 dataclass | `src/meeting_minutes/types.py` | ✅ |
| §4.2 MeetingDraftGenerator | `src/meeting_minutes/generator.py:123` | ✅ |
| §4.3 HwpxWriter | `src/meeting_minutes/hwpx_writer.py:73` | ✅ |
| §4.4 handle_create_meeting_minutes | `src/meeting_minutes/tool.py:32` | ⚠ avatar_state finally 누락(B-3) |
| §4.5 MeetingMinutesService | `src/meeting_minutes/service.py:22` | ⚠ agent monkey-patch(M-2) |
| §4.6 에러 6종 | `src/meeting_minutes/errors.py` | ✅ |
| §5 JSON Schema | `src/meeting_minutes/schemas.py` | ✅ |
| §6 prompts | `src/meeting_minutes/prompts.py` | ✅ |
| §7.4 _check_length_violations | `src/meeting_minutes/generator.py:39` | ✅ (단, fixture 한도 위반 M-1) |
| §8.1 라우터 | `src/app/meeting_minutes_routes.py` | ✅ |
| §8.4 APScheduler interval(1h) cleanup | **없음** | ❌ **B-1** |
| §9.2 close 순서 (cleanup_scheduler.shutdown 포함) | `service_context.py:415~420` | ❌ scheduler 자체 없음(B-1) |
| §10 에러 표 (error_code 매핑) | `tool.py:72~92` | ❌ Literal 미등록(B-2) |
| §11.3 placeholder 캐시 | `hwpx_writer.py:126` 구축 후 미사용 | ⚠ M-3 |
| §13.6 service_context 1블록 + slot | `service_context.py:55, 293~332` | ⚠ scheduler slot 누락 |
| §15 CR-MM-A complete_json | `agent/gemma_chat_agent.py:419` | ✅ |
| §16.4 mypy 통과 | mypy 3 errors | ❌ **B-2** |
| §16.5 lxml 의존성 추가 + bundle_deps | pyproject + bundle_deps | ✅ |
| §16.6 외부 네트워크 0건 | grep 통과 | ✅ |
| §16.8 docs/MODULES.md 갱신 | M_13 ✅ DONE 등재 (조기 등재) | ⚠ Critic PASS 전 등재 |
| §16.9 수동 QA (한글 뷰어) | review에 미기록 | ⚠ 추후 |

---

## 테스트 커버 검증 (스펙 §12)

| 스펙 케이스 | 구현 테스트 | 상태 |
|---|---|---|
| N-1 1장 라운드트립 | `test_generator.py::test_n1_generate_1page` | ⚠ fixture 자체가 14줄 초과 → degraded 경로 검증(M-1) |
| N-2 2장 라운드트립 | `test_generator.py::test_n2_generate_2page` | ✅ |
| N-3 placeholder 100% 치환 | `test_hwpx_writer.py::test_n3_no_placeholder_remaining` | ✅ |
| N-4 다운로드 라우터 200 | `test_routes.py::test_n4_download_200` | ✅ |
| N-5 cleanup_expired | `test_service.py::test_n5_cleanup_expired` | ✅ (단위만, 스케줄러 누락은 B-1) |
| N-6 ToolRouter dispatch | **없음** | ❌ |
| E-1 transcript 50자 | `test_schema.py::test_e1_transcript_exactly_50_chars` | ✅ |
| E-2 transcript 50000자 | `test_schema.py::test_e2_transcript_exactly_50000_chars` | ✅ |
| E-3 attendees 100명 | `test_schema.py::test_e3_attendees_100_items` | ✅ |
| E-4 next_steps=0 | `test_schema.py::test_e4_empty_next_steps` + `test_hwpx_writer.py::test_write_empty_next_steps` | ✅ |
| E-5 글자수 위반 재시도 | `test_generator.py::test_e5_length_violation_retry` | ✅ |
| E-6 download_base_url 비-loopback 강등 | `test_routes.py::test_e6_download_base_url_non_loopback` | ⚠ 실제로는 `service=None` 핸들러만 검증, `service_context` loopback 검증은 미커버. asyncio API 사용도 deprecated(m-1). |
| A-1 LLM 무한 비-JSON | `test_generator.py::test_a1_infinite_non_json` | ✅ |
| A-2 path traversal | `test_service.py::test_a2_path_traversal` 외 3건 | ✅ |
| A-3 transcript 인젝션 | `test_generator.py::test_a3_injection_transcript` | ⚠ 형식적 — mock LLM이 정상 응답 fixture를 반환하도록 강제했으므로 실제 인젝션 방어는 검증 못함. 시스템 프롬프트가 "회의 결과 보고서 외 거부"임을 강제하는 가드(예: 응답 후 검증) 부재. |
| A-4 손상된 템플릿 | `test_hwpx_writer.py::test_a4_nonexistent_template`, `test_a4_not_a_zip` | ✅ |
| 통합: tool.py 4가지 예외 경로 | **없음** | ❌ → BLOCKER B-3 발견 누락 원인 |
| 통합: avatar_state writing→neutral | **없음** | ❌ |
| 회귀: ToolRouter create_meeting_minutes 분기 | **없음** | ❌ |

---

## 긍정 평가

다음 항목은 스펙 준수가 확인되며 잘 구현되었다:

1. **JSON Schema 정의 정확** — Draft 2020-12 준수, additionalProperties:false, $defs 재사용 패턴 정상.
2. **HWPX 네임스페이스 동적 추출** (`hwpx_writer.py:114~117`) — R-MM-2(2011/2016 혼재) 완화 의도 충실.
3. **mimetype STORED + 첫 엔트리 위치 보존** (`hwpx_writer.py:215~219`) — R-MM-6 완화 충실. 한글 뷰어 호환성 기대.
4. **JSON Schema 위반 시 max_retries=1 + 글자수 위반 시 경고 후 통과** (`generator.py:188~213`) — §7.4 정책 정확 구현.
5. **path traversal 방어 견고** — `uuid.UUID(file_id, version=4)` 파싱 후 `temp_dir / f"{safe_id}.hwpx"` 사용. `../../etc/passwd` 시도는 ValueError(테스트 검증).
6. **lxml deepcopy + parent.insert 패턴** — `_insert_items` 알고리즘이 스펙 §7.3 단계 3을 충실히 구현.
7. **49건 모든 단위 테스트 통과**. `ruff check` 통과.

---

## 검토하지 못한 영역 (다음 Critic 또는 후속 reviewer 참고)

1. **실제 한글 뷰어 호환성 검증** — 스펙 §16.9가 요구하는 수동 QA. 본 Critic 환경에 한글 뷰어 없음. 별도 Windows 실기기 QA 필요.
2. **CPU-only 환경 LLM 응답 시간** — 스펙 §11.1 목표(2장 180s 이내) 미실측. 실 모델 fixture 회귀 테스트 부재.
3. **APScheduler 추가 후 ProactiveDispatcher와의 잡 등록 충돌** — 본 Critic 시점에 cleanup scheduler 자체가 없어 검증 불가. 추가 시 R-MM-3 재검토 필요.
4. **lxml 메모리 누수** — write() 반복 호출 시 `etree._Element` 가비지 컬렉션 거동. Long-running 프로세스에서 모니터링 필요.
5. **Windows에서 `os.scandir` + `unlink` race** — m-5 권고 참조.
6. **e2e 테스트** — `tests/e2e/`에 회의록 생성 시나리오 부재(M_13 자체 책임은 아니나 `integrator` 단계에서 추가 권고).

---

## 총평

본 모듈은 **단위 구현 품질은 합격선**이지만, **스펙이 명시한 핵심 비기능 요구사항(APScheduler cleanup)과 통합 정합성(타입·예외 경로·통합 테스트)에서 다수 결함**이 발견되었다.

특히 **B-1(cleanup 스케줄러 부재)는 스펙 §8.4·§9.2·§16.1 세 군데에서 명시적으로 요구되는데 구현 0건**이며, 운영 환경에서 디스크 누적으로 인한 실서비스 사고를 일으킬 수 있다. **B-2(mypy 3 errors)는 CLAUDE.md DoD 무조건 통과 요구를 정면 위배**한다. **B-3(아바타 writing 영구 고착)은 스펙 §10 에러 표가 정상 동작을 가정하는 정상적 비즈니스 예외 경로에서 UX를 깬다**.

`docs/MODULES.md`에 M_13이 이미 ✅ DONE으로 등재되어 있는데, **본 Critic 통과 전에 등재된 상태**다. CLAUDE.md "산출물 체크리스트" 마지막 두 항목("Critic PASS 기록", "MODULES.md ✅ DONE 갱신")의 순서가 역전되었다. 이 또한 절차적 문제다.

**권고 조치**: REJECT. 위 BLOCKER 3건 + MAJOR 4건 수정 + `docs/MODULES.md`에서 M_13 상태를 "🚧 IN_PROGRESS"로 되돌린 후 fresh critic 재검수.


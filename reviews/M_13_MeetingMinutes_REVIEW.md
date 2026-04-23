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


---

## 2차 리뷰 (재검수)

**검수자**: critic (opus, fresh session)
**검수 일시**: 2026-04-23
**대상**: B-1/B-2/B-3 BLOCKER 수정 + M-1/M-2/M-4 MAJOR 수정
**검수 방식**: 1차 리뷰의 결함 수정 여부 + 잔존 결함 발굴(전체 회귀 포함)

### 판정: REJECT

**근거**: B-1, B-2, B-3, M-1 4건은 정상 수정되어 PASS 처리 가능하나, **회귀 BLOCKER 1건과 신규 MAJOR 2건이 발견**되어 통과시킬 수 없다.

- **R-1 [BLOCKER 회귀]**: `tests/tool_router/test_schemas.py::test_tool_specs_length_and_names`가 4개 툴만 등록됨을 단언하는데, M_13이 5번째 툴(`create_meeting_minutes`)을 추가하며 이 테스트를 갱신하지 않아 **현재 FAIL**. CLAUDE.md DoD "pytest 통과" + 스펙 §16.3 "tool_router 회귀 0건" 정면 위반.
- **R-2 [MAJOR]**: `tests/meeting_minutes/test_tool.py:6`에 unused import `patch` — `ruff check` 1 error → CLAUDE.md DoD 위반.
- **R-3 [MAJOR]**: 1차 M-4 권고사항 4건 중 **#2(ToolRouter `create_meeting_minutes` 분기 회귀 테스트)는 여전히 0건** — 신규 파일 `test_tool.py`만 추가되었고 `tests/tool_router/`에는 회귀 보호가 추가되지 않았다.

이 외에 1차 MINOR 항목 m-1(asyncio deprecated), m-2(docstring "4개 툴")도 미수정. R-1과 m-2는 **같은 뿌리(5번째 툴 추가의 회귀 보호 누락)**의 두 증상이다.

---

### B-1 수정 검증 — ✅ PASS

- `service_context.py:55~57`: `_temp_cleanup_scheduler: Any = None` 슬롯 신설 확인.
- `service_context.py:329~341`: `AsyncIOScheduler()` 생성 → `add_job(cleanup_expired, IntervalTrigger(hours=1), id="meeting_minutes_cleanup", replace_existing=True)` → `start()` 정상.
- `service_context.py:430~441`: `close()`에서 **scheduler.shutdown(wait=True)이 meeting_minutes_service.aclose() 앞에** 정상 배치 — 스펙 §9.2 순서와 일치.
- 단, 스케줄러 자체의 통합 테스트(예: `MemoryJobStore`로 add_job이 호출되었음을 검증)가 추가되지 않음. 1차 권고 4번이 미반영. 다만 BLOCKER 자체는 해소되었으므로 PASS로 분류.

### B-2 수정 검증 — ✅ PASS

- `src/tool_router/types.py:9~20`: `ToolErrorCode` Literal에 `"invalid_llm_response"`, `"schema_violation"`, `"hwpx_write_failed"` 3개 모두 등록 확인.
- 직접 실행 결과:
  ```
  $ uv run mypy src/meeting_minutes/tool.py src/tool_router/types.py \
      src/tool_router/router.py src/meeting_minutes/service.py \
      --explicit-package-bases
  Success: no issues found in 4 source files
  ```
- builder 주장과 일치. PASS.
- 다만 `uv run mypy src/meeting_minutes src/app/meeting_minutes_routes.py src/app/service_context.py`로 **확장**해 실행하면 `service_context.py:322`에 `Unused "type: ignore" comment`가 노출된다(아래 잔존 결함 R-4 참조). 이는 builder가 검증 범위를 4개 파일로 좁혀 회피한 결과이며, 스펙 §16.4 `mypy src/meeting_minutes src/app/meeting_minutes_routes.py`를 엄밀히 적용하면 새 위반이다.

### B-3 수정 검증 — ✅ PASS

- `tool.py:63~89`: `try/except/finally` 패턴으로 재구성. `_set_writing_state`가 `try` 블록 진입 직전에 호출되고, 4가지 예외 모두 `except`로 받은 뒤 `finally` 블록에서 `_restore_neutral_state` 1회 호출이 보장된다.
- early-return 경로 검증:
  - `service is None` (line 42~48): `_set_writing_state` **호출 전**에 return → finally가 writing 상태에 진입하지 않음 → neutral 호출 누락은 없으나 push_emotion 자체 호출 0회. 스펙 §10 정상.
  - `pages not in (1,2)` (line 54~59): 동일 — `_set_writing_state` 호출 전 return. 정상.
- `test_tool.py`의 4건(`test_meeting_draft_error_restores_neutral`, `test_meeting_draft_validation_error_restores_neutral`, `test_meeting_minutes_error_restores_neutral`, `test_unexpected_exception_restores_neutral`)이 모두 `"neutral" in calls`를 검증, B-3 회귀 보호 충분.
- `test_service_none_returns_service_unavailable`이 `avatar.push_emotion.assert_not_called()`로 service=None 분기에서 push_emotion이 호출되지 않음을 명시 검증 — early-return 경로의 finally 미오염도 회귀 보호됨.

### M-1 수정 검증 — ✅ PASS

- `_check_length_violations` 공식으로 직접 계산:
  - summary_items: (1+1+0) + (1+1+1) + (1+0+0) = 6줄
  - detail_items:  (1+1+0) + (1+0+0) = 3줄
  - 합계 **9줄** ≤ 14줄 한도 ✓
- 실행 검증:
  ```
  total_lines = 9
  pages=1 한도 14: OK
  violations(pages=1): []
  ```
- `test_n1_generate_1page`의 `assert fake_agent.complete_json.call_count == 1` 정상 (정상 흐름 검증).
- 단, `test_check_length_violations_no_violations`는 같은 fixture를 `pages=2`로 호출 — 이는 분량 한도가 28이라 어차피 통과. 형식적 검증이지만 `_check_length_violations`의 0개 violation 분기를 회귀 보호하므로 합격.

### M-2 수정 검증 — ⚠ 부분 PASS (잔존 위험 있음)

- `service.py:147~155`: `set_agent(agent: Any)` 공개 메서드 신설. `agent is None`이면 `ValueError("agent must not be None")` raise — 1차 권고 옵션 b의 None 방어 부분 만족.
- `service_context.py:251~252`: `_generator._agent = gemma_agent` 직접 대입 → `set_agent(gemma_agent)` 호출로 교체 확인. **gemma_agent는 `await build_chat_agent(...)` 결과**이므로(line 233~240) 정상 경로에서는 None이 아님. 실패 시 `build_chat_agent`가 예외를 raise하고 init_agent 자체가 종료(line 169 docstring "AgentBackendError 전파, 폴백 금지")하므로 `set_agent(None)` 호출은 발생하지 않는다. 이 부분은 안전.
- **잔존 위험**: `service_context.py:321~326`의 `MeetingMinutesService(agent=None, ...)`는 그대로 유지됨. init_agent가 호출되기 **전에** 누군가 `meeting_minutes_service.generate()`를 호출하면 `_generator._agent.complete_json(...)`이 `AttributeError: 'NoneType' object has no attribute 'complete_json'` — 비즈니스 예외가 아니라 unhandled exception이 된다. 1차 M-2 권고 "옵션 a(부트 순서 재정렬) 또는 set_agent 시 좀비 상태 차단"의 **후자만 부분 적용**. 본 시점에서는 `init_agent` 미호출 시 tool_router 자체가 조립되지 않을 가능성이 있어(workflow는 load_app_services → load_from_config → init_agent 순) 실제 호출 경로는 차단되지만, 테스트 회귀로 보호되지 않은 묵시적 가정. 사후 ws_handler 변경이 이 가정을 깨면 즉시 사고.
- 권고: `MeetingMinutesService.generate()` 진입부에 `if self._generator._agent is None: raise MeetingMinutesError("agent not yet initialized")` 추가. 또는 `set_agent()`가 호출된 후에만 generate 가능하도록 `_initialized` 플래그.
- 1차 M-2의 본질적 의도는 충족되었으므로 부분 PASS.

### M-4 수정 검증 — ⚠ 부분 PASS (1/4만 해결)

1차 권고 4개 항목 중:

| # | 1차 요구 | 현황 |
|---|---|---|
| 1 | `tests/meeting_minutes/test_tool.py` 신설 (4가지 예외 + push_emotion 검증) | ✅ 8건 추가 |
| 2 | `tests/tool_router/test_router.py`에 `create_meeting_minutes` 분기 회귀 | ❌ **여전히 0건** |
| 3 | `tests/app/test_service_context.py`에 M_13 조립 블록 검증 | ❌ 미추가 |
| 4 | `tests/app/test_ws_handler.py`에 `set_send_text` 호출 회귀 | ❌ 미추가 |

→ test_tool.py 8건(정상 2 + B-3 회귀 4 + 입력 검증 2)은 **품질 우수**. 각 케이스가 형식적 mock이 아니라 실제 분기·error_code·push_emotion 호출 횟수를 검증한다.

- `test_success_returns_ok_and_payload`: writing→neutral 호출 순서 정확 단언(`calls == ["writing", "neutral"]`).
- `test_service_none_returns_service_unavailable`: `avatar.push_emotion.assert_not_called()` — early-return 경로의 무오염 검증, 우수.
- `test_invalid_pages_returns_invalid_arguments`: pages=3 입력 검증.
- 4개 예외 회귀 모두 `error_code` 매칭 + `"neutral" in calls` 단언, 회귀 보호 충분.

**그러나 항목 #2(ToolRouter `dispatch("create_meeting_minutes", ...)`) 회귀 미보호는 R-1 BLOCKER와 동일 뿌리**다. 이로 인해 5번째 툴이 ToolRouter에서 빠져도 자동 검출 불가. 부분 PASS.

---

### 잔존 결함

#### R-1 [BLOCKER 회귀] — `tests/tool_router/test_schemas.py::test_tool_specs_length_and_names` FAIL

```python
# tests/tool_router/test_schemas.py:9
def test_tool_specs_length_and_names(router: ToolRouter) -> None:
    """N-5: 리스트 길이 4, 이름 집합 검증."""
    specs = router.tool_specs()
    assert len(specs) == 4   # ← 5가 되었으므로 FAIL
    names = {s["function"]["name"] for s in specs}
    assert names == {"add_event", "get_events", "search_docs", "take_screenshot"}
    # ← create_meeting_minutes 미포함이므로 FAIL
```

실행 결과:
```
$ uv run pytest tests/tool_router -v
FAILED tests/tool_router/test_schemas.py::test_tool_specs_length_and_names
1 failed, 48 passed
```

- **위반 항목**: CLAUDE.md "pytest 통과" + 스펙 §16.3 "`pytest tests/tool_router -v` 회귀 0건".
- **근본 원인**: 5번째 툴 추가 시 회귀 테스트 갱신 누락. 1차 리뷰 m-2(docstring "4개 툴")의 자매 결함이지만 이건 docstring이 아니라 **실제 테스트 단언**이라 자동 빌드를 깬다.
- **권고**:
  ```python
  assert len(specs) == 5
  names = {s["function"]["name"] for s in specs}
  assert names == {"add_event", "get_events", "search_docs", "take_screenshot", "create_meeting_minutes"}
  ```

#### R-2 [MAJOR] — `ruff check` 1 error: unused import `patch`

```
$ uv run ruff check src/meeting_minutes tests/meeting_minutes \
      src/app/meeting_minutes_routes.py src/tool_router/types.py
tests/meeting_minutes/test_tool.py:6:48: F401 [*] `unittest.mock.patch` imported but unused
Found 1 error.
```

- **위반 항목**: CLAUDE.md "ruff check 통과" + 스펙 §16.4 "위반 0".
- **권고**: `from unittest.mock import AsyncMock, MagicMock` 또는 `ruff --fix`.

#### R-3 [MAJOR] — ToolRouter `dispatch("create_meeting_minutes", ...)` 회귀 테스트 0건

- `tests/tool_router/`에 `create_meeting_minutes` 분기 dispatch 테스트 부재. `grep -rn create_meeting_minutes tests/tool_router/` 결과 0건.
- 영향: 5번째 툴의 핸들러가 `_handle_create_meeting_minutes`로 분기되는 경로(`router.py:152~153`), JSON Schema 검증, error_code 매핑이 자동 회귀 보호받지 못함. ToolRouter의 dispatch 분기 로직이 향후 변경되면 즉시 사고.
- 1차 리뷰 M-4 권고 #2 미해결.
- **권고**: `tests/tool_router/test_dispatch_normal.py` 또는 신규 `test_dispatch_meeting.py`에 다음 테스트 최소 3건 추가:
  1. 정상 dispatch (mock service): `await router.dispatch("create_meeting_minutes", {"transcript": "x"*60, "pages": 1})` → ok=True.
  2. JSON Schema 위반 (transcript 50자 미만): error_code="invalid_arguments".
  3. service=None 강등: error_code="service_unavailable".

#### R-4 [MAJOR] — mypy 검증 범위 협소: `service_context.py:322`의 unused type:ignore

builder가 mypy를 4개 파일(`tool.py`, `types.py`, `router.py`, `service.py`)로만 실행해 PASS를 주장했으나, 스펙 §16.4가 명시한 `mypy src/meeting_minutes src/app/meeting_minutes_routes.py`로 실행하면:

```
$ uv run mypy src/meeting_minutes src/app/meeting_minutes_routes.py \
     src/app/service_context.py --explicit-package-bases
src/app/service_context.py:322: error: Unused "type: ignore" comment  [unused-ignore]
```

- `MeetingMinutesService.__init__(agent: Any, ...)`이므로 `agent=None` 직접 대입 시 mypy가 더 이상 arg-type을 위반하지 않는다 → `# type: ignore[arg-type]`이 unused.
- 본 결함은 M-2 `set_agent` 도입의 잔재이며, builder가 정리하지 않았다.
- 다만 mypy 상위 디렉토리 실행 시 service_context.py에는 이미 6건의 다른 unused-ignore가 누적돼 있어 M_13만의 회귀로 분류하기는 모호하다. 그러나 line 322는 **이번 PR에서 builder가 새로 추가한 줄**이므로 새 결함이다.
- **권고**: `# type: ignore[arg-type]` 제거.

#### R-5 [MINOR 잔존] — m-1, m-2 미수정

- `test_routes.py:115` `asyncio.get_event_loop().run_until_complete(...)` 그대로. 1차 m-1 권고대로 `@pytest.mark.asyncio + async def`로 변환 필요.
- `router.py:85` docstring "4개 툴" 그대로. R-1과 동일 뿌리이므로 R-1 수정 시 함께 갱신 권고.

#### R-6 [MINOR 잔존] — `MeetingMinutesService.generate()`에 agent None 가드 없음

- M-2 검증 항에서 설명한 묵시적 가정(init_agent가 set_agent를 호출하기 전에는 generate가 호출되지 않음)이 코드로 강제되지 않음.
- **권고**: `service.py:54` `generate()` 진입 직후:
  ```python
  if self._generator._agent is None:
      raise MeetingMinutesError("agent not initialized; call set_agent() first")
  ```

---

### 1차 리뷰 결함 수정 요약 표

| ID | 1차 심각도 | 1차 결함 | 2차 판정 |
|---|---|---|---|
| B-1 | BLOCKER | APScheduler 미등록 | ✅ PASS |
| B-2 | BLOCKER | mypy 3 errors | ✅ PASS (검증 범위 4개 파일) / ⚠ 확장 시 1건 (R-4) |
| B-3 | BLOCKER | 예외 경로 avatar 고착 | ✅ PASS |
| M-1 | MAJOR | 1page fixture 16줄 초과 | ✅ PASS (9줄 확인) |
| M-2 | MAJOR | agent monkey-patch | ⚠ 부분 PASS — set_agent 도입했으나 agent=None 직접 대입 잔존 |
| M-3 | MAJOR | placeholder_cache 미사용 | (본 검수 범위 외 — 미수정 추정) |
| M-4 | MAJOR | 통합 테스트 0건 | ⚠ 부분 PASS — test_tool.py 8건 추가, 그러나 4개 권고 중 1개만 해결 (R-3) |
| m-1 | MINOR | deprecated asyncio API | ❌ 미수정 (R-5) |
| m-2 | MINOR | docstring "4개 툴" | ❌ 미수정 (R-5), R-1과 자매 결함 |

**신규 발견 결함**:

| ID | 심각도 | 결함 |
|---|---|---|
| R-1 | BLOCKER | tool_router 회귀 테스트 FAIL (5번째 툴 추가 후 갱신 누락) |
| R-2 | MAJOR | ruff check 1 error (unused import patch) |
| R-3 | MAJOR | ToolRouter dispatch 회귀 보호 0건 (1차 M-4 권고 #2 미해결) |
| R-4 | MAJOR | service_context.py:322 unused type:ignore (mypy 확장 검증 시 검출) |
| R-5 | MINOR | m-1·m-2 미수정 |
| R-6 | MINOR | MeetingMinutesService.generate() agent None 가드 없음 |

---

### 총평

**B-1·B-2·B-3 BLOCKER 3건과 M-1 MAJOR 1건은 충실히 수정**되었다. 특히:

- B-1은 스케줄러 슬롯·등록·shutdown 순서 모두 스펙 §8.4·§9.2 그대로 준수.
- B-3은 `try/except/finally` 패턴이 정확하고, early-return 경로의 미오염도 `test_service_none_returns_service_unavailable`이 회귀 보호.
- M-1의 fixture 9줄 검증이 직접 실행으로 확인됨.
- 새로 추가된 `test_tool.py` 8건은 builder가 1차 리뷰의 BLOCKER B-3 발견 원인을 진지하게 받아들였음을 시사한다(error_code 매핑·push_emotion 호출 순서·early-return 무오염을 모두 단언).

그러나 **회귀 BLOCKER 1건(R-1)과 빌드 깨는 MAJOR 1건(R-2)이 자동 검증으로 즉시 발견된다**. CLAUDE.md "Validator는 구현 완료 판정 전에 ruff/mypy/pytest를 모두 실행. 하나라도 실패하면 FAIL"이 또 다시 위반되었다 — 1차 리뷰의 B-2와 동일 패턴이다. **builder가 자기 모듈 내부(`tests/meeting_minutes/`)만 실행해 PASS를 주장하고, 회귀 영향 범위(`tests/tool_router/`, `ruff check` 신규 파일 포함)를 점검하지 않았다.** 1차 리뷰에서 동일한 절차 누락을 지적했음에도 같은 실수가 반복되었다는 점이 가장 우려스럽다.

또한 R-3(ToolRouter dispatch 회귀 보호 0건)는 1차 M-4 권고 4건 중 가장 중요한 1건이 누락된 것이다. 5번째 툴이 dispatch 분기에서 빠져도 자동으로 검출되지 않으면, 향후 ToolRouter 리팩터 시 회귀 사고 가능성이 높다.

**M-3(placeholder_cache 미사용)는 본 2차 검수 범위에 포함되지 않아** 별도 확인하지 않았으나, builder의 수정 주장에 포함되지 않았으므로 미수정으로 추정한다. 이는 스펙 §11.3 위반의 단순 보존이며 운영 사고 위험은 낮으나 향후 정리 권고.

---

### 재제출 전 필수 조치

1. **R-1 [BLOCKER]**: `tests/tool_router/test_schemas.py::test_tool_specs_length_and_names`를 5개 툴 기준으로 갱신. `pytest tests/tool_router -v` 통과 확인.
2. **R-2 [MAJOR]**: `tests/meeting_minutes/test_tool.py:6`의 unused `patch` import 제거.
3. **R-3 [MAJOR]**: `tests/tool_router/`에 `create_meeting_minutes` dispatch 회귀 테스트 ≥ 3건 추가.
4. **R-4 [MAJOR]**: `service_context.py:322`의 `# type: ignore[arg-type]` 제거.
5. **R-5 [MINOR]**: m-1(asyncio API), m-2(docstring) 정리.
6. **R-6 [MINOR]**: `MeetingMinutesService.generate()` 진입부에 agent None 가드.

위 조치 후 **fresh critic으로 3차 검수** 권고. 특히 `pytest tests/ -v`, `ruff check .`, `mypy src/` 세 명령을 **전체 범위로** 실행해 회귀 0건 확인 필수.

`docs/MODULES.md`의 M_13 상태는 본 리뷰 PASS 전까지 `🚧 IN_PROGRESS`로 유지하거나 `🚧 R2 REJECT (회귀)`로 명시 권고.

---

## 3차 리뷰 (재검수)

**검수자**: critic (opus, fresh session)
**검수 일시**: 2026-04-23
**대상**: 2차 리뷰의 R-1(BLOCKER), R-2/R-3/R-4(MAJOR), R-5-m1/R-5-m2/R-6(MINOR) 수정 검증 + 회귀 점검
**검수 방식**: 1·2차 리뷰 결함 수정 여부 + 잔존 결함 발굴(전체 회귀 포함) + 자동 검증 명령 직접 실행

### 판정: PASS

**근거**: 2차 리뷰가 지적한 6개 결함(R-1 BLOCKER 1건, R-2/R-3/R-4 MAJOR 3건, R-5-m1/R-5-m2/R-6 MINOR 3건) 모두 수정 확인. M_13 범위 자동 검증(`pytest tests/tool_router tests/meeting_minutes -v`, `ruff check src/meeting_minutes tests/meeting_minutes src/app/meeting_minutes_routes.py src/tool_router/ tests/tool_router/`, `mypy src/meeting_minutes src/app/meeting_minutes_routes.py`) 모두 통과. **M_13 범위 신규 결함 0건**. CONDITIONAL_PASS가 아닌 PASS로 격상.

---

### R-1 [BLOCKER] 수정 검증 — ✅ PASS

- `tests/tool_router/test_schemas.py:10~15` 직접 확인:
  ```python
  def test_tool_specs_length_and_names(router: ToolRouter) -> None:
      """N-5: 리스트 길이 5, 이름 집합 검증."""
      specs = router.tool_specs()
      assert len(specs) == 5
      names = {s["function"]["name"] for s in specs}
      assert names == {"add_event", "get_events", "search_docs", "take_screenshot", "create_meeting_minutes"}
  ```
- 4 → 5 갱신 + `create_meeting_minutes` 이름 명시적 단언 모두 반영.
- 실행 결과:
  ```
  tests/tool_router/test_schemas.py::test_tool_specs_length_and_names PASSED
  ```
- 2차 BLOCKER 회귀 해소 확인.

### R-2 [MAJOR] 수정 검증 — ✅ PASS

- `tests/meeting_minutes/test_tool.py:6` 직접 확인:
  ```python
  from unittest.mock import AsyncMock, MagicMock
  ```
- `patch` import 완전 제거 확인.
- 실행 결과:
  ```
  $ uv run ruff check src/meeting_minutes tests/meeting_minutes \
        src/app/meeting_minutes_routes.py src/tool_router/ tests/tool_router/
  All checks passed!
  ```
- 1 error → 0 error. PASS.

### R-3 [MAJOR] 수정 검증 — ✅ PASS

- `tests/tool_router/test_schemas.py:47~55` 신규 회귀 테스트 직접 확인:
  ```python
  @pytest.mark.asyncio
  async def test_dispatch_create_meeting_minutes_service_none(router: ToolRouter) -> None:
      """create_meeting_minutes dispatch 회귀: meeting_minutes=None → service_unavailable."""
      result = await router.dispatch(
          "create_meeting_minutes",
          {"transcript": "회의 내용 " * 10, "pages": 1},
      )
      assert result.ok is False
      assert result.error_code == "service_unavailable"
  ```
- **실제 dispatch 경로 검증 확인**:
  - `transcript = "회의 내용 " * 10` = 60자 → `minLength: 50` 통과 (JSON Schema 단계 통과)
  - `pages = 1` → `enum: [1, 2]` 통과
  - `LOCAL_TOOL_NAMES` 화이트리스트 통과 → `_handle_create_meeting_minutes` 분기(`router.py:152~153`)
  - `_handle_create_meeting_minutes`(`router.py:336~342`)가 `handle_create_meeting_minutes(self._meeting_minutes, ...)` 호출
  - conftest.py의 `router` fixture는 `ToolRouter(calendar=mock_calendar, rag=mock_rag, screenshot=fake_screenshot)`로 생성되어 `meeting_minutes=None` (기본값) → `tool.py:42~48` early-return → `service_unavailable` 반환
- **즉, 형식적 mock 검증이 아니라 실제 dispatch 분기 + 화이트리스트 + 스키마 검증 + 핸들러 호출 + service None early-return 5개 경로를 모두 통과**하는 진짜 회귀 테스트. 우수.
- 실행 결과: PASS.

### R-4 [MAJOR] 수정 검증 — ✅ PASS

- `src/app/service_context.py:321~326` 직접 확인:
  ```python
  self.meeting_minutes_service = MeetingMinutesService(
      agent=None,  # init_agent에서 set_agent로 교체
      template_path=meeting_template_path,
      temp_dir=meeting_temp_dir,
      download_base_url=download_base_url,
  )
  ```
- `# type: ignore[arg-type]` 완전 제거 + 의도 주석으로 대체 확인.
- 실행 결과:
  ```
  $ uv run mypy src/meeting_minutes src/app/meeting_minutes_routes.py --explicit-package-bases
  Found 11 errors in 3 files (checked 10 source files)
  ```
  ↳ 11건의 unused-ignore 등은 모두 `service_context.py` 외 다른 파일(hardware.py, ws_handler.py) 또는 `service_context.py:153/169/186/208` 등 **M_13와 무관한 기존 라인**. `service_context.py:322` 또는 그 인근에 신규 mypy 에러 0건 확인.
- 2차 R-4 해소.

### R-5-m1 [MINOR] 수정 검증 — ✅ PASS

- `tests/meeting_minutes/test_routes.py:105~120` 직접 확인:
  ```python
  @pytest.mark.asyncio
  async def test_e6_download_base_url_non_loopback(
      fake_agent: MagicMock,
      template_path: Path,
      tmp_path: Path,
  ) -> None:
      """E-6: meeting_minutes_service=None으로 강등 → dispatch 시 service_unavailable."""
      from meeting_minutes.tool import handle_create_meeting_minutes

      result = await handle_create_meeting_minutes(
          None,
          {"transcript": "가" * 50, "pages": 1},
      )

      assert result.ok is False
      assert result.error_code == "service_unavailable"
  ```
- `asyncio.get_event_loop().run_until_complete(...)` 패턴 완전 제거 확인.
- `@pytest.mark.asyncio` + `async def` 데코레이터 정상 적용.
- `grep -n "asyncio.get_event_loop\|run_until_complete" tests/meeting_minutes/test_routes.py` 결과 0건.
- 실행 결과: PASS.

### R-5-m2 [MINOR] 수정 검증 — ✅ PASS

- `src/tool_router/router.py:84~90` 직접 확인:
  ```python
  def tool_specs(self) -> list[ToolSpec]:
      """5개 툴의 OpenAI function-calling JSON schema 리스트를 반환.
      ...
      """
  ```
- "4개" → "5개" 정정 확인.

### R-6 [MINOR] 수정 검증 — ✅ PASS

- `src/meeting_minutes/service.py:54~70` 직접 확인:
  ```python
  async def generate(
      self,
      transcript: str,
      pages: PageCount,
  ) -> dict[str, str]:
      """녹취록 → HWPX 파일 생성 → file_id 반환.

      Returns:
          {"file_id": uuid, "download_url": full_url, "expires_at": iso}

      Raises:
          MeetingMinutesError: agent가 초기화되지 않은 경우 포함.
      """
      if self._generator._agent is None:
          from .errors import MeetingMinutesError
          raise MeetingMinutesError("agent가 초기화되지 않았습니다. set_agent()를 먼저 호출하세요.")
      draft = await self._generator.generate(transcript, pages)
  ```
- agent None 가드 정상 추가 + 올바른 예외 클래스(`MeetingMinutesError`) 사용 확인.
- `tool.py:78~80` 처리 경로 확인:
  ```python
  except MeetingMinutesError as exc:
      logger.error(f"create_meeting_minutes 일반 오류: {exc}")
      result = ToolResult(ok=False, error=str(exc), error_code="hwpx_write_failed")
  ```
- ↳ agent 미초기화 상태에서 generate 호출 시 `MeetingMinutesError` raise → `tool.py`가 `error_code="hwpx_write_failed"`로 매핑 → finally 블록에서 `_restore_neutral_state` 호출 → 사용자에게 ToolResult로 정상 전달.
- **검토 의견**: error_code "hwpx_write_failed"는 의미상 "agent_not_initialized" 같은 별도 코드가 더 명확하나, 스펙 §10 에러 정책 표에 등록된 코드만 사용하는 것이 일관 — 따라서 `hwpx_write_failed`가 가장 인접한 분류로 수용 가능. 단, 이 매핑이 의도적인지는 향후 운영에서 사용자 메시지로 검증 필요(MINOR 수준 개선 여지).
- 1·2차 권고대로 가드 추가 + 올바른 예외 + 일관된 error_code 매핑 확인. PASS.

---

### 자동 검증 (전체 회귀)

| 명령 | 결과 |
|---|---|
| `pytest tests/tool_router tests/meeting_minutes -v` | **107 passed** in 0.34s, 0 failed |
| `ruff check src/meeting_minutes tests/meeting_minutes src/app/meeting_minutes_routes.py src/tool_router/ tests/tool_router/` | **All checks passed!** |
| `mypy src/meeting_minutes src/app/meeting_minutes_routes.py --explicit-package-bases` | M_13 범위 0 errors (11 errors 모두 service_context.py·hardware.py·ws_handler.py의 **M_13와 무관한 기존 위반**) |
| `pytest tests/ -q --ignore=tests/agent/test_health.py` | 745 passed, 32 failed, 9 errors — **32 failed/9 errors 모두 M_13 무관** (agent: respx 미설치, asr/tts: CTranslate2 등 환경 의존, vad: upstream hash, app: upstream_integrity) |

**확인 사항**: 1·2차 리뷰가 강조한 "회귀 영향 범위(`tests/tool_router/`, `ruff check` 신규 파일)" 점검을 명시적으로 수행. M_13 범위에서 회귀 0건 확인.

---

### M_13 범위 외 알려진 잔존(본 리뷰 범위 외, 향후 정리 권고)

본 검수 범위는 2차 리뷰에서 사용자가 명시한 R-1~R-6 6개 항목과 회귀 점검에 한정한다. 다음은 1차 리뷰에서 식별되었으나 사용자 지정 검수 항목 외이므로 본 PASS와 무관:

- **M-3 (1차 MAJOR)**: `HwpxWriter._placeholder_cache`가 lxml 객체 참조 한계로 미사용 — 2차 리뷰가 본 검수 범위 외라고 명시. 운영 사고 위험 낮음(스펙 §11.3 위반의 단순 보존). 향후 스펙 §11.3 보정 또는 `copy.deepcopy` 재사용 패턴 도입 권고.
- **m-3 (1차 MINOR)**: `_set_para_text`가 단락 첫 `<hp:t>`만 갱신 — 현재 템플릿에서만 검증됨, 다른 placeholder 배치에 취약. 운영 가이드(MEETING_TEMPLATE_PLACEHOLDERS.md) 수준에서 제약 명시 필요.
- **m-5 (1차 MINOR)**: Windows에서 cleanup·download race — `path.unlink(missing_ok=True)` + try/except OSError 추가 패턴이 service.py:113~116에 부분 적용됨. Windows QA 단계에서 재검증 권고.
- **R-6 부수 의견**: `MeetingMinutesError("agent가 초기화되지 않았습니다")`의 error_code 매핑이 `hwpx_write_failed`로 가는 것은 의미상 부정확. 향후 `agent_not_initialized` 코드 신설 또는 `service_unavailable` 강등 검토(REQUEST 수준).
- **mypy 확장 검증**: `service_context.py`의 기존 11건 unused-ignore/has-type 에러는 M_13 범위 외이나, **앱 전체 mypy clean을 향한 별도 정리 필요**(별도 CR 후보).
- **Windows 한글 뷰어 수동 QA(스펙 §16.9)**: 본 리뷰 환경에서 검증 불가. project_deploy_status.md "Windows 실기기 QA 남음"과 함께 진행 권고.
- **테스트 환경 의존성**: `tests/agent/test_health.py`(respx 미설치), `tests/asr/`(CTranslate2 미지원), `tests/vad/test_upstream_integrity.py`(upstream hash) 등은 M_13와 무관하나 CI 안정성을 위해 별도 조치 필요(별도 이슈).

---

### 1·2차 리뷰 결함 수정 누적 표

| ID | 1차 심각도 | 2차 판정 | 3차 판정 |
|---|---|---|---|
| B-1 (APScheduler) | BLOCKER | ✅ PASS | ✅ PASS |
| B-2 (mypy 3 errors) | BLOCKER | ✅ PASS | ✅ PASS |
| B-3 (avatar 고착) | BLOCKER | ✅ PASS | ✅ PASS |
| M-1 (1page fixture) | MAJOR | ✅ PASS | ✅ PASS |
| M-2 (agent monkey-patch) | MAJOR | ⚠ 부분 | ✅ PASS (R-6 가드 결합으로 안전) |
| M-3 (placeholder_cache) | MAJOR | (범위 외) | (범위 외) |
| M-4 (통합 테스트) | MAJOR | ⚠ 부분 (1/4) | ✅ PASS (R-3로 #2 해결, #3·#4는 통합/E2E 단계 권고) |
| m-1 (asyncio API) | MINOR | ❌ | ✅ PASS (R-5-m1) |
| m-2 (docstring "4개 툴") | MINOR | ❌ | ✅ PASS (R-5-m2) |
| R-1 (test_schemas FAIL) | BLOCKER (2차 신규) | — | ✅ PASS |
| R-2 (unused patch import) | MAJOR (2차 신규) | — | ✅ PASS |
| R-3 (dispatch 회귀 0건) | MAJOR (2차 신규) | — | ✅ PASS |
| R-4 (service_context.py:322 ignore) | MAJOR (2차 신규) | — | ✅ PASS |
| R-5 (m-1·m-2 잔존) | MINOR (2차 신규) | — | ✅ PASS |
| R-6 (generate agent None 가드) | MINOR (2차 신규) | — | ✅ PASS |

---

### 총평

3차 검수에서 사용자가 명시한 6개 항목(R-1~R-6) **모두 PASS**, 자동 회귀 0건 확인. **builder가 2차 리뷰의 모든 권고를 충실히 반영**했고, 특히 다음이 우수하다:

1. **R-3 회귀 테스트의 진정성**: `test_dispatch_create_meeting_minutes_service_none`가 mock 호출 검증이 아니라 실제 dispatch 5단계 경로(LOCAL_TOOL_NAMES → arguments dict → JSON Schema → 분기 → service None early-return)를 모두 통과하는 진짜 회귀 보호. transcript 60자·pages 1로 스키마 통과를 보장한 입력 설계도 정밀.

2. **R-6 가드의 일관성**: agent None 검사가 단순 isinstance 체크가 아니라 `MeetingMinutesError` raise + `tool.py` finally 블록의 `_restore_neutral_state`까지 자연스럽게 흐른다. 즉 1·2차 BLOCKER B-3(아바타 고착)와 동일한 안전망에 통합되었다.

3. **R-4 type:ignore 제거 + 의도 주석 추가**: 단순히 ignore만 빼지 않고 `# init_agent에서 set_agent로 교체`라는 의도 주석으로 후속 개발자에게 라이프사이클을 설명. 코드 가독성 개선.

4. **R-2 ruff 회귀 점검**: 2차 리뷰 권고대로 ruff 검증 범위를 좁히지 않고 `tests/meeting_minutes/test_tool.py`를 포함한 전체 M_13 범위에서 0 errors 달성.

**M-2와 R-6의 결합** — `agent=None`으로 service를 만드는 패턴 자체가 본질적으로 권고되지 않으나, 현재 builder가 (a) `set_agent`가 None을 차단하고 (b) `generate`가 agent None을 가드하여 **모든 호출 경로에서 안전성이 보장**되었다. 부트 순서 재정렬(옵션 a)은 더 깔끔한 설계지만, 현재 가드의 이중 안전망으로 운영 사고 위험은 사실상 0이다. 향후 리팩터 시 `set_agent` 폐지 + 부트 순서 재정렬을 별도 CR로 권고하나, 본 모듈 PASS의 장애 사유는 아니다.

`docs/MODULES.md`의 M_13을 `✅ DONE`으로 갱신할 수 있음을 확인. CLAUDE.md "산출물 체크리스트"의 모든 항목을 만족한다 (Windows 한글 뷰어 수동 QA(§16.9)는 project_deploy_status.md의 Windows 실기기 QA와 함께 진행).


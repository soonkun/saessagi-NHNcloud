# upstream 패치 (patches/)

`upstream/Open-LLM-VTuber/`는 **참조용 외부 의존성**으로, 원칙적으로 직접 수정하지 않는다
(`docs/ARCHITECTURE.md` "참조 전용, 직접 수정 금지"). 기능 확장은 `src/`에서 EXTEND(상속·래핑,
예: `src/agent/upstream_adapter.py`) 또는 NEW로 한다.

그러나 **대화 루프 내부의 모듈 레벨 함수처럼 외부에서 끼어들 후크가 없어 EXTEND로 풀 수 없는**
소수의 수정은 여기 patch 파일로 **관리**한다. 이렇게 하면:

- `scripts/bootstrap.py`가 upstream을 재clone해도 패치가 자동 재적용되어 **조용히 유실되지 않는다**.
- 무결성 테스트(`tests/app/test_upstream_integrity.py`)의 baseline이 "패치된 상태"를 기준으로 하므로,
  **여기서 관리하지 않는 추가 변조**는 여전히 테스트가 잡아낸다.

적용은 `bootstrap.apply_upstream_patches()`가 `git apply`로 수행하며 멱등(이미 적용 시 skip)이다.
upstream은 `UPSTREAM_PINNED_COMMIT`(`19b58b1`)에 고정되어 패치가 깨끗하게 적용된다.

---

## 0001-conversations-tts-robustness.patch

대화 종료 시 TTS 오디오 전송/재생 동기화 안정화. 외부 후크가 없는
`conversations/` 모듈 함수들이라 직접 패치로 관리.

| 파일 | 변경 | 사유 |
|------|------|------|
| `tts_manager.py` | `wait_until_drained()` 메서드 추가 (`_payload_queue.join()`) | TTS payload가 WebSocket으로 **전부 전송 완료**될 때까지 대기하는 수단 제공 |
| `conversation_utils.py` | `finalize_conversation_turn`에서 위 메서드 호출 + `frontend-playback-complete` 대기에 `timeout=30.0` | 프론트 재생완료 신호가 안 오면 **무한 대기(행)** 하던 것을 30초로 제한. 큐 드레인으로 오디오 순서 보장 |
| `single_conversation.py` | 중복 TTS 대기 블록 제거(finalize로 일원화) + 에러 로그에 예외 타입·repr 추가 | 이중 await 제거, 대화 체인 오류 진단성 향상 |

**되돌릴 경우 위험**: `timeout` 제거 시 프론트 무응답 상황에서 대화 턴이 영구 블로킹될 수 있음.
필요성 재검증 없이는 revert 금지.

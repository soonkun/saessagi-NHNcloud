# vendor/ — 벤더링된 외부 코드

## open_llm_vtuber / prompts

[Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) 커밋 `19b58b1`의
`src/open_llm_vtuber`와 `prompts` 패키지를 복사한 것 (CR-17, 2026-07-15).
라이선스: MIT (`LICENSE-Open-LLM-VTuber`).

기존 `patches/0001-conversations-tts-robustness.patch` 3건이 **이미 반영된 상태**다:

| 파일 | 변경 | 되돌릴 경우 위험 |
|------|------|------------------|
| `conversations/tts_manager.py` | `wait_until_drained()` 추가 | TTS 오디오 전송 완료 보장 소실 |
| `conversations/conversation_utils.py` | finalize에서 드레인 대기 + `frontend-playback-complete` 30s 타임아웃 | 프론트 무응답 시 대화 턴 **영구 블로킹** |
| `conversations/single_conversation.py` | 중복 TTS 대기 제거 + 오류 로그 강화 | 이중 await 재발 |

수정 원칙: 기능 확장은 여전히 `src/`에서 EXTEND(상속·래핑)가 우선.
vendor 직접 수정은 위 패치처럼 외부 후크가 없는 경우에 한하며, git이 추적하므로
별도 패치 파일·무결성 baseline은 두지 않는다.

## mecab_shim

한국어 형태소 분석(MeCab) 호환 심 — 기존과 동일.

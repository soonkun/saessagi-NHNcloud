# src/avatar_state/types.py
"""AvatarState 모듈 타입 정의.

공개 심볼:
  Emotion, AvatarEvent, CROSSFADE_MIN_MS, CROSSFADE_MAX_MS, CROSSFADE_DEFAULT_MS

내부 심볼 (밑줄 접두사):
  _VALID_EMOTIONS, _SPOKEN_EMOTIONS
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Emotion Literal — 유효 전체 집합 (note_writing: 업무노트 작성 중 전용, M_15 후속)
# ---------------------------------------------------------------------------

Emotion = Literal[
    "neutral",
    "happy",
    "surprised",
    "sad",
    "worried",
    "thinking",
    "sleepy",
    "study",
    "writing",
    "note_writing",
]

# (1) 유효 Emotion 집합 — push_event/AvatarEvent 경로(9종, study/writing 포함)
_VALID_EMOTIONS: frozenset[str] = frozenset(
    {
        "neutral",
        "happy",
        "surprised",
        "sad",
        "worried",
        "thinking",
        "sleepy",
        "study",
        "writing",
    }
)

# (2) LLM 발화 파싱 집합 — extract_emotion이 유효로 간주할 키(7종, study 제외)
#     study는 의도적으로 제외. LLM 발화 중 study가 등장하면 미지 키 취급(D-6).
_SPOKEN_EMOTIONS: frozenset[str] = frozenset(
    {
        "neutral",
        "happy",
        "surprised",
        "sad",
        "worried",
        "thinking",
        "sleepy",
    }
)

# ---------------------------------------------------------------------------
# crossfade 상수
# ---------------------------------------------------------------------------

CROSSFADE_MIN_MS: int = 200
CROSSFADE_MAX_MS: int = 300
CROSSFADE_DEFAULT_MS: int = 250

# ---------------------------------------------------------------------------
# AvatarEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AvatarEvent:
    """아바타 상태 이벤트. push_event의 입력 단위.

    Fields:
        emotion: 8종 Emotion Literal 중 하나(study 포함).
            _VALID_EMOTIONS 집합으로 방어적 검증 수행.
        crossfade_ms: 페이드 전환 시간(ms). 허용 범위 [200, 300].
            범위 밖이면 ValueError (clamp 금지, D-5).
        speaking: 립싱크 opacity 펄스 ON/OFF 토글. 단순 boolean.

    Raises:
        ValueError: emotion이 8종 외 또는 crossfade_ms가 [200,300] 범위 밖.
    """

    emotion: Emotion
    crossfade_ms: int = CROSSFADE_DEFAULT_MS
    speaking: bool = False

    def __post_init__(self) -> None:
        if self.emotion not in _VALID_EMOTIONS:
            _ordered = (
                "neutral",
                "happy",
                "surprised",
                "sad",
                "worried",
                "thinking",
                "sleepy",
                "study",
                "writing",
            )
            raise ValueError(f"emotion must be one of {_ordered}, got {self.emotion!r}")
        if not (CROSSFADE_MIN_MS <= self.crossfade_ms <= CROSSFADE_MAX_MS):
            raise ValueError(
                f"crossfade_ms must be in [{CROSSFADE_MIN_MS},{CROSSFADE_MAX_MS}],"
                f" got {self.crossfade_ms}"
            )

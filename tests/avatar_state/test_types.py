# tests/avatar_state/test_types.py
"""AvatarEvent, Emotion, 집합 불변식 테스트."""

from __future__ import annotations

import pytest

from avatar_state.types import (
    CROSSFADE_DEFAULT_MS,
    CROSSFADE_MAX_MS,
    CROSSFADE_MIN_MS,
    AvatarEvent,
    _SPOKEN_EMOTIONS,
    _VALID_EMOTIONS,
)


class TestEmotionSets:
    """집합 불변식 검증."""

    def test_spoken_is_subset_of_valid(self) -> None:
        """_SPOKEN_EMOTIONS ⊂ _VALID_EMOTIONS."""
        assert _SPOKEN_EMOTIONS < _VALID_EMOTIONS

    def test_valid_minus_spoken_is_study_and_writing(self) -> None:
        """_VALID_EMOTIONS - _SPOKEN_EMOTIONS == {"study", "writing"}."""
        assert _VALID_EMOTIONS - _SPOKEN_EMOTIONS == {"study", "writing"}

    def test_valid_has_9_emotions(self) -> None:
        assert len(_VALID_EMOTIONS) == 9

    def test_spoken_has_7_emotions(self) -> None:
        assert len(_SPOKEN_EMOTIONS) == 7

    def test_study_in_valid_not_in_spoken(self) -> None:
        assert "study" in _VALID_EMOTIONS
        assert "study" not in _SPOKEN_EMOTIONS


class TestAvatarEventNormal:
    """AvatarEvent 정상 생성."""

    def test_default_fields(self) -> None:
        ev = AvatarEvent(emotion="happy")
        assert ev.emotion == "happy"
        assert ev.crossfade_ms == CROSSFADE_DEFAULT_MS
        assert ev.speaking is False

    def test_crossfade_min_boundary(self) -> None:
        """E-6: crossfade_ms=200 성공."""
        ev = AvatarEvent(emotion="happy", crossfade_ms=CROSSFADE_MIN_MS)
        assert ev.crossfade_ms == 200

    def test_crossfade_max_boundary(self) -> None:
        """E-6: crossfade_ms=300 성공."""
        ev = AvatarEvent(emotion="happy", crossfade_ms=CROSSFADE_MAX_MS)
        assert ev.crossfade_ms == 300

    def test_study_emotion_allowed(self) -> None:
        """N-8: study 감정은 AvatarEvent에서 허용됨."""
        ev = AvatarEvent(emotion="study", crossfade_ms=250, speaking=False)
        assert ev.emotion == "study"

    def test_frozen(self) -> None:
        """AvatarEvent는 frozen=True이므로 수정 불가."""
        ev = AvatarEvent(emotion="happy")
        with pytest.raises((AttributeError, TypeError)):
            ev.emotion = "sad"  # type: ignore[misc]


class TestAvatarEventErrors:
    """AvatarEvent 에러 케이스."""

    def test_invalid_emotion_raises_value_error(self) -> None:
        """8종 외 감정 키 → ValueError."""
        with pytest.raises(ValueError, match="emotion must be one of"):
            AvatarEvent(emotion="joy")  # type: ignore[arg-type]

    def test_crossfade_below_min_raises(self) -> None:
        """E-6: crossfade_ms=199 → ValueError."""
        with pytest.raises(ValueError, match="crossfade_ms must be in"):
            AvatarEvent(emotion="happy", crossfade_ms=199)

    def test_crossfade_above_max_raises(self) -> None:
        """E-6: crossfade_ms=301 → ValueError."""
        with pytest.raises(ValueError, match="crossfade_ms must be in"):
            AvatarEvent(emotion="happy", crossfade_ms=301)

    def test_crossfade_way_below_raises(self) -> None:
        with pytest.raises(ValueError):
            AvatarEvent(emotion="neutral", crossfade_ms=150)

    def test_crossfade_way_above_raises(self) -> None:
        with pytest.raises(ValueError):
            AvatarEvent(emotion="neutral", crossfade_ms=350)

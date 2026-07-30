"""CR-47 — 진행 상태 이벤트가 캐릭터 모습을 함께 실어 보내는지.

떠 있는 새싹이의 존재 이유가 "지금 뭘 하고 있는지 한눈에 보이는 것"이므로, 가장 흔한
경로인 일반 대화에서 모습이 바뀌지 않으면 기능이 사실상 없는 것과 같다. 실제로 처음
구현했을 때 진행 문구는 바뀌는데 그림은 계속 neutral이었다.
"""

from __future__ import annotations

import pytest

from agent.upstream_adapter import (
    _PHASE_EMOTION_ANSWER,
    _PHASE_EMOTION_SEARCH,
    _TOOL_STATUS_EMOTION,
    _TOOL_STATUS_TEXT,
    _status_event,
)
from avatar_state.types import _VALID_EMOTIONS


class TestStatusEvent:
    def test_emotion_omitted_when_not_given(self) -> None:
        """예전 클라이언트가 모르는 필드를 받지 않도록 없을 때는 넣지 않는다."""
        assert "emotion" not in _status_event("문서를 찾는 중…")

    def test_emotion_included_when_given(self) -> None:
        ev = _status_event("문서를 찾는 중…", "study")
        assert ev["emotion"] == "study"
        assert ev["content"] == "문서를 찾는 중…"

    def test_channel_shape_unchanged(self) -> None:
        """감정을 추가하면서 기존 중계 규약(tool_call_status/_agent_status)이 깨지면
        진행 문구 자체가 사라진다."""
        ev = _status_event("x", "study")
        assert ev["type"] == "tool_call_status"
        assert ev["tool_name"] == "_agent_status"
        assert ev["status"] == "running"


class TestEmotionTable:
    def test_every_tool_status_text_has_an_emotion(self) -> None:
        """문구만 있고 그림이 없으면 그 단계에서 캐릭터가 멈춘 것처럼 보인다."""
        missing = sorted(set(_TOOL_STATUS_TEXT) - set(_TOOL_STATUS_EMOTION))
        assert missing == [], f"모습이 지정되지 않은 도구: {missing}"

    def test_no_emotion_without_text(self) -> None:
        """반대로 문구 없는 감정만 남으면 표가 어긋난 것이다."""
        extra = sorted(set(_TOOL_STATUS_EMOTION) - set(_TOOL_STATUS_TEXT))
        assert extra == [], f"문구가 없는 항목: {extra}"

    @pytest.mark.parametrize("emotion", sorted(_TOOL_STATUS_EMOTION.values()))
    def test_tool_emotions_are_valid(self, emotion: str) -> None:
        """avatar_state가 인정하는 감정이어야 한다 — 오타면 그림 파일이 없어
        neutral로 폴백되어 조용히 아무 일도 일어나지 않는다."""
        assert emotion in _VALID_EMOTIONS

    @pytest.mark.parametrize("emotion", [_PHASE_EMOTION_SEARCH, _PHASE_EMOTION_ANSWER])
    def test_phase_emotions_are_valid(self, emotion: str) -> None:
        assert emotion in _VALID_EMOTIONS

    def test_search_and_answer_look_different(self) -> None:
        """두 단계가 같은 그림이면 '문서를 찾는 중'과 '답을 쓰는 중'을 구분할 수 없다."""
        assert _PHASE_EMOTION_SEARCH != _PHASE_EMOTION_ANSWER

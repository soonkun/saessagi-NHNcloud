# src/app/history_title.py
"""대화 목록 제목 만들기 (CR-53).

예전에는 upstream `get_history_list`가 돌려주는 **마지막 메시지**(대개 새싹이의 답변)를
그대로 목록에 썼다. 그래서 목록이 이렇게 보였다:

    자료를 찾아볼게요! ## 기후변화 농업…
    자료를 찾아볼게요! ## 기후변화 농업…
    자료를 찾아볼게요! 제공된 문서들을 …

셋 다 안내 멘트로 시작해 **무슨 대화였는지 구분되지 않는다.** 대화를 규정하는 것은
답변이 아니라 사용자가 처음 던진 질문이므로 그것을 제목으로 쓴다.
"""

from __future__ import annotations

import re
from typing import Any

# 우리가 자동으로 붙이는 메타 접두어 — 제목에 나오면 안 된다.
_ATTACHMENT_META = re.compile(r"\[첨부 (?:자료|이미지):[^\]]*\]\s*")
# 감정 태그 `[study]` 등
_EMOTION_TAG = re.compile(r"\[[a-z_]+\]", re.IGNORECASE)
# 인용 마커
_MARKERS = re.compile(r"\[\[(?:doc|note):[^\]]*\]{0,2}")

_MAX_LEN = 60


def clean_title_text(text: str) -> str:
    """제목용으로 군더더기를 걷어낸다."""
    t = _ATTACHMENT_META.sub("", text or "")
    t = _MARKERS.sub("", t)
    t = _EMOTION_TAG.sub("", t)
    t = re.sub(r"[#*`>]+", " ", t)  # 마크다운 기호
    t = re.sub(r"\s+", " ", t).strip()
    return t[:_MAX_LEN]


def history_title(messages: list[dict[str, Any]]) -> str:
    """대화의 제목. **첫 사용자 질문**을 쓴다.

    사용자 발화가 하나도 없으면(첨부만 올린 경우 등) 첫 AI 발화로 물러선다.
    그것도 없으면 빈 문자열 — 호출부가 기본 문구를 정한다.
    """
    for m in messages:
        if m.get("role") == "human":
            title = clean_title_text(str(m.get("content") or ""))
            if title:
                return title
    for m in messages:
        if m.get("role") == "ai":
            title = clean_title_text(str(m.get("content") or ""))
            if title:
                return title
    return ""

# src/rag_watch/activity.py
"""사용자 응답 중인지 알리는 표시등 (CR-54).

RAG 폴더 감시는 배경 작업이다. 급할 것이 없고 실패해도 다음 주기에 다시 하면 된다.
반면 대화·딥 리서치는 사용자가 화면 앞에서 기다리는 작업이다.

그런데 둘은 같은 GPU를 쓴다. 딥 리서치가 128B 모델(80GB)을 올린 채로 임베딩이 계속
돌면 GPU가 고갈되고, 실제로 리랭커가 `cuDNN Frontend error`를 내다가 **백엔드가 통째로
죽었다** (E-87). 죽지 않더라도 배경 작업이 GPU를 가져가면 사용자 응답이 그만큼 느려진다
(의도 분류가 3~6초에서 16초로 늘어난 사례).

그래서 "지금 사용자를 기다리게 하고 있는가"를 한 곳에 두고, 감시자가 그때는 쉬게 한다.
스레드 간 공유되므로 카운터로 관리한다 — 대화가 겹쳐도 마지막 하나가 끝나야 재개된다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_active = 0


@contextmanager
def conversation_active() -> Iterator[None]:
    """이 블록이 도는 동안 "사용자 응답 중"으로 표시한다."""
    global _active
    with _lock:
        _active += 1
    try:
        yield
    finally:
        with _lock:
            _active = max(0, _active - 1)


def is_conversation_active() -> bool:
    with _lock:
        return _active > 0

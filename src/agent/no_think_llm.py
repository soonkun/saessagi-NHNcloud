# src/agent/no_think_llm.py
"""NoThinkLLM — 모든 Ollama API 호출에 think=False를 강제하는 LLM.

gemma4:e2b / e4b 의 extended-thinking이 일상 대화에서도 항상 실행되어
응답이 느린 문제를 해결한다.

upstream chat_completion()을 재구현하지 않고, 내부 OpenAI 클라이언트의
chat.completions.create를 패치해 extra_body={"think": False}를 주입한다.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import AsyncLLM

logger = logging.getLogger(__name__)


class NoThinkLLM(AsyncLLM):
    """모든 Ollama API 호출에 think=False를 강제하는 AsyncLLM 서브클래스."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._patch_client()

    def _patch_client(self) -> None:
        """client.chat.completions.create를 패치해 think=False를 항상 주입."""
        original_create = self.client.chat.completions.create

        @wraps(original_create)
        async def _no_think_create(
            *args: Any,
            extra_body: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            eb: dict[str, Any] = dict(extra_body) if extra_body else {}
            eb.setdefault("think", False)
            logger.debug("NoThinkLLM: injecting extra_body=%s", eb)
            return await original_create(*args, extra_body=eb, **kwargs)

        # instance attribute가 class method를 shadow — 정상적인 Python 패턴
        self.client.chat.completions.create = _no_think_create  # type: ignore[method-assign]
        logger.info("NoThinkLLM: think=False 패치 완료 (model=%s)", self.model)

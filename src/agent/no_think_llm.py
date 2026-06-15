# src/agent/no_think_llm.py
"""NoThinkLLM — 모든 Ollama API 호출에 thinking 비활성화를 강제하는 LLM.

gemma4 류의 extended-thinking이 일상 대화에서도 항상 실행되어 응답이 느리거나,
추론 토큰이 출력 예산을 잠식해 content가 빈 문자열이 되는 문제를 해결한다.
(특히 비전 입력 시 추론이 길어 num_predict를 다 소진하고 content=''가 된다.)

upstream chat_completion()을 재구현하지 않고, 내부 OpenAI 클라이언트의
chat.completions.create를 패치해 thinking 비활성화 파라미터를 주입한다.

주의: Ollama의 OpenAI-호환 엔드포인트(/v1/chat/completions)는 `think` 파라미터를
**무시**한다 (네이티브 /api/chat에서만 동작). /v1에서 추론을 끄는 것은
`reasoning_effort="none"`이므로 이 둘을 함께 주입한다 (E-53).
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
            eb["think"] = False  # 네이티브 /api/chat용 (강제)
            # /v1 OpenAI-호환 엔드포인트는 think를 무시하므로 reasoning_effort로 끈다.
            # 호출자가 명시한 값(예: 정형 문서용 'low')은 존중 (setdefault).
            eb.setdefault("reasoning_effort", "none")
            logger.debug("NoThinkLLM: injecting extra_body=%s", eb)
            return await original_create(*args, extra_body=eb, **kwargs)

        # instance attribute가 class method를 shadow — 정상적인 Python 패턴
        self.client.chat.completions.create = _no_think_create  # type: ignore[method-assign]
        logger.info("NoThinkLLM: think=False 패치 완료 (model=%s)", self.model)

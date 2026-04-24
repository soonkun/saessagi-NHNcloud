# src/agent/builder.py
"""build_chat_agent — AppConfig에서 GemmaChatAgent를 생성하는 빌더."""

import logging
from typing import Any

from open_llm_vtuber.mcpp.tool_executor import ToolExecutor
from open_llm_vtuber.mcpp.tool_manager import ToolManager

from src.app.config import AppConfig, OllamaConfig

from .errors import AgentInitError, AgentBackendError  # noqa: F401
from .gemma_chat_agent import GemmaChatAgent

logger = logging.getLogger(__name__)


async def build_chat_agent(
    app_config: AppConfig,
    ollama_config: OllamaConfig,
    tool_manager: ToolManager | None,
    tool_executor: ToolExecutor | None,
    system_prompt: str,
    extra_tool_specs: list[dict[str, Any]] | None = None,
    tts_preprocessor_config: Any | None = None,
) -> GemmaChatAgent:
    """AppConfig.agent 서브스키마를 읽어 GemmaChatAgent를 생성한다.

    - ollama_config.base_url은 M_01이 enforce_private_url로 이미 검증된 상태여야 함.
    - use_mcpp는 (tool_manager is not None and tool_executor is not None)로 결정.
    - faster_first_response는 app_config.agent.faster_first_response(기본 True).
    - extra_tool_specs: MCP 외 추가 tool 스키마 목록(OpenAI format). 기본 None.

    Raises:
        AgentInitError | AgentBackendError: GemmaChatAgent.create()와 동일.
    """
    agent_cfg = app_config.agent
    use_mcpp = tool_manager is not None and tool_executor is not None

    logger.info(
        f"build_chat_agent: model={ollama_config.model}, "
        f"base_url={ollama_config.base_url}, use_mcpp={use_mcpp}"
    )

    return await GemmaChatAgent.create(
        base_url=ollama_config.base_url,
        model=ollama_config.model,
        system_prompt=system_prompt,
        tool_manager=tool_manager,
        tool_executor=tool_executor,
        temperature=agent_cfg.temperature,
        max_context_tokens=agent_cfg.max_context_tokens,
        faster_first_response=agent_cfg.faster_first_response,
        interrupt_method=agent_cfg.interrupt_method,
        use_mcpp=use_mcpp,
        extra_tool_specs=extra_tool_specs,
        tts_preprocessor_config=tts_preprocessor_config,
    )

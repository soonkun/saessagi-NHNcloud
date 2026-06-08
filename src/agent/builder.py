# src/agent/builder.py
"""build_chat_agent — AppConfig에서 GemmaChatAgent를 생성하는 빌더."""

import logging
from typing import Any

from open_llm_vtuber.mcpp.tool_executor import ToolExecutor
from open_llm_vtuber.mcpp.tool_manager import ToolManager

from src.app.config import AppConfig, LlmProviderKind, OllamaConfig

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

    llm_provider에 따라 Ollama(로컬) 또는 외부 API(OpenAI 등)를 선택한다.
    - Ollama: enforce_private_url 검증 + Ollama 헬스체크 수행.
    - 외부: URL 검증·헬스체크 건너뜀, api_key 전달.
    """
    agent_cfg = app_config.agent
    use_mcpp = tool_manager is not None and tool_executor is not None

    if app_config.llm_provider == LlmProviderKind.OPENAI:
        openai_cfg = app_config.openai
        base_url = "https://api.openai.com/v1"
        model = openai_cfg.model
        api_key = openai_cfg.api_key
        is_external = True
        logger.info(f"build_chat_agent: provider=openai, model={model}, use_mcpp={use_mcpp}")
    else:
        base_url = ollama_config.base_url
        model = ollama_config.model
        api_key = "z"
        is_external = False
        logger.info(
            f"build_chat_agent: provider=ollama, model={model}, base_url={base_url}, use_mcpp={use_mcpp}"
        )

    # 모델별 안전한 temperature — gpt-5/o-series는 1.0만 허용 (OpenAI 400 회피)
    effective_temperature = agent_cfg.temperature
    if app_config.llm_provider == LlmProviderKind.OPENAI:
        m = (model or "").lower()
        if m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
            if effective_temperature != 1.0:
                logger.info(
                    f"build_chat_agent: {model}는 temperature=1.0만 허용 — "
                    f"{effective_temperature} → 1.0 자동 조정"
                )
                effective_temperature = 1.0

    return await GemmaChatAgent.create(
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        tool_manager=tool_manager,
        tool_executor=tool_executor,
        temperature=effective_temperature,
        max_context_tokens=agent_cfg.max_context_tokens,
        faster_first_response=agent_cfg.faster_first_response,
        interrupt_method=agent_cfg.interrupt_method,
        use_mcpp=use_mcpp,
        extra_tool_specs=extra_tool_specs,
        tts_preprocessor_config=tts_preprocessor_config,
        llm_api_key=api_key,
        is_external=is_external,
    )

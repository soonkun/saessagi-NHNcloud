# src/agent_prompts/__init__.py
"""M_17 AgentInstructions — 키 집합, 기본값, 메타 공개 API."""

from __future__ import annotations

from .registry import PROMPT_KEYS, PromptKey, effective_prompt, get_default, get_label, get_risk

__all__ = [
    "PROMPT_KEYS",
    "PromptKey",
    "effective_prompt",
    "get_default",
    "get_label",
    "get_risk",
]

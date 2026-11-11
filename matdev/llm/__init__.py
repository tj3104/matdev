"""LLM の層。設定ファイルの llm.provider で差し替える。

会話の記録は共通の形 (Anthropic の Messages 形式のブロック: text / tool_use / tool_result、ほかに LLM 固有の
ブロック) で持ち、各部品が LLM ごとの形に変換する。LLM を足すときは base.LLM を継いだ部品を 1 つ書き、make_llm に足す。
"""
from __future__ import annotations

from ..config import LLMConfig
from .base import LLM, LLMAuthError, LLMConnectionError, LLMError, LLMRateLimit, MissingKey, Turn, Usage


def make_llm(config: LLMConfig, client_factory=None) -> LLM:
    if config.provider == "anthropic":
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(config, client_factory)
    if config.provider == "openai":
        from .openai_llm import OpenAILLM
        return OpenAILLM(config, client_factory)
    raise ValueError(f"知らない LLM です: {config.provider}")


__all__ = ["LLM", "LLMAuthError", "LLMConnectionError", "LLMError", "LLMRateLimit", "MissingKey", "Turn", "Usage",
           "make_llm"]

from __future__ import annotations

import os

from ..config import Settings
from .base import ChatProvider


def provider_from_settings(s: Settings) -> ChatProvider:
    if s.provider == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], s.model, s.max_output_tokens)
    if s.provider == "azure_openai":
        from .azure_openai import AzureOpenAIProvider
        return AzureOpenAIProvider(
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            deployment=s.model,
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            max_tokens=s.max_output_tokens,
        )
    raise ValueError(f"Unknown provider: {s.provider}")

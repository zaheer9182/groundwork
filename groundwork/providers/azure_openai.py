"""Azure OpenAI, for tenants that need the model inside their own subscription.

With no key configured it authenticates with the Function's managed identity
(Cognitive Services OpenAI User role), so there is no secret to store.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from .base import Message


class AzureOpenAIProvider:
    name = "azure_openai"

    def __init__(self, endpoint: str, deployment: str, api_key: str = "", max_tokens: int = 800,
                 api_version: str = "2024-10-21"):
        from openai import AsyncAzureOpenAI
        if api_key:
            self._client = AsyncAzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        else:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
            provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
            self._client = AsyncAzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=provider, api_version=api_version)
        self._deployment = deployment
        self._max_tokens = max_tokens

    async def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._deployment,
            temperature=0,
            max_tokens=self._max_tokens,
            stream=True,
            messages=[{"role": "system", "content": system}, *messages],
        )
        async for event in stream:
            if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                yield event.choices[0].delta.content

from __future__ import annotations

from collections.abc import AsyncIterator

from .base import Message


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 800):
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60)
        self._model = model
        self._max_tokens = max_tokens

    async def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=0,
            system=system,
            messages=messages,
        ) as response:
            async for text in response.text_stream:
                yield text

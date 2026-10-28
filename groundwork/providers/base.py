from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

Message = dict  # {"role": "user" | "assistant", "content": str}


class ChatProvider(Protocol):
    name: str

    def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        """Yield text deltas as they arrive."""
        ...

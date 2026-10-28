"""Extracted-chunk cache.

Keys are `source_id@version`. The only way to read the cache is by a key that
came from the current user's own search results, so text from documents the
user cannot open never reaches the prompt. A new eTag means a new key, so
edited documents are re-read instead of served stale.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from .chunking import Chunk


class ChunkCache(Protocol):
    def get(self, key: str) -> list[Chunk] | None: ...
    def put(self, key: str, chunks: list[Chunk]) -> None: ...


class MemoryCache:
    """Per-instance LRU. Enough for a single always-ready instance; use a shared
    store (Blob, Redis) when the app scales out and cold reads start to show."""

    def __init__(self, max_entries: int = 500):
        self._data: OrderedDict[str, list[Chunk]] = OrderedDict()
        self._max = max_entries

    def get(self, key: str) -> list[Chunk] | None:
        chunks = self._data.get(key)
        if chunks is not None:
            self._data.move_to_end(key)
        return chunks

    def put(self, key: str, chunks: list[Chunk]) -> None:
        self._data[key] = chunks
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

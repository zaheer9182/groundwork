from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SUPPORTED_EXTENSIONS = (".docx", ".pdf", ".txt", ".md")


@dataclass(frozen=True)
class Document:
    """A search hit. `version` must change whenever content changes (eTag, mtime)."""
    source_id: str
    version: str
    name: str
    url: str
    rank: int          # position in the source's own result list, 0 = best

    @property
    def cache_key(self) -> str:
        return f"{self.source_id}@{self.version}"


class Source(Protocol):
    async def search(self, query: str, limit: int) -> list[Document]: ...
    async def fetch(self, doc: Document) -> bytes: ...

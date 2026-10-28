"""A folder on disk that behaves like a search source.

Used for local development, the eval runner and tests, so the whole pipeline
runs without a tenant. An optional `.access.json` in the root maps file names
to the users allowed to see them, which lets tests prove permission trimming.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..ranking import bm25
from .base import SUPPORTED_EXTENSIONS, Document


class LocalSource:
    def __init__(self, root: str | Path, user: str | None = None):
        self.root = Path(root)
        self.user = user
        acl = self.root / ".access.json"
        self._acl: dict[str, list[str]] = json.loads(acl.read_text()) if acl.exists() else {}

    def _visible(self, path: Path) -> bool:
        allowed = self._acl.get(path.name)
        return allowed is None or (self.user is not None and self.user in allowed)

    def _files(self) -> list[Path]:
        return sorted(
            p for p in self.root.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and self._visible(p)
        )

    async def search(self, query: str, limit: int) -> list[Document]:
        files = self._files()
        texts = [f"{p.stem} {p.read_text(errors='ignore') if p.suffix in ('.md', '.txt') else ''}" for p in files]
        scored = bm25(query, texts)
        order = [i for i, s in scored if s > 0][:limit]
        return [
            Document(
                source_id=str(files[i].relative_to(self.root)),
                version=str(files[i].stat().st_mtime_ns),
                name=files[i].name,
                url=files[i].resolve().as_uri(),
                rank=r,
            )
            for r, i in enumerate(order)
        ]

    async def fetch(self, doc: Document) -> bytes:
        return (self.root / doc.source_id).read_bytes()

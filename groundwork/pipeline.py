"""Retrieve, then answer. Transport-agnostic: yields (event, data) tuples that
the HTTP layer turns into server-sent events.

Events, in order:
  status     {"stage": "searching" | "reading" | "writing"}
  delta      {"text": "..."}            (many)
  citations  [{"id", "title", "url", "section", "page"}]
  done       {"refused": bool, "grounded": bool, "request_id": str}
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .cache import ChunkCache
from .chunking import Chunk, Chunker
from .config import Settings
from .extract import extract
from .prompt import resolve_citations, search_query, system_prompt, user_turn
from .providers.base import ChatProvider
from .ranking import bm25, coverage, fuse
from .sources.base import Document, Source
from .telemetry import event, log, redact

MAX_CHUNKS_PER_DOCUMENT = 3


@dataclass
class Retrieval:
    documents: list[Document]
    chunks: list[Chunk]          # selected, best first
    coverage: float              # best query-term coverage among selected chunks (refusal gate)


class Assistant:
    def __init__(self, settings: Settings, provider: ChatProvider | None, cache: ChunkCache):
        self.s = settings
        self.provider = provider
        self.cache = cache
        self.chunker = Chunker(settings.chunk_chars, settings.chunk_overlap)

    async def _load(self, source: Source, doc: Document) -> list[Chunk]:
        cached = self.cache.get(doc.cache_key)
        if cached is not None:
            return cached
        data = await source.fetch(doc)
        chunks = self.chunker.split(doc.cache_key, doc.name, doc.url, extract(doc.name, data))
        self.cache.put(doc.cache_key, chunks)
        return chunks

    async def retrieve(self, source: Source, query: str) -> Retrieval:
        docs = await source.search(query, self.s.max_documents)
        loaded = await asyncio.gather(*(self._load(source, d) for d in docs), return_exceptions=True)

        candidates: list[Chunk] = []
        doc_rank: dict[str, int] = {}
        for doc, result in zip(docs, loaded):
            if isinstance(result, Exception):
                log.warning("document skipped", extra={"custom_dimensions": {"error": type(result).__name__}})
                continue
            doc_rank[doc.cache_key] = doc.rank
            candidates.extend(result)

        scored = bm25(query, [f"{c.title} {c.section} {c.text}" for c in candidates])
        matching = [i for i, score in scored if score > 0]
        if not matching:
            return Retrieval(docs, [], 0.0)

        by_source_rank = sorted(matching, key=lambda i: (doc_rank[candidates[i].doc_key], candidates[i].ordinal))
        fused = fuse([matching, by_source_rank])

        picked: list[Chunk] = []
        per_doc: dict[str, int] = {}
        for i, _ in fused:
            c = candidates[i]
            if per_doc.get(c.doc_key, 0) >= MAX_CHUNKS_PER_DOCUMENT:
                continue
            per_doc[c.doc_key] = per_doc.get(c.doc_key, 0) + 1
            picked.append(c)
            if len(picked) == self.s.top_k:
                break
        best = max(coverage(query, f"{c.title} {c.section} {c.text}") for c in picked)
        return Retrieval(docs, picked, best)

    async def ask(self, source: Source, question: str, history: list[dict] | None = None,
                  user: str = "anonymous") -> AsyncIterator[tuple[str, object]]:
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        history = [h for h in (history or []) if h.get("role") in ("user", "assistant") and h.get("content")]
        history = history[-self.s.history_turns:]

        yield "status", {"stage": "searching"}
        query = search_query(question, history)
        yield "status", {"stage": "reading"}
        found = await self.retrieve(source, query)

        refused = found.coverage < self.s.min_coverage
        answer = ""
        if refused:
            answer = self.s.refusal_text
            yield "delta", {"text": answer}
        else:
            yield "status", {"stage": "writing"}
            messages = [*history, {"role": "user", "content": user_turn(question, found.chunks)}]
            async for text in self.provider.stream(system_prompt(self.s.assistant_name, self.s.refusal_text), messages):
                answer += text
                yield "delta", {"text": text}
            refused = answer.strip().startswith(self.s.refusal_text[:40])

        citations, invented = resolve_citations(answer, found.chunks)
        grounded = refused or bool(citations)
        yield "citations", [c.as_dict() for c in citations]
        yield "done", {"refused": refused, "grounded": grounded, "request_id": request_id}

        event(
            "groundwork.answer",
            request_id=request_id,
            user=user,
            question=redact(question),
            documents=len(found.documents),
            chunks=len(found.chunks),
            coverage=round(found.coverage, 3),
            refused=refused,
            grounded=grounded,
            citations=len(citations),
            invented_citations=len(invented),
            follow_up=bool(history),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

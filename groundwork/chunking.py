"""Paragraph-aware chunking.

  * Split on paragraphs; only split inside a paragraph (at sentence ends) when
    it alone exceeds the target size.
  * Track the nearest heading (markdown "#" lines or docx heading styles) and
    keep it on every chunk, so retrieval and citations know the section.
  * Carry an overlap from the previous chunk so facts at a boundary survive.
  * Keep the page number for PDFs so a citation can link to the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Block

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    doc_key: str
    title: str
    url: str
    page: int | None
    ordinal: int
    section: str
    text: str

    @property
    def link(self) -> str:
        return f"{self.url}#page={self.page}" if self.page else self.url


class Chunker:
    def __init__(self, target: int = 1200, overlap: int = 200):
        if overlap >= target:
            raise ValueError("overlap must be smaller than target")
        self.target = target
        self.overlap = overlap

    def _pieces(self, paragraph: str) -> list[str]:
        if len(paragraph) <= self.target:
            return [paragraph]
        out, buf = [], ""
        for sentence in _SENTENCE_END.split(paragraph):
            while len(sentence) > self.target:        # a "sentence" with no punctuation
                out.append(sentence[: self.target])
                sentence = sentence[self.target:]
            if buf and len(buf) + len(sentence) + 1 > self.target:
                out.append(buf)
                buf = sentence
            else:
                buf = f"{buf} {sentence}".strip()
        if buf:
            out.append(buf)
        return out

    def _tail(self, text: str) -> str:
        tail = text[-self.overlap:]
        cut = tail.find(" ")
        return tail[cut + 1:] if cut != -1 else tail

    def split(self, doc_key: str, title: str, url: str, blocks: list[Block]) -> list[Chunk]:
        chunks: list[Chunk] = []
        section = ""

        def emit(page, text):
            chunks.append(Chunk(doc_key, title, url, page, len(chunks), section, text.strip()))

        for page, text in blocks:
            buf = ""
            for raw in re.split(r"\n\s*\n", text):
                para = raw.strip()
                if not para:
                    continue
                heading = _HEADING.match(para.splitlines()[0])
                if heading:
                    if buf:
                        emit(page, buf)
                        buf = ""
                    section = heading.group(1).strip()
                    rest = "\n".join(para.splitlines()[1:]).strip()
                    if not rest:
                        continue
                    para = rest
                for piece in self._pieces(para):
                    if buf and len(buf) + len(piece) + 2 > self.target:
                        emit(page, buf)
                        buf = f"{self._tail(buf)}\n\n{piece}"
                    else:
                        buf = f"{buf}\n\n{piece}" if buf else piece
            if buf:
                emit(page, buf)
        return chunks

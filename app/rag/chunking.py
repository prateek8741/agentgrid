"""Structure-aware chunking: split on headings/paragraphs first, then
sentences, packing pieces up to `size` characters with `overlap` carried over
so answers that straddle a boundary remain retrievable."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.extract import Page

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_HEADING = re.compile(r"^(#{1,6}\s+.+|[A-Z][A-Za-z0-9 &/,'()-]{2,80}:?)$")


@dataclass
class Chunk:
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _pieces(text: str, size: int) -> list[tuple[str, str | None]]:
    """Paragraph-level pieces annotated with the nearest heading."""
    out: list[tuple[str, str | None]] = []
    heading: str | None = None
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        first_line = block.split("\n", 1)[0].strip()
        if _HEADING.match(first_line) and len(first_line) < 90:
            heading = first_line.lstrip("#").strip().rstrip(":")
        if len(block) <= size:
            out.append((block, heading))
            continue
        sentences = _SENTENCE.split(block)
        buf = ""
        for sentence in sentences:
            while len(sentence) > size:  # pathological run-on text: hard split
                out.append((sentence[:size], heading))
                sentence = sentence[size:]
            if len(buf) + len(sentence) + 1 > size and buf:
                out.append((buf.strip(), heading))
                buf = ""
            buf += sentence + " "
        if buf.strip():
            out.append((buf.strip(), heading))
    return out


def _emit(chunks: list[Chunk], buf: str, heading: str | None, page: int | None) -> None:
    text = buf.strip()
    if not text:
        return
    # Contextual header: prefix the section title so a chunk about "Payment options"
    # still matches "how can I pay?" even if the body never repeats the heading.
    if heading and not text.lstrip("# ").startswith(heading):
        text = f"{heading}\n{text}"
    chunks.append(Chunk(text, {"page": page, "section": heading}))


def chunk_pages(pages: list[Page], size: int = 900, overlap: int = 150) -> list[Chunk]:
    """Pack paragraphs into chunks of <= `size` chars. A new section (heading) always
    starts a new chunk: topic-pure chunks embed far better than mixed ones."""
    chunks: list[Chunk] = []
    for page in pages:
        buf, buf_heading = "", None
        for piece, heading in _pieces(page.text, size):
            new_section = heading != buf_heading and bool(buf.strip())
            if buf and (new_section or len(buf) + len(piece) + 2 > size):
                _emit(chunks, buf, buf_heading, page.page)
                tail = "" if new_section or not overlap else buf[-overlap:]
                buf = (tail[tail.find(" ") + 1 :] if " " in tail else tail) + "\n\n" if tail else ""
            if not buf.strip():
                buf_heading = heading
            buf += piece + "\n\n"
        _emit(chunks, buf, buf_heading, page.page)
    return [c for c in chunks if len(c.text) >= 20]

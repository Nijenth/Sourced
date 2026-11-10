"""Load PDFs page by page and split pages into chunks that never cross a page boundary.

Keeping every chunk on a single page is what makes page-level citations exact.
"""
from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass

from pypdf import PdfReader

_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+(?=[A-Z0-9\"'(•$])")
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class Page:
    source: str
    page: int  # 1-based
    text: str


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    page: int
    text: str


class PdfError(ValueError):
    """The file could not be read as a PDF, or has no extractable text."""


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _strip_repeated_lines(pages_lines: list[list[str]]) -> list[list[str]]:
    """Remove running headers/footers: lines that repeat on most pages (page numbers ignored)."""
    if len(pages_lines) < 3:
        return pages_lines
    def key(line: str) -> str:
        return _DIGITS.sub("#", line.strip().lower())

    counts: Counter[str] = Counter()  # how many pages contain each normalised line
    for lines in pages_lines:
        counts.update({key(l) for l in lines if l.strip()})
    threshold = max(3, int(0.6 * len(pages_lines)))
    boiler = {k for k, n in counts.items() if n >= threshold}
    return [[l for l in lines if key(l) not in boiler] for lines in pages_lines]


def load_pdf(data: bytes, source: str) -> list[Page]:
    """Extract text per page. Raises PdfError if the PDF is unreadable or has no text layer."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - depends on file
                raise PdfError(f"{source} is password-protected.") from exc
        raw = [(pg.extract_text() or "") for pg in reader.pages]
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"{source} could not be read as a PDF.") from exc

    cleaned = _strip_repeated_lines([t.splitlines() for t in raw])
    pages = [Page(source, i + 1, collapse(" ".join(lines))) for i, lines in enumerate(cleaned)]
    pages = [pg for pg in pages if pg.text]
    if not pages:
        raise PdfError(
            f"{source} has no selectable text. It is probably a scanned document; run OCR on it first."
        )
    return pages


def count_pages(data: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise PdfError("This file could not be read as a PDF.") from exc


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def chunk_pages(pages: list[Page], chunk_chars: int = 900, overlap_sentences: int = 1) -> list[Chunk]:
    """Pack whole sentences into chunks of roughly `chunk_chars`, overlapping by a sentence."""
    chunks: list[Chunk] = []
    for pg in pages:
        sentences = split_sentences(pg.text)
        # Hard-split any single sentence that is far longer than a chunk (tables, run-on text).
        units: list[str] = []
        for s in sentences:
            while len(s) > chunk_chars * 1.5:
                cut = s.rfind(" ", 0, chunk_chars)
                cut = cut if cut > 0 else chunk_chars
                units.append(s[:cut].strip())
                s = s[cut:].strip()
            if s:
                units.append(s)

        current: list[str] = []
        size = 0
        idx = 0

        def flush():
            nonlocal idx
            text = " ".join(current).strip()
            if text:
                chunks.append(Chunk(f"{file_hash(pg.source.encode())}-p{pg.page}-c{idx}", pg.source, pg.page, text))
                idx += 1

        for unit in units:
            if current and size + len(unit) + 1 > chunk_chars:
                flush()
                current = current[-overlap_sentences:] if overlap_sentences else []
                size = sum(len(u) + 1 for u in current)
                if size + len(unit) + 1 > chunk_chars:  # overlap would overflow: start fresh
                    current, size = [], 0
            current.append(unit)
            size += len(unit) + 1
        flush()
    return chunks

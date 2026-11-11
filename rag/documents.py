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


# ---- tables -----------------------------------------------------------------------------------
# pypdf's default ("plain") extraction puts every table cell on its own line, so after joining lines a
# table becomes run-on text ("... After probation 4 weeks Lead, Principal and Director roles 8 weeks").
# Its "layout" mode keeps the columns apart with wide gaps. We keep plain mode for ordinary text (it
# reads multi-column pages in the right order) and use layout mode only to find table rows, which are
# rewritten with their column headers: "Lead, Principal and Director roles | Notice required — 8 weeks."
# A dash (not a colon) joins header and value, so sentence splitting can never cut a row in two.

_TABLE_GAP = re.compile(r"\s{5,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_line(line: str) -> str:
    """pypdf returns the bullet glyph as DEL (\\x7f); show it as a bullet and drop other control characters."""
    return _CONTROL.sub("", line.replace("\x7f", "\u2022"))


def table_blocks(layout_text: str, max_blank_lines: int = 2) -> list[list[list[str]]]:
    """Runs of 2+ consecutive lines that split into 2+ cells on wide gaps.

    Single blank lines between rows are normal in layout mode; a longer run of blank lines (e.g. the
    space above a page footer) ends the table.
    """
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    blanks = 0
    for line in layout_text.splitlines():
        stripped = _clean_line(line).strip()
        if not stripped:
            blanks += 1
            continue
        if blanks > max_blank_lines:
            if len(current) >= 2:
                blocks.append(current)
            current = []
        blanks = 0
        cells = [c.strip() for c in _TABLE_GAP.split(stripped) if c.strip()]
        if len(cells) >= 2:
            current.append(cells)
            continue
        if len(current) >= 2:
            blocks.append(current)
        current = []
    if len(current) >= 2:
        blocks.append(current)
    return blocks


def format_table(block: list[list[str]]) -> list[tuple[list[str], str]]:
    """(cells, sentence) per row. A first row without digits is treated as the header and repeated in each row."""
    header = block[0] if not any(any(ch.isdigit() for ch in c) for c in block[0]) else None
    out = []
    for i, cells in enumerate(block):
        if header and i > 0 and len(cells) == len(header):
            text = f"{cells[0]} | " + "; ".join(f"{h} \u2014 {c}" for h, c in zip(header[1:], cells[1:]))
        else:
            text = " | ".join(cells)
        out.append((cells, text if text.endswith((".", "!", "?", ":")) else text + "."))
    return out


def merge_tables(plain_lines: list[str], blocks: list[list[list[str]]]) -> list[str]:
    """Replace the plain-mode lines of each table row (one line per cell, or all cells on one line)."""
    rows = [row for block in blocks for row in format_table(block)]
    lines = [_clean_line(l).strip() for l in plain_lines]
    out, i = [], 0
    while i < len(lines):
        for cells, text in rows:
            n = len(cells)
            if lines[i : i + n] == cells:
                out.append(text)
                i += n
                break
            if lines[i] == " ".join(cells):
                out.append(text)
                i += 1
                break
        else:
            out.append(lines[i])
            i += 1
    return out


def _page_lines(page) -> list[str]:
    plain = page.extract_text() or ""
    try:
        layout = page.extract_text(extraction_mode="layout") or ""
    except Exception:  # older pypdf or an unusual page: keep plain text
        return [_clean_line(l) for l in plain.splitlines()]
    return merge_tables(plain.splitlines(), table_blocks(layout))


def load_pdf(data: bytes, source: str) -> list[Page]:
    """Extract text per page. Raises PdfError if the PDF is unreadable or has no text layer."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - depends on file
                raise PdfError(f"{source} is password-protected.") from exc
        raw_lines = [_page_lines(pg) for pg in reader.pages]
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"{source} could not be read as a PDF.") from exc

    cleaned = _strip_repeated_lines(raw_lines)
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

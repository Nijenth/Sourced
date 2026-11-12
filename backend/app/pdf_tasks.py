"""Bounded PDF operations run outside the API and worker processes."""

import io
import json
import math
import os
import re
import subprocess
import sys
import threading
from dataclasses import asdict, replace

from rag.documents import Chunk, chunk_pages, count_pages, load_pdf

PREVIEW_MAX_SIDE = 1400
DEFAULT_MEMORY_MB = 1024
MEMORY_ENV = "SOURCED_PDF_MEMORY_MB"


CONCURRENCY_ENV = "SOURCED_PDF_CONCURRENCY"
DEFAULT_CONCURRENCY = 2
REQUEST_WAIT_SECONDS = 10.0


class PdfTaskError(ValueError):
    pass


class PdfBusy(PdfTaskError):
    """Every PDF slot stayed busy for the whole wait. Callers answer 503 so the client can retry."""

    def __init__(self):
        super().__init__("The server is busy reading other PDFs. Try again in a moment.")


_slots: tuple[int, threading.BoundedSemaphore] | None = None
_slots_lock = threading.Lock()


def pdf_slots() -> threading.BoundedSemaphore:
    """One process-wide limit on PDF subprocesses running at once (security audit S02): uploads, page
    pictures and highlights all share it, so a burst of them queues instead of exhausting memory."""
    global _slots
    size = int(os.environ.get(CONCURRENCY_ENV, DEFAULT_CONCURRENCY))
    if size < 1:
        raise ValueError(f"{CONCURRENCY_ENV} must be a positive integer.")
    with _slots_lock:
        if _slots is None or _slots[0] != size:
            _slots = (size, threading.BoundedSemaphore(size))
        return _slots[1]


def run_pdf_task(operation: str, data: bytes, *args: str, timeout: int, background: bool = False) -> bytes:
    """Run one PDF operation in a child process, once a slot is free. A web request waits up to
    REQUEST_WAIT_SECONDS and then gets PdfBusy; the background worker waits as long as it takes."""
    slots = pdf_slots()
    acquired = slots.acquire() if background else slots.acquire(timeout=REQUEST_WAIT_SECONDS)
    if not acquired:
        raise PdfBusy()
    try:
        return _run(operation, data, args, timeout)
    finally:
        slots.release()


def _run(operation: str, data: bytes, args: tuple[str, ...], timeout: int) -> bytes:
    # The parser only needs the PDF and a minimal runtime environment.
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    env[MEMORY_ENV] = str(memory_limit_mb())
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "backend.app.pdf_tasks", operation, *args],
            input=data, capture_output=True, timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        raise PdfTaskError("The PDF took too long to process.") from None
    if result.returncode == 3:
        raise PdfTaskError("The PDF contains too much text.")
    if result.returncode == 4:
        raise PdfTaskError("The PDF needs too much memory to process.")
    if result.returncode != 0:
        raise PdfTaskError("The PDF could not be read or has no selectable text.")
    return result.stdout


def extract_chunks(data: bytes, filename: str, max_characters: int, timeout: int) -> list[Chunk]:
    # The background worker waits for a slot rather than failing the document.
    payload = run_pdf_task("extract", data, filename, str(max_characters), timeout=timeout, background=True)
    malformed = "The PDF text could not be extracted. Try saving the PDF again and uploading it."
    try:
        records = json.loads(payload)
        if not isinstance(records, list):
            raise ValueError
        chunks = [Chunk(**record) for record in records]
    except (TypeError, ValueError, RecursionError):
        raise PdfTaskError(malformed) from None

    if not chunks:
        raise PdfTaskError("The PDF has no selectable text.")
    if any(
        type(chunk.page) is not int or chunk.page < 1 or chunk.source != filename
        or not isinstance(chunk.id, str) or not chunk.id or "\x00" in chunk.id
        or not isinstance(chunk.text, str) or not chunk.text.strip() or "\x00" in chunk.text
        for chunk in chunks
    ) or len({chunk.id for chunk in chunks}) != len(chunks):
        raise PdfTaskError(malformed)
    return chunks


def render_preview(data: bytes, page_number: int, quote: str = "") -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_number - 1]
        longest = max(page.get_size())
        if not math.isfinite(longest) or longest <= 0:
            raise ValueError("Invalid page size.")
        bitmap = page.render(scale=min(1.6, (PREVIEW_MAX_SIDE - 1) / longest))
        image = bitmap.to_pil()
        if quote:
            from PIL import ImageDraw

            text = page.get_textpage()
            match = text.search(quote).get_next()
            matches = [match] if match is not None else []
            if not matches:
                pieces = dict.fromkeys(piece.strip() for piece in re.split(r"\||…|\.\.\.", quote))
                for piece in pieces:
                    if len(piece) >= 4:
                        match = text.search(piece).get_next()
                        if match is not None:
                            matches.append(match)
            if matches:
                image = image.convert("RGB")
                draw = ImageDraw.Draw(image, "RGBA")
                position = bitmap.get_posconv(page)
                text.count_rects()
                for match in matches:
                    for index in range(text.count_rects(*match)):
                        left, bottom, right, top = text.get_rect(index)
                        x1, y1 = position.to_bitmap(left, top)
                        x2, y2 = position.to_bitmap(right, bottom)
                        draw.rectangle((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), fill=(255, 215, 66, 100))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    finally:
        pdf.close()


def memory_limit_mb() -> int:
    value = int(os.environ.get(MEMORY_ENV, DEFAULT_MEMORY_MB))
    if value < 128:
        raise ValueError(f"{MEMORY_ENV} must be at least 128.")
    return value


def apply_memory_limit() -> None:
    """Cap this process's address space on Linux and macOS. Windows has no equivalent; there only the timeout applies."""
    try:
        import resource
    except ImportError:
        return
    limit = int(os.environ.get(MEMORY_ENV, DEFAULT_MEMORY_MB)) * 1024 * 1024
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, OSError):
        pass  # e.g. macOS refuses RLIMIT_AS changes; the timeout still applies.


def main() -> int:
    operation, *args = sys.argv[1:]
    apply_memory_limit()
    data = sys.stdin.buffer.read()
    try:
        if operation == "count":
            result = str(count_pages(data)).encode()
        elif operation == "extract":
            filename, max_characters = args
            pages = load_pdf(data, filename)
            if sum(len(page.text) for page in pages) > int(max_characters):
                return 3
            pages = [replace(page, text=page.text.replace("\x00", "")) for page in pages]
            result = json.dumps([asdict(chunk) for chunk in chunk_pages(pages)]).encode()
        elif operation == "preview":
            result = render_preview(data, int(args[0]))
        elif operation == "highlight":
            quote, data = data.split(b"\n", 1)
            result = render_preview(data, int(args[0]), json.loads(quote))
        else:
            return 2
    except MemoryError:
        return 4
    except Exception:
        return 2
    sys.stdout.buffer.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

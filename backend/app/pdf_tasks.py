"""Bounded PDF operations run outside the API and worker processes."""

import io
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict, replace

from rag.documents import Chunk, chunk_pages, count_pages, load_pdf

PREVIEW_MAX_SIDE = 1400


class PdfTaskError(ValueError):
    pass


def run_pdf_task(operation: str, data: bytes, *args: str, timeout: int) -> bytes:
    # The parser only needs the PDF and a minimal runtime environment.
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if name in os.environ}
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "backend.app.pdf_tasks", operation, *args],
            input=data, capture_output=True, timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        raise PdfTaskError("The PDF took too long to process.") from None
    if result.returncode == 3:
        raise PdfTaskError("The PDF contains too much text.")
    if result.returncode != 0:
        raise PdfTaskError("The PDF could not be read or has no selectable text.")
    return result.stdout


def extract_chunks(data: bytes, filename: str, max_characters: int, timeout: int) -> list[Chunk]:
    payload = run_pdf_task("extract", data, filename, str(max_characters), timeout=timeout)
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


def main() -> int:
    operation, *args = sys.argv[1:]
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
    except Exception:
        return 2
    sys.stdout.buffer.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

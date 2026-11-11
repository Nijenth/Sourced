"""Bounded PDF operations run outside the API and worker processes."""

import io
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, replace

from rag.documents import chunk_pages, count_pages, load_pdf

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


def render_preview(data: bytes, page_number: int) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_number - 1]
        longest = max(page.get_size())
        if not math.isfinite(longest) or longest <= 0:
            raise ValueError("Invalid page size.")
        bitmap = page.render(scale=min(1.6, (PREVIEW_MAX_SIDE - 1) / longest))
        output = io.BytesIO()
        bitmap.to_pil().save(output, format="PNG")
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
        else:
            return 2
    except Exception:
        return 2
    sys.stdout.buffer.write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

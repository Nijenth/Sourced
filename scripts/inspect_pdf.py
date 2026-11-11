"""Print how pypdf reads one page, in its default and "layout" modes, line by line.

    python scripts/inspect_pdf.py 4
    python scripts/inspect_pdf.py 10 --file path/to/other.pdf

Useful for seeing how tables come out before they are stored for search.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page", type=int, help="page number, starting at 1")
    ap.add_argument("--file", default=str(PDF))
    args = ap.parse_args()
    page = PdfReader(args.file).pages[args.page - 1]
    for mode in ("plain", "layout"):
        print(f"===== {mode} mode =====")
        try:
            text = page.extract_text(extraction_mode=mode) or ""
        except TypeError:  # very old pypdf without extraction modes
            text = page.extract_text() or ""
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                print(f"{i:3}| {line.rstrip()!r}")
        print()


if __name__ == "__main__":
    main()

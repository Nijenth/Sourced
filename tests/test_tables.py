"""Tables in PDFs: rows are rebuilt with their headers instead of becoming run-on text."""
from __future__ import annotations

from pathlib import Path

from rag.documents import collapse, load_pdf, merge_tables, table_blocks

PDF = Path(__file__).resolve().parent.parent / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"

LAYOUT = ("   Situation                                  Notice required (either side)\n\n"
          "   During probation                           1 week\n\n"
          "   Lead, Principal and Director roles         8 weeks"
          + "\n" * 40
          + "Footer text for every page.                                         Page 4")
PLAIN = ["Footer text for every page.", "Page 4", "Notice periods", " Situation", "Notice required (either side)",
         "During probation", "1 week", "Lead, Principal and Director roles", "8 weeks"]


def test_rows_get_their_column_header_and_the_footer_is_left_alone():
    text = collapse(" ".join(merge_tables(PLAIN, table_blocks(LAYOUT))))
    assert "Lead, Principal and Director roles | Notice required (either side) \u2014 8 weeks." in text
    assert "During probation | Notice required (either side) \u2014 1 week." in text
    assert text.startswith("Footer text for every page. Page 4")  # not glued onto the table


def test_cells_already_on_one_plain_line_are_also_rebuilt():
    layout = "   Hotel per night          $220\n\n   New York or San Francisco          $300"
    out = merge_tables(["Hotel per night $220", "New York or San Francisco $300"], table_blocks(layout))
    assert out == ["Hotel per night | $220.", "New York or San Francisco | $300."]


def test_ordinary_text_is_not_mistaken_for_a_table():
    layout = " Every new employee has a probation period of 90 days.\n Your manager meets you at 30, 60 and 90 days."
    assert table_blocks(layout) == []


def test_a_table_row_is_never_split_across_passages():
    from rag.documents import Page, chunk_pages

    rows = " ".join(f"Role {i} | Notice required (either side) \u2014 {i} weeks." for i in range(1, 60))
    for chunk in chunk_pages([Page("t.pdf", 1, rows)]):
        assert chunk.text.endswith("weeks."), chunk.text[-60:]


def test_the_real_handbook_notice_table_reads_row_by_row():
    page4 = load_pdf(PDF.read_bytes(), PDF.name)[3].text
    assert "Lead, Principal and Director roles | Notice required (either side) \u2014 8 weeks." in page4, page4
    assert "\x7f" not in page4 and "\u2022" in page4  # bullets are real bullets now
    assert "fictional company" not in page4  # the repeated footer is still removed

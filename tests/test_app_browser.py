"""Audit N03: in a real browser, the Streamlit demo shows hostile text without links, images or formatting.

Streamlit links bare web and email addresses in the browser, after Markdown is parsed, so only a real page shows
whether md_escape stops that. Needs Python Playwright with Chromium; skipped where they aren't installed.
"""

import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

APP = Path(__file__).resolve().parents[1] / "app.py"
HOSTILE = (
    "Visit https://evil.example/x or www.evil.example, mail boss@evil.example, "
    "[click](https://evil.example/login) ![pixel](https://evil.example/p.png) **bold** <b>tag</b> $5"
)

PAGE = textwrap.dedent('''
    import ast, re, sys
    import streamlit as st

    tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
    wanted = {"_MARKDOWN_PUNCTUATION", "_WORD_JOINER", "_LINK_SCHEME", "_WWW", "md_escape"}
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in wanted for t in n.targets))]
    space = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), space)
    text = sys.argv[2]
    st.markdown("MARKDOWN " + space["md_escape"](text))
    st.caption("CAPTION " + space["md_escape"](text))
    st.warning("WARNING " + space["md_escape"](text))
    with st.expander("EXPANDER " + space["md_escape"](text)):
        st.write("inside")
''')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_hostile_text_renders_literally_in_a_browser(tmp_path):
    page = tmp_path / "page.py"
    page.write_text(PAGE, "utf-8")
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(page), "--server.port", str(port), "--server.headless", "true",
         "--browser.gatherUsageStats", "false", "--", str(APP), HOSTILE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:  # noqa: BLE001 (no browser installed here)
                pytest.skip(f"Chromium isn't available: {exc}")
            tab = browser.new_page()
            deadline = time.monotonic() + 60
            while True:
                try:
                    tab.goto(f"http://127.0.0.1:{port}")
                    tab.get_by_text("MARKDOWN", exact=False).first.wait_for(timeout=15_000)
                    break
                except sync_api.Error:
                    assert time.monotonic() < deadline, "Streamlit didn't start."
                    time.sleep(1)
            tab.get_by_text("EXPANDER", exact=False).first.wait_for()
            main = tab.locator("section.stMain, [data-testid='stMain'], .main").first
            assert main.locator("a").count() == 0, main.locator("a").evaluate_all("els => els.map(e => e.href)")
            assert main.locator("img, strong, b, em").count() == 0
            for label in ("MARKDOWN", "CAPTION", "WARNING", "EXPANDER"):
                shown = tab.get_by_text(label, exact=False).first.inner_text().replace("⁠", "")
                assert HOSTILE in " ".join(shown.split()), (label, shown)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

"""A Streamlit page for streamlit-text.spec.ts: the demo's real answer rendering (app.py's render_answer and
show) fed hostile text. app.py runs as a whole app on import, so only those functions are loaded from it."""

import ast
import html
import re
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parents[2] / "app.py"
NEEDED = {"_MARKDOWN_PUNCTUATION", "md_escape", "_TEXT_STYLES", "show", "render_answer"}

tree = ast.parse(APP.read_text("utf-8"))
nodes = [
    node for node in tree.body
    if (isinstance(node, ast.FunctionDef) and node.name in NEEDED)
    or (isinstance(node, ast.Assign) and any(getattr(target, "id", None) in NEEDED for target in node.targets))
]
app = {"st": st, "html": html, "re": re, "render_page": None, "file_hash": None}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), "exec"), app)  # noqa: S102 (our own file)
assert NEEDED <= app.keys()

HOSTILE = (
    "Visit https://evil.example/x, www.evil.example, _https://under.example and _www.under.example, "
    "mail boss@evil.example or @handle, [click](https://evil.example/login) ![pixel](https://evil.example/p.png) "
    "**bold** _em_ `code` <b>tag</b> <img src=x onerror=alert(1)> $5 & co"
)

st.caption("Streamlit text check")
app["render_answer"]({
    "role": "assistant", "content": "ANSWER " + HOSTILE, "found": True, "partial": True, "status": "answered",
    "searched_for": "SEARCHED " + HOSTILE,
    "judgement": {"verdict": "partial", "problems": ["PROBLEM " + HOSTILE], "judge": "groq"},
    "citations": [{"number": 1, "source": "SOURCE " + HOSTILE, "page": 6, "quote": "QUOTE " + HOSTILE,
                   "verified": False}],
}, 0, {})
app["render_answer"]({
    "role": "assistant", "content": "BLOCKED " + HOSTILE, "status": "unverified", "reason": "REASON " + HOSTILE,
    "passages": [{"source": "PASSAGE-SOURCE " + HOSTILE, "page": 2, "text": "PASSAGE " + HOSTILE}],
}, 1, {})
app["show"]("WARNING " + HOSTILE, "warning")
app["show"]("PROMPT " + HOSTILE)
st.caption("END OF PAGE")

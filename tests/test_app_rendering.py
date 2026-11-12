"""Security audit S03: the Streamlit demo shows document and model text literally, and a blocked answer never
repeats the claims the accuracy check rejected.

app.py runs as a Streamlit script on import, so these tests load only the functions they need from it.
"""

import ast
import html
import re
from pathlib import Path
from types import SimpleNamespace

from markdown_it import MarkdownIt

from rag.citations import INVALID_OUTPUT, NOT_FOUND, UNVERIFIED, snippet

APP = Path(__file__).resolve().parents[1] / "app.py"
NEEDED = {"_MARKDOWN_PUNCTUATION", "_WORD_JOINER", "_LINK_SCHEME", "_WWW", "JUDGE_BLOCK_REASON", "md_escape",
          "_same_question", "answer_to_message"}


def load_app_parts() -> dict:
    tree = ast.parse(APP.read_text("utf-8"))
    keep = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in NEEDED)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in NEEDED for t in node.targets))
    ]
    namespace = {"re": re, "UNVERIFIED": UNVERIFIED, "INVALID_OUTPUT": INVALID_OUTPUT, "snippet": snippet}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(APP), "exec"), namespace)  # noqa: S102 (our own file)
    assert NEEDED <= namespace.keys()
    return namespace


APP_PARTS = load_app_parts()
md_escape = APP_PARTS["md_escape"]

HOSTILE = [
    "[Click here](https://evil.example/login)",
    "![tracker](https://evil.example/pixel.png)",
    "<img src=x onerror=alert(1)>",
    "**Urgent:** _update_ your `password` now",
    "Meals cost $40 a day and hotels $300 a night.",
    "# Ignore the handbook",
    "> Quoted instruction",
    "1. First\n- Second",
    "Visit <https://evil.example> or https://evil.example",
    "Back\\slash, pipes | and ~~strike~~",
]


def test_markdown_and_html_stay_literal():
    renderer = MarkdownIt("commonmark").enable("strikethrough").enable("table")
    for text in HOSTILE:
        rendered = renderer.render(md_escape(text))
        for tag in ("<a", "<img", "<strong", "<em", "<code", "<h1", "<blockquote", "<ol", "<ul", "<s>", "<del"):
            assert tag not in rendered, (text, rendered)
        visible = html.unescape(re.sub(r"<[^>]+>", "", rendered)).replace("\u2060", "")
        assert " ".join(visible.split()) == " ".join(text.split()), (text, visible)


def test_plain_text_is_unchanged_when_shown():
    renderer = MarkdownIt("commonmark")
    text = "Full-time employees get 24 days of paid annual leave [1]. It's pro-rated (part-time)!"
    assert html.unescape(re.sub(r"<[^>]+>", "", renderer.render(md_escape(text)))).strip() == text
    assert "\u2060" not in md_escape(text)  # only addresses get the invisible joiner


# The patterns GFM-style autolinking looks for in text (web addresses, www. hosts and email addresses).
AUTOLINKS = [
    re.compile(r"(?i)(?:https?|ftp|mailto|xmpp):"),
    re.compile(r"(?i)\bwww\."),
    re.compile(r"[\w.+-]@[\w-]"),
]


def test_addresses_can_no_longer_be_autolinked():
    for text in ("See https://evil.example/x and HTTP://EVIL.EXAMPLE", "www.evil.example", "mail boss@evil.example",
                 "mailto:boss@evil.example", "ftp://files.example"):
        plain = md_escape(text).replace("\\", "")
        assert not any(pattern.search(plain) for pattern in AUTOLINKS), plain
        assert plain.replace("\u2060", "") == text


def answer(status, reason, judgement):
    return SimpleNamespace(search_query="q", text="text", found=False, partial=False, judgement=judgement,
                           status=status, reason=reason, hits=[], citations=[])


def test_a_judge_blocked_answer_gets_a_fixed_reason():
    rejected = "An accuracy check found claims the sources don't support: The CEO gets 90 days of leave."
    judgement = SimpleNamespace(verdict="unsupported", problems=["The CEO gets 90 days of leave."], judge="groq")
    message = APP_PARTS["answer_to_message"](answer(UNVERIFIED, rejected, judgement), "q")
    assert message["reason"] == APP_PARTS["JUDGE_BLOCK_REASON"]
    assert "90 days" not in message["reason"]


def test_other_reasons_are_kept():
    for status, reason in ((UNVERIFIED, "The quote given for source [1] isn't in that source."),
                           (INVALID_OUTPUT, "The model's reply wasn't in the required JSON format."),
                           (NOT_FOUND, "")):
        assert APP_PARTS["answer_to_message"](answer(status, reason, None), "q")["reason"] == reason

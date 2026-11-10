"""Score the chatbot against questions with known answers from the sample handbook.

Usage:
    python eval/run_eval.py                    # uses Claude if ANTHROPIC_API_KEY is set, else OpenAI
    python eval/run_eval.py --provider openai
    python eval/run_eval.py --provider offline # keyword baseline, no API keys

Writes a Markdown report to eval/results.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from rag.config import load_settings  # noqa: E402
from rag.documents import chunk_pages, load_pdf  # noqa: E402
from rag.pipeline import RAGPipeline  # noqa: E402
from rag.store import VectorStore  # noqa: E402

PDF = ROOT / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


def build(provider: str):
    from rag.providers import ProviderError, make_chat, make_embedder

    s = load_settings()
    env = dict(os.environ)
    if provider == "offline":
        env["RAG_OFFLINE_MODE"] = "1"
    try:
        return make_embedder(env), make_chat(provider, env), s.top_k
    except ProviderError as exc:
        sys.exit(f"{exc} (or use --provider offline)")


class StopEval(Exception):
    """The provider kept failing; finish with the results gathered so far."""


def ask_with_retry(pipeline, question: str, wait: float):
    """Ask once; on a provider error (e.g. a free-tier rate limit) wait and retry once, then stop cleanly."""
    from rag.providers import ProviderError

    try:
        return pipeline.ask(question)
    except ProviderError as exc:
        print(f"     {exc} Waiting {wait:.0f}s, then retrying once…")
        time.sleep(wait)
        try:
            return pipeline.ask(question)
        except ProviderError as exc2:
            raise StopEval(str(exc2)) from exc2


def has_all(text: str, expected: list[str]) -> bool:
    t = text.lower().replace(",", "")
    return all(e.lower().replace(",", "") in t for e in expected)


def main() -> None:
    s = load_settings()
    from rag.providers import PROVIDERS, chat_provider_names

    ready = chat_provider_names()
    default = ready[0] if ready else "offline"
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["offline", *PROVIDERS, "claude"], default=default)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results.md"))
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to wait between questions (free tiers: Gemini allows ~5 requests/min, so use 13)")
    ap.add_argument("--retry-wait", type=float, default=30.0,
                    help="seconds to wait before retrying a question once after a provider error such as a rate limit")
    args = ap.parse_args()

    embedder, chat, top_k = build(args.provider)
    store = VectorStore(embedder, "eval")
    store.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))
    pipeline = RAGPipeline(store, chat, top_k=top_k)

    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    rows, totals = [], {"retrieval": [0, 0], "answer": [0, 0], "citation": [0, 0], "refusal": [0, 0], "blocked": [0, 0]}
    started = time.time()
    stopped = ""

    for i, case in enumerate(cases):
        if i and args.delay:
            time.sleep(args.delay)
        q, page, expect = case["question"], case["page"], case["expect"]
        try:
            a = ask_with_retry(pipeline, q, args.retry_wait)
        except StopEval as exc:
            stopped = f"Stopped after {i} of {len(cases)} questions: {exc}"
            print(f"\n{stopped}")
            break
        hit_pages = [h.chunk.page for h in a.hits]
        cited_pages = [c.page for c in a.citations]
        if page is None:
            ok = not a.found
            totals["refusal"][0] += ok
            totals["refusal"][1] += 1
            rows.append([q, "not in handbook", "-", "-", "-", "pass" if ok else "FAIL"])
        else:
            r_ok = page in hit_pages
            a_ok = a.found and has_all(a.text, expect)
            c_ok = page in cited_pages
            blocked = a.status in ("unverified", "invalid_output")
            for key, val in (("retrieval", r_ok), ("answer", a_ok), ("citation", c_ok), ("blocked", blocked)):
                totals[key][0] += val
                totals[key][1] += 1
            rows.append([q, f"p. {page}", *("pass" if v else "FAIL" for v in (r_ok, a_ok, c_ok)), "-"])
        status = rows[-1]
        print(f"{'OK ' if 'FAIL' not in status else 'X  '} {q}")
        if "FAIL" in status:
            print(f"     answer: {a.text[:160]!r}  hits={hit_pages} cited={cited_pages}  status={a.status}")
            if a.reason:
                print(f"     why: {a.reason}")

    def pct(key):
        n, d = totals[key]
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"

    model = getattr(chat, "label", args.provider)
    summary = [
        ("Retrieval: the right page is in the top results", pct("retrieval")),
        ("Answer contains the expected facts", pct("answer")),
        ("Answer cites the right page", pct("citation")),
        ("Says \"not found\" for questions the handbook doesn't cover", pct("refusal")),
        ("Answerable questions blocked by the citation checks (lower is better)", pct("blocked")),
    ]
    lines = [
        "# Evaluation results",
        "",
        f"Model: {model} · embeddings: {embedder.name} · top-k: {top_k} · "
        f"run {dt.datetime.now():%Y-%m-%d %H:%M} · {time.time() - started:.0f}s",
        "",
        *([f"**Partial run.** {stopped}", ""] if stopped else []),
        "| Check | Score |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in summary],
        "",
        "| Question | Expected | Retrieval | Answer | Citation | Refusal |",
        "| --- | --- | --- | --- | --- | --- |",
        *["| " + " | ".join(r) + " |" for r in rows],
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print()
    for k, v in summary:
        print(f"{v:>12}  {k}")
    print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()

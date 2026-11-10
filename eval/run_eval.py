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
    s = load_settings()
    if provider == "offline":
        from rag.offline import HashingEmbedder, KeywordChat

        return HashingEmbedder(), KeywordChat(), s.top_k
    from rag.providers import ClaudeChat, OpenAIChat, OpenAIEmbedder

    if not s.openai_api_key:
        sys.exit("OPENAI_API_KEY is needed for embeddings. Add it to .env or use --provider offline.")
    embedder = OpenAIEmbedder(s.openai_api_key, s.embedding_model)
    if provider == "claude":
        if not s.anthropic_api_key:
            sys.exit("ANTHROPIC_API_KEY is not set.")
        return embedder, ClaudeChat(s.anthropic_api_key, s.claude_model), s.top_k
    return embedder, OpenAIChat(s.openai_api_key, s.openai_model), s.top_k


def has_all(text: str, expected: list[str]) -> bool:
    t = text.lower().replace(",", "")
    return all(e.lower().replace(",", "") in t for e in expected)


def main() -> None:
    s = load_settings()
    default = "claude" if s.anthropic_api_key else "openai" if s.openai_api_key else "offline"
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["claude", "openai", "offline"], default=default)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results.md"))
    args = ap.parse_args()

    embedder, chat, top_k = build(args.provider)
    store = VectorStore(embedder, "eval")
    store.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))
    pipeline = RAGPipeline(store, chat, top_k=top_k)

    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    rows, totals = [], {"retrieval": [0, 0], "answer": [0, 0], "citation": [0, 0], "refusal": [0, 0]}
    started = time.time()

    for case in cases:
        q, page, expect = case["question"], case["page"], case["expect"]
        a = pipeline.ask(q)
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
            for key, val in (("retrieval", r_ok), ("answer", a_ok), ("citation", c_ok)):
                totals[key][0] += val
                totals[key][1] += 1
            rows.append([q, f"p. {page}", *("pass" if v else "FAIL" for v in (r_ok, a_ok, c_ok)), "-"])
        status = rows[-1]
        print(f"{'OK ' if 'FAIL' not in status else 'X  '} {q}")
        if "FAIL" in status:
            print(f"     answer: {a.text[:160]!r}  hits={hit_pages} cited={cited_pages}")

    def pct(key):
        n, d = totals[key]
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"

    model = getattr(chat, "label", args.provider)
    summary = [
        ("Retrieval: the right page is in the top results", pct("retrieval")),
        ("Answer contains the expected facts", pct("answer")),
        ("Answer cites the right page", pct("citation")),
        ("Says \"not found\" for questions the handbook doesn't cover", pct("refusal")),
    ]
    lines = [
        "# Evaluation results",
        "",
        f"Model: {model} · embeddings: {embedder.name} · top-k: {top_k} · "
        f"run {dt.datetime.now():%Y-%m-%d %H:%M} · {time.time() - started:.0f}s",
        "",
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

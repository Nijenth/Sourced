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
import unicodedata
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


def ask_with_retry(pipeline, question: str, wait: float, history: list | None = None):
    """Ask once; on a provider error (e.g. a free-tier rate limit) wait and retry once, then stop cleanly."""
    from rag.providers import ProviderError

    try:
        return pipeline.ask(question, history)
    except ProviderError as exc:
        print(f"     {exc} Waiting {wait:.0f}s, then retrying once…")
        time.sleep(wait)
        try:
            return pipeline.ask(question, history)
        except ProviderError as exc2:
            raise StopEval(str(exc2)) from exc2


def _plain(text: str) -> str:
    """Normalise text for fact matching: case, commas, hyphens, every kind of space, and number words ("five" -> "5")."""
    from rag.citations import _COMPOUND, _NUMBER_WORD, _TRANSLATE, _WORDS

    # Same character table as the quote checker, so scoring and verification treat text alike.
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).lower()
    text = _COMPOUND.sub(lambda m: str(_WORDS[m.group(1)] + _WORDS[m.group(2)]), text)
    text = _NUMBER_WORD.sub(lambda m: str(_WORDS[m.group(1)]), text)
    text = text.replace(",", "").replace("-", "")
    return " ".join(text.split())


def has_all(text: str, expected: list) -> bool:
    """Every expected fact must appear. A fact can be a string or a list of acceptable alternatives."""
    t = _plain(text)
    return all(any(_plain(alt) in t for alt in ([e] if isinstance(e, str) else e)) for e in expected)


def page_metrics(gold: list[int], hit_pages: list[int], needs_all: bool = False) -> tuple[bool, float]:
    """(retrieval hit, reciprocal rank). Hit means any gold page retrieved, or all of them when needs_all."""
    found = [p in hit_pages for p in gold]
    hit = all(found) if needs_all else any(found)
    rank = next((i + 1 for i, p in enumerate(hit_pages) if p in gold), None)
    return hit, (1.0 / rank if rank else 0.0)


def gold_pages(case: dict) -> list[int]:
    if "pages" in case:
        return list(case["pages"] or [])
    return [] if case.get("page") is None else [case["page"]]


def load_cases(categories: list[str] | None) -> list[dict]:
    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    return [c for c in cases if not categories or c.get("category", "basic") in categories]


def run_ablation(args, top_k: int) -> None:
    """Retrieval-only comparison of search modes: no chat model is called, so it is free and quick."""
    from rag.providers import ProviderError, make_embedder
    from rag.store import SEARCH_MODES

    env = dict(os.environ)
    if args.provider == "offline":
        env["RAG_OFFLINE_MODE"] = "1"
    try:
        embedder = make_embedder(env)
    except ProviderError as exc:
        sys.exit(f"{exc} (or use --provider offline)")
    store = VectorStore(embedder, "ablation")
    store.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))

    all_cases = load_cases(args.category)
    # Follow-ups need the chat model to rewrite them first, and unanswerable questions have no right page.
    cases = [c for c in all_cases if gold_pages(c) and not c.get("history")]
    skipped = len(all_cases) - len(cases)

    results = {}  # mode -> list of (case, hit, reciprocal rank, pages)
    for mode in SEARCH_MODES:
        rows = []
        for c in cases:
            pages = [h.chunk.page for h in store.search(c["question"], top_k, mode=mode)]
            hit, rr = page_metrics(gold_pages(c), pages, c.get("needs_all_pages", False))
            rows.append((c, hit, rr, pages))
        results[mode] = rows

    def score(rows):
        n = len(rows) or 1
        return f"{sum(r[1] for r in rows)}/{len(rows)} ({100 * sum(r[1] for r in rows) / n:.0f}%)", \
            f"{sum(r[2] for r in rows) / n:.2f}"

    cats = sorted({c.get("category", "basic") for c in cases}, key=lambda c: (c != "basic", c))
    header = "| Search | Hit@k | MRR | " + " | ".join(cats) + " |"
    table = [header, "|" + " --- |" * (3 + len(cats))]
    print(f"\nRetrieval-only comparison · embeddings: {embedder.name} · top-k: {top_k} · "
          f"{len(cases)} questions ({skipped} follow-up/unanswerable skipped)\n")
    print(f"  {'search':<8} {'Hit@k':>12} {'MRR':>6}   per-category hits")
    for mode, rows in results.items():
        hit, mrr_ = score(rows)
        per_cat = []
        for cat in cats:
            cr = [r for r in rows if r[0].get("category", "basic") == cat]
            per_cat.append(f"{sum(r[1] for r in cr)}/{len(cr)}")
        print(f"  {mode:<8} {hit:>12} {mrr_:>6}   " + "  ".join(f"{c}:{p}" for c, p in zip(cats, per_cat)))
        table.append(f"| {mode} | {hit} | {mrr_} | " + " | ".join(per_cat) + " |")

    # Questions where the methods disagree are the interesting ones.
    diffs = []
    for i, c in enumerate(cases):
        outcomes = {m: results[m][i][1] for m in SEARCH_MODES}
        if len(set(outcomes.values())) > 1:
            found = ", ".join(m for m, ok in outcomes.items() if ok) or "none"
            diffs.append(f"- {c['question']} (right page found by: {found}; "
                         + "; ".join(f"{m} {results[m][i][3]}" for m in SEARCH_MODES) + ")")
    if diffs:
        print("\nWhere the methods disagree:")
        print("\n".join("  " + d for d in diffs))

    out = ROOT / "eval" / "ablation.md"
    out.write_text("\n".join([
        "# Retrieval ablation", "",
        f"Embeddings: {embedder.name} · top-k: {top_k} · {dt.datetime.now():%Y-%m-%d %H:%M} · "
        f"{len(cases)} answerable questions (follow-ups skipped: they need the chat model to rewrite them).", "",
        *table, "", "## Where the methods disagree", "", *(diffs or ["(none)"]), "",
    ]), encoding="utf-8")
    print(f"\nReport written to {out}")


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
    ap.add_argument("--category", action="append",
                    help="only run these categories (repeatable), e.g. --category followup --category exception")
    ap.add_argument("--retrieval", choices=["dense", "bm25", "hybrid"], default=s.retrieval_mode,
                    help="search method (default: RETRIEVAL_MODE setting, else hybrid)")
    ap.add_argument("--retrieval-only", action="store_true",
                    help="compare dense, bm25 and hybrid search without calling any AI model (free and fast)")
    args = ap.parse_args()
    if args.retrieval_only:
        run_ablation(args, s.top_k)
        return

    embedder, chat, top_k = build(args.provider)
    store = VectorStore(embedder, "eval")
    store.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))
    pipeline = RAGPipeline(store, chat, top_k=top_k, retrieval_mode=args.retrieval)

    cases = load_cases(args.category)
    results: list[dict] = []
    started = time.time()
    stopped = ""

    for i, case in enumerate(cases):
        if i and args.delay:
            time.sleep(args.delay)
        q, cat = case["question"], case.get("category", "basic")
        gold, needs_all = gold_pages(case), case.get("needs_all_pages", False)
        try:
            history: list[dict] = []
            for earlier in case.get("history", []):  # earlier turns of a follow-up conversation
                prev = ask_with_retry(pipeline, earlier, args.retry_wait, history)
                history += [{"role": "user", "content": earlier}, {"role": "assistant", "content": prev.text}]
                if args.delay:
                    time.sleep(args.delay)
            a = ask_with_retry(pipeline, q, args.retry_wait, history)
        except StopEval as exc:
            stopped = f"Stopped after {i} of {len(cases)} questions: {exc}"
            print(f"\n{stopped}")
            break
        hit_pages = [h.chunk.page for h in a.hits]
        cited_pages = [c.page for c in a.citations]
        r = {"q": q, "cat": cat, "answerable": bool(gold), "a": a}
        if gold:
            r["hit"], r["rr"] = page_metrics(gold, hit_pages, needs_all)
            r["answer_ok"] = a.found and has_all(a.text, case.get("expect", []))
            r["cite_ok"] = (all if needs_all else any)(p in cited_pages for p in gold)
            r["blocked"] = a.status in ("unverified", "invalid_output")
            r["ok"] = r["answer_ok"] and r["cite_ok"]
            r["expected"] = "p. " + ", ".join(map(str, gold))
        else:
            r["ok"] = not a.found
            r["expected"] = "not in handbook"
        results.append(r)
        label = f"[{cat}] {q}" + (f"  (after: {' / '.join(case['history'])})" if case.get("history") else "")
        print(f"{'OK ' if r['ok'] else 'X  '} {label}")
        if not r["ok"]:
            print(f"     answer: {a.text[:200]!r}  hits={hit_pages} cited={cited_pages}  status={a.status}"
                  + ("  (partial)" if a.partial else ""))
            if a.search_query != q:
                print(f"     searched for: {a.search_query!r}")
            if a.reason:
                print(f"     why: {a.reason}")
            if a.status in ("unverified", "invalid_output") and a.raw:
                print(f"     model said: {a.raw[:400]!r}")

    answerable = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]

    def frac(items, key):
        n, d = sum(1 for r in items if r[key]), len(items)
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"

    def mrr(items):
        return f"{sum(r['rr'] for r in items) / len(items):.2f}" if items else "-"

    model = getattr(chat, "label", args.provider)
    summary = [
        ("Retrieval: the right page is in the top results (Hit@k)", frac(answerable, "hit")),
        ("Retrieval: how high the right page ranks (MRR, 1.00 = always first)", mrr(answerable)),
        ("Answer contains the expected facts", frac(answerable, "answer_ok")),
        ("Answer cites the right page", frac(answerable, "cite_ok")),
        ("Says \"not found\" for questions the handbook doesn't cover", frac(unanswerable, "ok")),
        ("Answerable questions blocked by the citation checks (lower is better)", frac(answerable, "blocked")),
    ]
    categories = sorted({r["cat"] for r in results}, key=lambda c: (c != "basic", c))
    by_cat = []
    for c in categories:
        items = [r for r in results if r["cat"] == c]
        ans = [r for r in items if r["answerable"]]
        by_cat.append((c, frac(items, "ok"), frac(ans, "hit") if ans else "-", mrr(ans)))

    lines = [
        "# Evaluation results",
        "",
        f"Model: {model} · embeddings: {embedder.name} · retrieval: {args.retrieval} · top-k: {top_k} · "
        f"run {dt.datetime.now():%Y-%m-%d %H:%M} · {time.time() - started:.0f}s",
        "",
        *([f"**Partial run.** {stopped}", ""] if stopped else []),
        "| Check | Score |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in summary],
        "",
        "| Category | Fully correct | Retrieval hit | MRR |",
        "| --- | --- | --- | --- |",
        *[f"| {c} | {ok} | {hit} | {m} |" for c, ok, hit, m in by_cat],
        "",
        "| Category | Question | Expected | Result | Status |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {r['cat']} | {r['q']} | {r['expected']} | {'pass' if r['ok'] else 'FAIL'} | {r['a'].status}{' (partial)' if r['a'].partial else ''} |" for r in results],
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print()
    for k, v in summary:
        print(f"{v:>12}  {k}")
    print("\nBy category (fully correct = right facts and right page, or a correct refusal):")
    for c, ok, hit, m in by_cat:
        print(f"  {c:<12} {ok:>12}   retrieval {hit:>12}   MRR {m}")
    print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()

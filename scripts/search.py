"""Show what each search method retrieves for one question. Handy when an answer misses a fact.

    python scripts/search.py "How much notice must a Director give?"
    python scripts/search.py "..." --k 8

Uses your .env settings (embedding provider, reranker). No chat model is called.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from rag.config import load_settings  # noqa: E402
from rag.documents import chunk_pages, collapse, load_pdf  # noqa: E402
from rag.providers import ProviderError, make_embedder  # noqa: E402
from rag.rerank import make_reranker  # noqa: E402
from rag.store import SEARCH_MODES, VectorStore  # noqa: E402

PDF = ROOT / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


def main() -> None:
    s = load_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=s.top_k, help="passages to show per method (default: TOP_K)")
    ap.add_argument("--page", type=int, help="also print every stored passage of this page, in full")
    args = ap.parse_args()

    try:
        embedder = make_embedder()
        reranker = make_reranker(s.reranker, s.rerank_model)
    except ProviderError as exc:
        sys.exit(str(exc))
    store = VectorStore(embedder, "debug")
    store.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))

    print(f"Question: {args.question}\nEmbeddings: {embedder.name} · reranker: {s.reranker} · top {args.k}\n")
    runs = [(mode, store.search(args.question, args.k, mode=mode)) for mode in SEARCH_MODES]
    if reranker:
        pool = store.search(args.question, max(s.rerank_pool, args.k), mode="hybrid")
        runs.append(("hybrid+rerank", reranker.rerank(args.question, pool)[: args.k]))
    for label, hits in runs:
        print(f"{label}:")
        for rank, h in enumerate(hits, 1):
            text = collapse(h.chunk.text)
            print(f"  {rank}. p.{h.chunk.page:<3} {h.chunk.id[-8:]} score {h.score:7.3f}  {text[:80]}…")
        print()

    if args.page:
        chunks = [c for c in store._chunks if c.page == args.page]
        print(f"Page {args.page} is stored as {len(chunks)} passage(s):")
        for c in chunks:
            print(f"\n--- passage {c.id[-8:]} ({len(c.text)} characters) ---\n{c.text}")


if __name__ == "__main__":
    main()

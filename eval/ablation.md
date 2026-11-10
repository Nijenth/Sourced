# Retrieval ablation

Embeddings: ollama:nomic-embed-text · top-k: 5 · 2026-11-10 23:38 · 26 answerable questions (follow-ups skipped: they need the chat model to rewrite them).

| Search | Hit@k | MRR | basic | exact_term | exception | multi_part | paraphrase |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | 25/26 (96%) | 0.88 | 12/12 | 4/4 | 3/3 | 2/3 | 4/4 |
| bm25 | 26/26 (100%) | 0.93 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| hybrid | 26/26 (100%) | 0.90 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |

## Where the methods disagree

- I'm a Director and I'm resigning. How much notice must I give, and how soon must I return my laptop after my last day? (right page found by: bm25, hybrid; dense [15, 12, 6, 2, 6]; bm25 [15, 4, 8, 6, 12]; hybrid [15, 12, 6, 8, 4])

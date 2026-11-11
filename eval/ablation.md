# Retrieval ablation

Embeddings: ollama:nomic-embed-text · top-k: 5 · rerank pool: 20 · 2026-11-11 11:18 · 26 answerable questions (follow-ups skipped: they need the chat model to rewrite them).

| Search | Hit@k | MRR | basic | exact_term | exception | multi_part | paraphrase |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | 26/26 (100%) | 0.96 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| bm25 | 26/26 (100%) | 0.94 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| hybrid | 26/26 (100%) | 0.98 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| dense+rerank | 26/26 (100%) | 1.00 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| hybrid+rerank | 26/26 (100%) | 1.00 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |

## Where the methods disagree

- How long can I work from another country each year? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [6, 4, 5, 5, 7]; bm25 [5, 6, 13, 2, 2]; hybrid [6, 5, 7, 8, 13]; dense+rerank [5, 6, 4, 8, 7]; hybrid+rerank [5, 6, 4, 8, 7])
- Will Brightleaf pay anything toward my gym membership? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [14, 9, 11, 2, 14]; bm25 [9, 5, 11, 15, 2]; hybrid [9, 11, 2, 15, 14]; dense+rerank [9, 11, 14, 15, 2]; hybrid+rerank [9, 11, 14, 15, 2])
- My work computer got nicked on the train. What should I do? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [12, 12, 5, 15, 14]; bm25 [5, 3, 8, 2, 12]; hybrid [12, 5, 15, 14, 16]; dense+rerank [12, 12, 15, 5, 7]; hybrid+rerank [12, 12, 15, 5, 7])
- Which version of the handbook is this, and when did it take effect? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [1, 2, 16, 2, 13]; bm25 [2, 2, 1, 9, 4]; hybrid [1, 2, 2, 13, 4]; dense+rerank [1, 2, 2, 4, 9]; hybrid+rerank [1, 2, 2, 4, 9])

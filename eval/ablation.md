# Retrieval ablation

Embeddings: ollama:nomic-embed-text · top-k: 5 · rerank pool: 20 · 2026-11-11 00:54 · 26 answerable questions (follow-ups skipped: they need the chat model to rewrite them).

| Search | Hit@k | MRR | basic | exact_term | exception | multi_part | paraphrase |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense | 25/26 (96%) | 0.88 | 12/12 | 4/4 | 3/3 | 2/3 | 4/4 |
| bm25 | 26/26 (100%) | 0.93 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| hybrid | 26/26 (100%) | 0.90 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| dense+rerank | 26/26 (100%) | 0.96 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |
| hybrid+rerank | 26/26 (100%) | 0.96 | 12/12 | 4/4 | 3/3 | 3/3 | 4/4 |

## Where the methods disagree

- What notice do I have to give if I resign after probation? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [15, 4, 2, 16, 8]; bm25 [4, 15, 5, 8, 6]; hybrid [15, 4, 8, 7, 6]; dense+rerank [4, 15, 7, 8, 6]; hybrid+rerank [4, 15, 5, 7, 8])
- How long can I work from another country each year? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [6, 4, 5, 5, 7]; bm25 [5, 6, 13, 2, 8]; hybrid [6, 5, 7, 8, 13]; dense+rerank [5, 6, 4, 8, 7]; hybrid+rerank [5, 6, 4, 8, 7])
- I'm a Principal designer. How much notice do I need to give if I resign? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [15, 6, 4, 2, 14]; bm25 [15, 4, 7, 11, 10]; hybrid [15, 4, 7, 11, 6]; dense+rerank [15, 4, 7, 5, 6]; hybrid+rerank [15, 4, 7, 5, 6])
- Will Brightleaf pay anything toward my gym membership? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [14, 11, 9, 2, 15]; bm25 [9, 5, 11, 2, 3]; hybrid [9, 11, 2, 15, 14]; dense+rerank [9, 14, 11, 15, 2]; hybrid+rerank [9, 14, 11, 15, 2])
- My work computer got nicked on the train. What should I do? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [12, 12, 5, 15, 14]; bm25 [5, 3, 2, 8, 12]; hybrid [12, 5, 3, 15, 14]; dense+rerank [12, 12, 15, 5, 7]; hybrid+rerank [12, 12, 15, 5, 7])
- Is Juneteenth a day off at Brightleaf? (right page found by: dense, bm25, hybrid, dense+rerank, hybrid+rerank; dense [6, 2, 9, 15, 11]; bm25 [6, 2, 7, 6, 16]; hybrid [2, 6, 6, 9, 15]; dense+rerank [6, 6, 2, 15, 9]; hybrid+rerank [6, 6, 2, 15, 9])
- I'm a Director and I'm resigning. How much notice must I give, and how soon must I return my laptop after my last day? (right page found by: bm25, hybrid, dense+rerank, hybrid+rerank; dense [15, 12, 6, 2, 6]; bm25 [15, 4, 8, 6, 12]; hybrid [15, 12, 6, 8, 4]; dense+rerank [15, 4, 7, 6, 8]; hybrid+rerank [15, 4, 7, 6, 8])

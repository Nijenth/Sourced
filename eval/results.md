# Evaluation results

Model: GroqCloud (openai/gpt-oss-120b) · embeddings: ollama:nomic-embed-text · top-k: 5 · run 2026-11-10 23:33 · 23s

| Check | Score |
| --- | --- |
| Retrieval: the right page is in the top results (Hit@k) | 3/3 (100%) |
| Retrieval: how high the right page ranks (MRR, 1.00 = always first) | 1.00 |
| Answer contains the expected facts | 3/3 (100%) |
| Answer cites the right page | 3/3 (100%) |
| Says "not found" for questions the handbook doesn't cover | - |
| Answerable questions blocked by the citation checks (lower is better) | 0/3 (0%) |

| Category | Fully correct | Retrieval hit | MRR |
| --- | --- | --- | --- |
| followup | 3/3 (100%) | 3/3 (100%) | 1.00 |

| Category | Question | Expected | Result | Status |
| --- | --- | --- | --- | --- |
| followup | What about in San Francisco? | p. 10 | pass | answered |
| followup | And for the birthing parent? | p. 8 | pass | answered |
| followup | And for part-time staff? | p. 6 | pass | answered |

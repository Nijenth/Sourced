# Evaluation results

Model: Ollama (qwen3.6:35b-a3b-q4_K_M, local) · embeddings: ollama:nomic-embed-text · retrieval: hybrid · rerank: cross-encoder · top-k: 5 · run 2026-11-11 10:02 · 240s

| Check | Score |
| --- | --- |
| Retrieval: the right page is in the top results (Hit@k) | 7/7 (100%) |
| Retrieval: how high the right page ranks (MRR, 1.00 = always first) | 1.00 |
| Answer contains the expected facts | 7/7 (100%) |
| Answer cites the right page | 7/7 (100%) |
| Says "not found" for questions the handbook doesn't cover | - |
| Answerable questions blocked by the citation checks (lower is better) | 0/7 (0%) |

Judge (GroqCloud (openai/gpt-oss-120b)) verdicts on 7 answers: supported 7, partial 0, unsupported 0, error 0
Correct answers the judge approved: 7/7
Tampered answers (a number swapped for another from the same passage) the judge caught: 6/6

| Category | Fully correct | Retrieval hit | MRR |
| --- | --- | --- | --- |
| followup | 3/3 (100%) | 3/3 (100%) | 1.00 |
| paraphrase | 4/4 (100%) | 4/4 (100%) | 1.00 |

| Category | Question | Expected | Result | Status |
| --- | --- | --- | --- | --- |
| paraphrase | How much vacation time do I get each year? | p. 6 | pass | answered (partial) |
| paraphrase | Will Brightleaf pay anything toward my gym membership? | p. 9 | pass | answered |
| paraphrase | My work computer got nicked on the train. What should I do? | p. 12 | pass | answered |
| paraphrase | How much of the health insurance cost does the company cover for my kids? | p. 9 | pass | answered |
| followup | What about in San Francisco? | p. 10 | pass | answered |
| followup | And for the birthing parent? | p. 8 | pass | answered |
| followup | And for part-time staff? | p. 6 | pass | answered (partial) |

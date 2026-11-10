# Evaluation results

Model: Ollama (qwen3.6:35b-a3b-q4_K_M, local) · embeddings: ollama:nomic-embed-text · top-k: 5 · run 2026-11-10 22:24 · 217s

| Check | Score |
| --- | --- |
| Retrieval: the right page is in the top results | 12/12 (100%) |
| Answer contains the expected facts | 12/12 (100%) |
| Answer cites the right page | 12/12 (100%) |
| Says "not found" for questions the handbook doesn't cover | 3/3 (100%) |
| Answerable questions blocked by the citation checks (lower is better) | 0/12 (0%) |

| Question | Expected | Retrieval | Answer | Citation | Refusal |
| --- | --- | --- | --- | --- | --- |
| How many days of annual leave do full-time employees get? | p. 6 | pass | pass | pass | - |
| How many unused leave days can I carry over, and by when must I use them? | p. 6 | pass | pass | pass | - |
| How long is the probation period? | p. 4 | pass | pass | pass | - |
| What notice do I have to give if I resign after probation? | p. 4 | pass | pass | pass | - |
| How long can I work from another country each year? | p. 5 | pass | pass | pass | - |
| What is the nightly hotel limit when travelling for work? | p. 10 | pass | pass | pass | - |
| How many paid sick days do I get? | p. 7 | pass | pass | pass | - |
| How long is parental leave for a non-birthing parent? | p. 8 | pass | pass | pass | - |
| What is my yearly learning budget? | p. 11 | pass | pass | pass | - |
| Who do I tell if my laptop is stolen, and how quickly? | p. 12 | pass | pass | pass | - |
| How much does the company match on 401(k) contributions? | p. 9 | pass | pass | pass | - |
| When do performance reviews happen? | p. 13 | pass | pass | pass | - |
| What is the dress code for client meetings? | not in handbook | - | - | - | pass |
| Does Brightleaf have a pet-friendly office policy? | not in handbook | - | - | - | pass |
| What is the company's stock price today? | not in handbook | - | - | - | pass |

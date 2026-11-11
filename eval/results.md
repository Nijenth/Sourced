# Evaluation results

Model: Ollama (qwen3.6:35b-a3b-q4_K_M, local) · embeddings: ollama:nomic-embed-text · retrieval: hybrid · rerank: cross-encoder · top-k: 5 · run 2026-11-11 09:29 · 747s

| Check | Score |
| --- | --- |
| Retrieval: the right page is in the top results (Hit@k) | 29/29 (100%) |
| Retrieval: how high the right page ranks (MRR, 1.00 = always first) | 0.97 |
| Answer contains the expected facts | 29/29 (100%) |
| Answer cites the right page | 29/29 (100%) |
| Says "not found" for questions the handbook doesn't cover | 5/6 (83%) |
| Answerable questions blocked by the citation checks (lower is better) | 0/29 (0%) |

Judge (GroqCloud (openai/gpt-oss-120b)) verdicts on 30 answers: supported 28, partial 2, unsupported 0, error 0
Correct answers the judge approved: 27/29
Tampered answers (a number swapped for another from the same passage) the judge caught: 27/27

| Category | Fully correct | Retrieval hit | MRR |
| --- | --- | --- | --- |
| basic | 14/15 (93%) | 12/12 (100%) | 1.00 |
| exact_term | 4/4 (100%) | 4/4 (100%) | 0.88 |
| exception | 3/3 (100%) | 3/3 (100%) | 0.83 |
| followup | 3/3 (100%) | 3/3 (100%) | 1.00 |
| multi_part | 3/3 (100%) | 3/3 (100%) | 1.00 |
| near_miss | 3/3 (100%) | - | - |
| paraphrase | 4/4 (100%) | 4/4 (100%) | 1.00 |

| Category | Question | Expected | Result | Status |
| --- | --- | --- | --- | --- |
| basic | How many days of annual leave do full-time employees get? | p. 6 | pass | answered |
| basic | How many unused leave days can I carry over, and by when must I use them? | p. 6 | pass | answered |
| basic | How long is the probation period? | p. 4 | pass | answered |
| basic | What notice do I have to give if I resign after probation? | p. 4 | pass | answered |
| basic | How long can I work from another country each year? | p. 5 | pass | answered |
| basic | What is the nightly hotel limit when travelling for work? | p. 10 | pass | answered |
| basic | How many paid sick days do I get? | p. 7 | pass | answered |
| basic | How long is parental leave for a non-birthing parent? | p. 8 | pass | answered |
| basic | What is my yearly learning budget? | p. 11 | pass | answered |
| basic | Who do I tell if my laptop is stolen, and how quickly? | p. 12 | pass | answered |
| basic | How much does the company match on 401(k) contributions? | p. 9 | pass | answered |
| basic | When do performance reviews happen? | p. 13 | pass | answered |
| basic | What is the dress code for client meetings? | not in handbook | pass | not_found |
| basic | Does Brightleaf have a pet-friendly office policy? | not in handbook | pass | not_found |
| basic | What is the company's stock price today? | not in handbook | FAIL | answered (partial) |
| exception | What's the hotel limit per night when I'm working in New York? | p. 10 | pass | answered |
| exception | I'm a Principal designer. How much notice do I need to give if I resign? | p. 4 | pass | answered |
| exception | How much can I spend on meals per day on an international trip? | p. 10 | pass | answered |
| paraphrase | How much vacation time do I get each year? | p. 6 | pass | answered (partial) |
| paraphrase | Will Brightleaf pay anything toward my gym membership? | p. 9 | pass | answered |
| paraphrase | My work computer got nicked on the train. What should I do? | p. 12 | pass | answered |
| paraphrase | How much of the health insurance cost does the company cover for my kids? | p. 9 | pass | answered |
| exact_term | What is the deadline for submitting expenses in Ledgerly? | p. 10 | pass | answered |
| exact_term | What is the SpeakUp form used for? | p. 14, 16 | pass | answered |
| exact_term | Is Juneteenth a day off at Brightleaf? | p. 6 | pass | answered |
| exact_term | Which version of the handbook is this, and when did it take effect? | p. 1 | pass | answered |
| multi_part | I'm a Director and I'm resigning. How much notice must I give, and how soon must I return my laptop after my last day? | p. 4, 15 | pass | answered |
| multi_part | How long is probation, and can it be extended? If so, by how much? | p. 4 | pass | answered |
| multi_part | If I'm off sick for a long time, how much of my salary does short-term disability pay, and for how long? | p. 7 | pass | answered |
| followup | What about in San Francisco? | p. 10 | pass | answered |
| followup | And for the birthing parent? | p. 8 | pass | answered |
| followup | And for part-time staff? | p. 6 | pass | answered (partial) |
| near_miss | What is the name of Brightleaf's dental insurance provider? | not in handbook | pass | not_found |
| near_miss | How many vacation days do interns get? | not in handbook | pass | not_found |
| near_miss | What time does the Austin studio open on Saturdays? | not in handbook | pass | not_found |

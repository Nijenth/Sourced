# Full-stack brief: Sourced for clients

**Audience:** a coding agent (Codex) or developer building the production version.
**Read first:** `AGENTS.md` (ownership and rules), `README.md` (what the engine does and how well).

## 1. Goal

Turn the Sourced engine (`rag/`) into a product a client can use with **their own private documents**: user accounts, documents that persist and stay private per user, a polished web interface, and safe operation for many users at once.

The engine is done and measured (35/35 on the evaluation, MRR 1.00, 137 tests). **This work is the product around it, not a new engine.** Reuse `rag/` as a library; don't re-implement retrieval, prompting or the answer checks.

## 2. Architecture

```
frontend/   React + TypeScript (Vite)          → talks only to the API
backend/    FastAPI (Python 3.12)               → imports rag/ as a library
  app/api/        routes
  app/auth/       sign-in, sessions
  app/db/         SQLAlchemy models, Alembic migrations
  app/store.py    PgVectorStore: the engine's store interface on Postgres + pgvector
  app/worker.py   background ingestion jobs
deploy/     docker-compose.yml: api, worker, postgres (pgvector), frontend
```

### Using the engine (no changes to `rag/`)

| Need | Use |
| --- | --- |
| Read a PDF | `rag.documents.load_pdf(bytes, display_name)` → pages (tables already rebuilt), `count_pages`, `file_hash` |
| Split into passages | `rag.documents.chunk_pages(pages)` → `Chunk(id, source, page, text)` |
| Providers | `rag.providers`: `chat_provider_names()`, `make_chat(name)`, `make_embedder()`, `setup_problems()`, `PROVIDERS` (labels) |
| Answer a question | `rag.pipeline.RAGPipeline(store, chat, top_k, retrieval_mode, reranker, rerank_pool, judge, judge_action).ask(question, history)` → `rag.citations.Answer` |
| Rerank / judge | `rag.rerank.make_reranker(name, model)`, `rag.judge.Judge(chat)` |
| Keyword search pieces | `rag.lexical.BM25`, `rag.lexical.reciprocal_rank_fusion` |

`RAGPipeline` only calls `store.search(query, k, mode)`, which returns `list[Hit]` (`Hit(chunk, score)`), best first, where `mode` is `"dense"`, `"bm25"` or `"hybrid"`. **Implement `PgVectorStore` in `backend/` with that method**, scoped to one user's documents:
- `dense`: pgvector cosine search over the user's chunks (the query embedded with the same embedder; use `embedder.embed_query` when it exists, else `embed([q])[0]`).
- `bm25`: either Postgres full-text search or the engine's `BM25` over the user's chunks (fine for small libraries; document the limit).
- `hybrid`: both, fused with `reciprocal_rank_fusion`, exactly as `rag/store.py` does.

**Caveat:** `Chunk.id` is derived from the display filename, page and position, so it is **not globally unique** (two users can upload "handbook.pdf"). Key stored chunks by `(document_id, chunk.id)`, or assign your own UUID and keep `chunk.id` as a column.

### Answer → API mapping

Return everything the Streamlit app shows today (see `answer_to_message` and `render_answer` in `app.py`):

```json
{
  "status": "answered | not_found | unverified | invalid_output",
  "text": "Full-time employees get 24 days… [1]",
  "partial": false,
  "searched_for": "How many days of annual leave do part-time employees get?",
  "reason": "",
  "citations": [{"number": 1, "document_id": "…", "source": "handbook.pdf", "page": 6, "quote": "…", "verified": true}],
  "passages": [{"document_id": "…", "source": "…", "page": 4, "text": "…"}],
  "judgement": {"verdict": "supported | partial | unsupported | error", "problems": [], "judge": "GroqCloud"}
}
```
`passages` is filled only for blocked answers (`unverified`, `invalid_output`); then `text` is the standard "couldn't verify" message and the model's own text must never be sent to the browser.

## 3. API (v1)

| Method and path | Purpose | Notes |
| --- | --- | --- |
| `POST /api/auth/signup`, `/login`, `/logout`, `GET /api/auth/me` | Accounts | Email + password (argon2 hashes), HTTP-only secure session cookie. Design so SSO/OIDC can be added later. |
| `GET /api/providers` | Chat providers ready to use | `[{name, label, ready}]` from the registry; never return keys |
| `POST /api/documents` | Upload a PDF | Validate size and page count **before** parsing; store the file; enqueue ingestion; return `202 {id, status: "queued"}` |
| `GET /api/documents` | The user's documents | `id, filename, pages, status (queued/processing/ready/failed), error, created_at` |
| `GET /api/documents/{id}` | One document | 404 if not the caller's |
| `DELETE /api/documents/{id}` | Delete | Removes the file, chunks and vectors; idempotent |
| `GET /api/documents/{id}/pages/{n}.png` | Page preview | Rendered with pypdfium2, size-capped, cached; ownership checked |
| `POST /api/chat` | Ask | `{question, history[], provider, document_ids?}` → the answer JSON above. Searches only the caller's ready documents (optionally a subset). |
| `GET /api/health` | Liveness | No secrets, no provider calls |

Streaming answers are out of scope for v1 (the answer must pass its checks before anything is shown).

## 4. Data model

- `users(id, email unique, password_hash, created_at)`
- `documents(id, owner_id → users, filename, sha256, pages, status, error, storage_key, embedding_model, created_at, deleted_at)`
- `chunks(id uuid, document_id → documents ON DELETE CASCADE, engine_chunk_id, page, ordinal, text, embedding vector(d), tsv tsvector)` with a pgvector index and a full-text index
- Record the embedding model per document. If `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL` changes, re-index (a job) instead of mixing vectors.

## 5. Security requirements (from the project audit)

1. **Ownership on every access:** documents, chunks, page previews, chat retrieval and deletion all filter by the signed-in user. Add tests where user B tries to read, search, preview and delete user A's document.
2. **Upload limits before expensive work:** max bytes, max pages, max extracted characters, parse timeout, and a cap on rendered preview size. Parsing happens in the worker, never in the request.
3. **Rate and cost limits per user:** questions per day, uploads per day, concurrent jobs. Configurable via environment variables.
4. **Provider disclosure:** the upload screen says which provider indexes the text and that relevant passages go to the chosen chat model (see `upload_notice` in `app.py`).
5. **Safe rendering:** answers, quotes, filenames and problems are plain text in React. No `dangerouslySetInnerHTML`, no auto-linking of model output.
6. **Errors:** users see the engine's friendly messages (`ProviderError`); stack traces and raw provider errors go to logs only.
7. **Secrets** from environment variables only; CORS restricted to the frontend origin; secure, HTTP-only, SameSite cookies; CSRF protection on state-changing routes.
8. **Deletion is real:** file, chunks and vectors are gone after `DELETE`; a test proves search can't find them.

## 6. Frontend

One chat page and one documents page.
- **Chat:** messages with `[n]` markers that open a source drawer (file, page, quote, "show the page" preview); the judge badge ("✓ Accuracy check (GroqCloud)…", or why it couldn't run); the "only partly answers" note; the "Searched for: …" line when it differs; blocked answers show "couldn't verify" plus the closest passages.
- **Model picker:** providers from `/api/providers`, each marked ready or not (design reference: a dropdown with provider names and a status badge). Only call a provider "Connected" after a real check; otherwise "Ready".
- **Documents:** upload (with the disclosure notice), status per file, delete with confirmation.
- Accessible: keyboard navigation, focus management after sending, sufficient contrast, labels on every control. Works at phone width.

## 7. Milestones and acceptance criteria

| # | Deliverable | Done when |
| --- | --- | --- |
| M1 | FastAPI skeleton: `/health`, `/providers`, `/chat` on the bundled sample handbook (in-memory, no auth) | API tests pass with `RAG_OFFLINE_MODE=1`; `/chat` returns the JSON above for an answered, a not-found and a blocked case |
| M2 | React chat UI on M1 | Citations, source drawer with page preview, judge badge, partial note, searched-for line and blocked-answer view all work against the offline API |
| M3 | Postgres + pgvector, `PgVectorStore`, ingestion worker, document endpoints, auth | Two-user isolation tests pass; delete test passes; hybrid search on pgvector matches the engine's retrieval-only results on the sample (`eval/run_eval.py --retrieval-only` numbers within a small tolerance, measured with a script that uses `PgVectorStore`) |
| M4 | Hardening: limits, rate limits, safe errors, CSRF/CORS, logging with request IDs and per-stage timings | Tests for each limit; no raw provider error reaches the browser |
| M5 | `deploy/docker-compose.yml` and a short deploy guide | `docker compose up` gives a working app from a clean machine with only `.env` filled in |

## 8. Testing

- Backend: pytest with FastAPI's test client and the engine's offline stand-ins (`rag.offline.HashingEmbedder`, `KeywordChat`), so CI needs no keys. A Postgres service (testcontainers or a CI service) for M3+.
- Frontend: Vitest + Testing Library for components; one Playwright smoke test (sign up, upload, ask, open a source, delete).
- The existing `pytest` suite and `python eval/run_eval.py --provider offline` must stay green.
- Add a CI workflow (GitHub Actions) that runs all of the above on every push.

## 9. Out of scope for v1

New AI providers; changes to the answer checks, prompts or retrieval (request them in `docs/engine-requests.md`); streaming; OCR; non-PDF formats; billing; multi-tenant organisations (single user per account is enough for v1).

## 10. Known engine requests (not yet done)

These would help the product; ask for them rather than patching `rag/`:
- Report token usage and timings per stage from providers and the pipeline (for cost tracking and monitoring).
- A relevance check in the judge ("does the answer address the question asked?").
- A store interface written down as a `Protocol` in `rag/store.py`, so `PgVectorStore` can be type-checked against it.

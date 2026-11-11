# Full-stack brief: Sourced for clients

**Audience:** a coding agent (Codex) or developer building the production version.
**Read first:** `AGENTS.md` (ownership and rules), `README.md` (what the engine does and how well), `docs/design/README.md` (the screens to build).

**Status:** M1–M7 are implemented. Deployment is described in `docs/deploy.md`.

## 1. Goal

Turn the Sourced engine (`rag/`) into a product a business can use with **its own private documents**, delivered **API-first**: one backend that every channel talks to.

- **Admin dashboard** (web): the client uploads documents, tests answers, sets up channels and sees usage.
- **Website widget**: a chat bubble the client adds to *their own* website with one `<script>` tag.
- **Mobile apps**: the same chat inside the client's Android or iOS app (hosted chat page in a WebView, or their own screen calling the API).
- **Later:** Slack / Microsoft Teams bots on the same API (out of scope for v1, but don't design it out).

The chat is a feature inside the client's products, not a destination site. The engine is done and measured (35/35 on the evaluation, MRR 1.00, 137 tests). **This work is the product around it, not a new engine.** Reuse `rag/` as a library; don't re-implement retrieval, prompting or the answer checks.

## 2. Architecture

```
backend/    sourced-api: FastAPI (Python 3.12), imports rag/ as a library
  app/api/admin/    dashboard routes (session cookie)
  app/api/public/   channel routes (site key or session token, no cookies)
  app/auth/         sign-in, sessions, site keys, secret keys, session tokens
  app/db/           SQLAlchemy models, Alembic migrations
  app/store.py      PgVectorStore: the engine's store interface on Postgres + pgvector
  app/chat.py       one ask() service used by every channel
  app/worker.py     background ingestion jobs
frontend/   sourced-web: React + TypeScript (Vite) admin dashboard
widget/     sourced-widget: the embeddable chat (TypeScript, no framework or Preact),
            built to one small widget.js; the same UI also serves the hosted chat page
demo/       a static fake-company page with the widget installed (the public demo)
deploy/     docker-compose.yml: api, worker, postgres (pgvector), web, widget assets
```

Every channel goes through `app/chat.py`, which picks the workspace, the allowed documents, the provider and the accuracy-check setting **on the server**. Callers never choose documents or providers on a public channel.

### Using the engine (no changes to `rag/`)

| Need | Use |
| --- | --- |
| Read a PDF | `rag.documents.load_pdf(bytes, display_name)` → pages (tables already rebuilt), `count_pages`, `file_hash` |
| Split into passages | `rag.documents.chunk_pages(pages)` → `Chunk(id, source, page, text)` |
| Providers | `rag.providers`: `chat_provider_names()`, `make_chat(name)`, `make_embedder()`, `setup_problems()`, `PROVIDERS` (labels) |
| Answer a question | `rag.pipeline.RAGPipeline(store, chat, top_k, retrieval_mode, reranker, rerank_pool, judge, judge_action).ask(question, history)` → `rag.citations.Answer` |
| Rerank / judge | `rag.rerank.make_reranker(name, model)`, `rag.judge.Judge(chat)` |
| Keyword search pieces | `rag.lexical.BM25`, `rag.lexical.reciprocal_rank_fusion` |

`RAGPipeline` only calls `store.search(query, k, mode)`, which returns `list[Hit]` (`Hit(chunk, score)`), best first, where `mode` is `"dense"`, `"bm25"` or `"hybrid"`. **Implement `PgVectorStore` in `backend/` with that method**, scoped to a given set of document ids within one workspace:
- `dense`: pgvector cosine search (the query embedded with the same embedder; use `embedder.embed_query` when it exists, else `embed([q])[0]`).
- `bm25`: either Postgres full-text search or the engine's `BM25` over the allowed chunks (fine for small libraries; document the limit).
- `hybrid`: both, fused with `reciprocal_rank_fusion`, exactly as `rag/store.py` does.

**Caveat:** `Chunk.id` is derived from the display filename, page and position, so it is **not globally unique** (two workspaces can upload "handbook.pdf"). Key stored chunks by `(document_id, chunk.id)`, or assign your own UUID and keep `chunk.id` as a column.

### Answer → API mapping

Every channel gets the same JSON (see `answer_to_message` and `render_answer` in `app.py` for what the demo shows today):

```json
{
  "status": "answered | not_found | unverified | invalid_output",
  "text": "Full-time employees get 24 days… [1]",
  "partial": false,
  "searched_for": "How many days of annual leave do part-time employees get?",
  "reason": "",
  "fallback": "Try the People team: people@example.com",
  "citations": [{"number": 1, "document_id": "…", "source": "handbook.pdf", "page": 6, "quote": "…", "verified": true, "preview_url": "…"}],
  "passages": [{"document_id": "…", "source": "…", "page": 4, "text": "…"}],
  "judgement": {"verdict": "supported | partial | unsupported | error", "problems": [], "judge": "GroqCloud"}
}
```
- `passages` is filled only for blocked answers (`unverified`, `invalid_output`) **on the admin API**; then `text` is the standard "couldn't verify" message and the model's own text must never be sent to any client.
- On **public** channels, blocked answers return the "couldn't verify" message plus `fallback`, with no passages. `fallback` is the widget's "when the documents don't cover a question" text and is also sent with `not_found`.
- `preview_url` on public channels is a signed, short-lived URL for that cited page only (see security 12).

## 3. API (v1)

All paths start with `/api/v1`.

### Admin API (dashboard; session cookie)

| Method and path | Purpose | Notes |
| --- | --- | --- |
| `POST /auth/signup`, `/auth/login`, `/auth/logout`, `GET /auth/me` | Accounts | Email + password (argon2 hashes), HTTP-only secure session cookie. Sign-up creates a workspace. Design so SSO/OIDC can be added later. |
| `GET /providers` | Chat providers ready to use | `[{name, label, ready}]` from the registry; never return keys |
| `POST /documents` | Upload a PDF | Validate size and page count **before** parsing; store the file; enqueue ingestion; return `202 {id, status: "queued"}` |
| `GET /documents`, `GET /documents/{id}` | The workspace's documents | `id, filename, pages, status (queued/processing/ready/failed), progress, error, created_at`; 404 if not in the caller's workspace |
| `DELETE /documents/{id}` | Delete | Removes the file, chunks and vectors, and removes it from every widget; idempotent |
| `GET /documents/{id}/pages/{n}.png` | Page preview | Rendered with pypdfium2, size-capped, cached; workspace checked |
| `POST /chat` | Ask (test) | `{question, history[], provider, document_ids?}` → the answer JSON. Searches only the workspace's ready documents. |
| `GET/POST /widgets`, `GET/PATCH/DELETE /widgets/{id}` | Channel settings | Fields in section 4. A workspace can have several widgets (e.g. intranet and public help site). |
| `POST /widgets/{id}/rotate-keys` | New site key and/or secret key | The secret key is shown **once** in the response and stored hashed |
| `GET /usage` | Counts per day per channel | questions, answered, not found, blocked |
| `GET /health` | Liveness | No secrets, no provider calls (path `/api/v1/health`) |

### Public API (widget, hosted page, mobile; no cookies)

| Method and path | Purpose | Notes |
| --- | --- | --- |
| `GET /public/widgets/{site_key}/config` | Title, brand colour, greeting, fallback, access mode | No secrets, no document names |
| `POST /public/chat` | Ask | Header `X-Sourced-Key: pk_…` (public mode) or `Authorization: Bearer <session token>` (signed-in mode). Body `{question, history[]}` with length caps. Provider, documents and judge come from the widget's settings. |
| `GET /public/previews/{signed_id}.png` | Page preview of a cited page | Only via `preview_url` from a chat response; expires |
| `POST /widgets/{id}/session-tokens` | Server-to-server: issue a short-lived token for one end user | Called by the **client's server** with `Authorization: Bearer sk_…`. Body `{user_ref?}` → `{token, expires_in}` (default 15 minutes). The token only allows `/public/chat` and previews for that widget. |

### Hosted chat page

`GET /c/{site_key}` serves the widget UI full screen, for mobile WebViews and direct links. In signed-in mode the app passes the session token in the URL **fragment** (`/c/pk_…#token=…`), never the query string, so it doesn't reach server logs; the page reads it, then clears the fragment.

Streaming answers are out of scope for v1 (the answer must pass its checks before anything is shown).

## 4. Data model

- `workspaces(id, name, created_at)`; `users(id, email unique, password_hash, created_at)`; `memberships(user_id, workspace_id, role)`. One admin per workspace is enough for v1, but keep the table.
- `documents(id, workspace_id, filename, sha256, pages, status, progress, error, storage_key, embedding_model, created_at, deleted_at)`
- `chunks(id uuid, document_id → documents ON DELETE CASCADE, engine_chunk_id, page, ordinal, text, embedding vector(d), tsv tsvector)` with a pgvector index and a full-text index
- `widgets(id, workspace_id, name, site_key unique, secret_key_hash, access_mode 'public' | 'signed_in', allowed_origins text[], provider, judge_enabled, title, greeting, brand_color, fallback_text, created_at)` and `widget_documents(widget_id, document_id)`
- `usage_daily(workspace_id, widget_id nullable, day, questions, answered, not_found, blocked)`
- **No visitor messages are stored in v1**, only counts. If chat history is added later, it needs a retention setting and a delete path.
- Record the embedding model per document. If `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL` changes, re-index (a job) instead of mixing vectors.

## 5. Security requirements

From the project audit:
1. **Workspace scoping on every access:** documents, chunks, page previews, chat retrieval, widgets and deletion all filter by the caller's workspace. Add tests where workspace B tries to read, search, preview, delete and attach to a widget workspace A's document.
2. **Upload limits before expensive work:** max bytes, max pages, max extracted characters, parse timeout, and a cap on rendered preview size. Parsing happens in the worker, never in the request.
3. **Rate and cost limits:** per admin user (questions, uploads, concurrent jobs) and per widget and per IP on public channels, plus a daily question cap per workspace. All configurable via environment variables.
4. **Provider disclosure:** the upload screen says which provider indexes the text and that relevant passages go to the chosen chat model (see `upload_notice` in `app.py`).
5. **Safe rendering:** answers, quotes, filenames and problems are plain text in every UI (React, widget, hosted page). No `dangerouslySetInnerHTML`/`innerHTML`, no auto-linking of model output.
6. **Errors:** users see the engine's friendly messages (`ProviderError`); stack traces and raw provider errors go to logs only.
7. **Secrets** from environment variables only; secure, HTTP-only, SameSite cookies; CSRF protection on state-changing admin routes.
8. **Deletion is real:** file, chunks and vectors are gone after `DELETE`; a test proves no channel can find them.

For the channels:

9. **Site keys are public, not secrets.** The allowed-origins check stops other websites from embedding the widget in a browser, but anyone can call the API directly with a forged `Origin`. Real protection is the rate and cost limits (3) and **signed-in mode**. The settings screen must say that documents in a public-mode widget are effectively public. Internal content (like an employee handbook) belongs in signed-in mode.
10. **Signed-in mode:** `/public/chat` rejects site-key-only calls; it needs a valid, unexpired session token for that widget. Tokens are signed (HMAC with a server secret), carry `widget_id`, `exp` and the optional `user_ref`, and can't be used on admin routes.
11. **Secret keys** (`sk_…`) are shown once, stored hashed, can be rotated, and must never be put in a browser or a mobile app; the docs and the settings screen say so.
12. **Public previews** only for pages that were cited in a response to that caller, via signed URLs that expire (e.g. 10 minutes).
13. **CORS:** admin routes allow only the dashboard origin, with credentials. Public routes allow only that widget's allowed origins, without credentials.
14. **Widget isolation:** the widget renders inside a Shadow DOM, sets text with `textContent`, uses no cookies or `localStorage` for anything sensitive, reads nothing from the host page, and needs no `eval` or inline scripts (works under a strict CSP). Serve `widget.js` from a versioned URL and offer an SRI hash in the snippet.

## 6. Frontend

**The screens are in `docs/design/`** (see its README). Match their layout, wording and states; the colours and fonts there are the starting brand.

**Admin dashboard** (`frontend/`):
- **Documents:** upload with the provider disclosure, status per file (ready, processing with progress, couldn't read), delete with confirmation.
- **Ask (test):** model picker from `/providers` (marked Ready, or Connected only after a real check), answers with numbered citations, the source panel (file, page, verified quote, page preview with the quote highlighted when possible, link to the full page), the accuracy-check badge with the judge's name, the partial note, the "Searched for" line, and the blocked-answer view with the closest passages.
- **Website widget:** the embed snippet with copy button, allowed websites, access mode (public or signed-in), which documents it may answer from (processing ones disabled), answer model, accuracy check toggle, fallback text, title and brand colour with a live preview, key rotation.
- **Usage:** a simple table or chart of the counts in `usage_daily`.
- Accessible: keyboard navigation, focus management after sending, sufficient contrast, labels on every control, 44 px touch targets. Works at phone width.

**Widget** (`widget/`): the bubble and panel from `docs/design/Widget.dc.html` in the widget's brand colour; numbered citations; a source chip that opens the quote and page preview; "Checked against the source" when the judge passed; not-found and blocked answers show the fallback text; "Powered by Sourced". Keep `widget.js` small (target under 60 KB gzipped) and load it asynchronously.

**Hosted page / mobile:** the same UI full screen, sized for phones, with sources in a bottom sheet (`docs/design/MobileChat.dc.html`). Inputs at least 16 px so iOS doesn't zoom.

## 7. Milestones and acceptance criteria

| # | Deliverable | Done when |
| --- | --- | --- |
| M1 | FastAPI skeleton: `/api/v1/health`, `/api/v1/providers`, `/api/v1/chat` on the bundled sample handbook (in-memory, no auth) | API tests pass with `RAG_OFFLINE_MODE=1`; `/chat` returns the JSON above for an answered, a not-found and a blocked case |
| M2 | Postgres + pgvector, `PgVectorStore`, ingestion worker, workspaces, auth, document endpoints | Two-workspace isolation tests pass; delete test passes; hybrid search on pgvector matches the engine's retrieval-only results on the sample (`eval/run_eval.py --retrieval-only` numbers within a small tolerance, measured with a script that uses `PgVectorStore`) |
| M3 | Admin dashboard: Documents, Ask (test), Usage | Every state in the design works against the API (ready, processing, failed upload; answered, partial, not found, blocked; judge passed and failed); Vitest component tests; keyboard-only walkthrough works |
| M4 | Website widget: widget endpoints and settings screen, public chat, keys, allowed origins, public-channel limits, `widget.js`, hosted chat page, and `demo/` (a fake-company page with the widget, using the sample handbook) | Playwright on `demo/`: open the bubble, ask, see a citation, open the source; a disallowed origin is refused; the rate limit triggers; a widget only searches its own documents; blocked answers never leak model text |
| M5 | Mobile: signed-in mode, session tokens, `docs/mobile.md` with a WebView example for Android (Kotlin) and iOS (Swift) and a server example that issues tokens | Tests: expired, wrong-widget and missing tokens are refused; a site key alone is refused in signed-in mode; the token never appears in server access logs |
| M6 | Hardening: remaining limits, safe errors, CSRF/CORS, logging with request IDs and per-stage timings, CI | Tests for each limit; no raw provider error reaches any client; GitHub Actions runs everything on every push |
| M7 | `deploy/docker-compose.yml`, a short deploy guide, and the public demo deployed | `docker compose up` gives a working system (dashboard, widget, demo page) from a clean machine with only `.env` filled in |

Open one pull request per milestone, on its own branch (`fullstack-m1`, `fullstack-m2`, …).

## 8. Testing

- Backend: pytest with FastAPI's test client and the engine's offline stand-ins (`rag.offline.HashingEmbedder`, `KeywordChat`), so CI needs no keys. A Postgres service (testcontainers or a CI service) from M2.
- Frontend and widget: Vitest + Testing Library for components; Playwright for the admin smoke test (sign up, upload, ask, open a source, delete) and the widget test on `demo/`.
- The existing `pytest` suite and `python eval/run_eval.py --provider offline` must stay green.

## 9. Out of scope for v1

Slack/Teams bots; native mobile SDKs (React Native, Flutter); new AI providers; changes to the answer checks, prompts or retrieval (request them in `docs/engine-requests.md`); streaming; OCR; non-PDF formats; billing; storing visitor chat history.

## 10. Known engine requests (not yet done)

These would help the product; ask for them rather than patching `rag/`:
- Report token usage and timings per stage from providers and the pipeline (for cost tracking and monitoring).
- A relevance check in the judge ("does the answer address the question asked?").
- A store interface written down as a `Protocol` in `rag/store.py`, so `PgVectorStore` can be type-checked against it.

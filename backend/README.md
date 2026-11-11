# Sourced workspace API

Run these commands from the repository root with Python 3.12. Set `SOURCED_DATABASE_URL` to the Postgres connection URL in both the API and worker environments. The migration needs pgvector available on the server and permission to install it if the extension is absent. For a local run with keyword stand-ins, set `RAG_OFFLINE_MODE=1` in both environments.

```sh
python -m pip install -r backend/requirements.txt
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn backend.app.main:app --no-proxy-headers --ssl-keyfile path/to/key.pem --ssl-certfile path/to/cert.pem
```

Start the worker in a separate shell with the same database URL and embedding settings.

```sh
python -m backend.app.worker
```

Use HTTPS so clients send the Secure cookies. All routes below use the `/api/v1` prefix.

| Route | Request and result |
| --- | --- |
| `POST /auth/signup` | Send JSON with `email`, `password` (12–128 characters), and an optional `workspace_name`. Creates a workspace and signs in its administrator. |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Start, end, or inspect a 12-hour session. |
| `POST /documents` | Send one PDF as multipart form field `file`. Returns `202` and the queued document ID. |
| `GET /documents`, `GET /documents/{id}` | Read workspace document status and ingestion progress. |
| `GET /documents/settings` | Read upload limits and the configured embedding provider label. |
| `GET /documents/{id}/pages/{page}.png` | Read a PNG of a page in a ready document. Page numbers start at 1. |
| `GET /documents/{id}/pdf` | Open the original PDF for a ready document in the workspace. |
| `POST /documents/{id}/pages/{page}/highlight` | Render the page with a highlight when its text matches the supplied quote. |
| `DELETE /documents/{id}` | Delete a document, its file, vectors, and cached previews. Repeated deletion returns `204`. |
| `POST /chat` | Ask over the workspace's ready documents. Optionally pass `document_ids` to limit retrieval to a subset. |
| `GET /usage` | Read counts of completed answers by UTC day and channel. The optional `days` parameter defaults to 30 and accepts 1–90. |

Keep the session and CSRF cookies from signup or login. Send the value of `__Host-sourced_csrf` in the `X-CSRF-Token` header for upload, deletion, highlighted previews, chat, and logout. Only the liveness endpoint and account creation and sign-in are available without a session. Usage stores counts only.

PDFs are stored in Postgres under their document IDs. Defaults allow 10 MiB per PDF, 80 pages, and 500,000 extracted characters. The page count, extraction, and preview operations have a 20-second timeout per operation. Previews are capped at 1,400 pixels on their longest side. Configure the limits with `MAX_UPLOAD_MB`, `MAX_UPLOAD_PAGES`, `SOURCED_MAX_PDF_CHARS`, and `SOURCED_PDF_TIMEOUT_SECONDS`.

The worker picks up queued uploads and can reclaim a job after its 15-minute lease expires. A job gets up to three attempts per upload or reindexing run. Invalid PDFs and other known errors can fail immediately with a document status message. When the embedding model changes, the worker reindexes ready documents from their stored PDFs. Retrieval excludes documents indexed by a different model until they are ready again. Run the API and all workers with the same embedding configuration.

Hybrid search uses exact pgvector cosine distance on the allowed chunks and the engine's BM25 and rank fusion. Keyword and hybrid searches support up to 10,000 chunks per request. Select fewer documents or configure dense search for larger libraries.

## Website widgets

Run the latest migrations and set `SOURCED_PUBLIC_ORIGIN` to the HTTPS origin that serves the public API, widget assets, and hosted chat. Set `SOURCED_PREVIEW_SECRET` to a random server-side value of at least 32 bytes. Use the same secret across API instances. Set `SOURCED_DASHBOARD_ORIGIN` only if the dashboard needs a separate CORS origin. The same-origin dashboard requires no CORS setting.

The admin widget settings and rotation routes use the workspace session and require CSRF on writes. Public site keys are safe to embed. A rotated site key invalidates the previous snippets and hosted links. If an administrator generates or rotates a server secret key via `POST /api/v1/widgets/{id}/rotate-keys`, the `sk_` value appears once in that response and is stored as a digest. Keep it on the client’s server.

For a signed-in widget, the client’s server calls `POST /api/v1/widgets/{id}/session-tokens` with the widget’s server secret in the Authorization bearer header and an optional `user_ref` in the JSON body. This endpoint issues a 15-minute token scoped to that widget. It uses the configured signing secret in a separate HMAC context from preview grants. Changing access mode or rotating either key revokes existing sessions. See the [mobile integration guide](../docs/mobile.md).

Public-mode chat accepts the key in `X-Sourced-Key` and checks the exact `Origin`. Signed-in chat requires an Authorization bearer session token. Browser requests for either mode use the public key in the `site_key` query parameter so the preflight can look up the widget. The Sourced origin and hosted link require per-widget opt-in. Origin checks control browser access. Direct callers can forge an Origin header, so treat documents attached to public-mode widgets as public.

Public questions count against `SOURCED_WIDGET_REQUESTS_PER_MINUTE` and `SOURCED_IP_REQUESTS_PER_MINUTE`. `SOURCED_WORKSPACE_QUESTIONS_PER_DAY` counts admitted admin and public questions together and resets at midnight UTC. The defaults are 20 per widget per minute, 10 per IP per minute, and 300 per workspace per day.

Run Uvicorn with `--no-proxy-headers` so Sourced receives the direct connection address. Behind a proxy, list its IP or CIDR in the comma-separated `SOURCED_TRUSTED_PROXIES` setting. Each trusted proxy must append the address it received the request from. Sourced walks `X-Forwarded-For` from right to left past trusted proxies and uses the first untrusted IP. The default trust list is empty. IPv6 clients share a /64 identity for rate limits and previews.

Cited-page previews expire after ten minutes. The grant binds the current site key, access version, citation’s page, and requesting IP and origin. Signed-in previews also require the same valid session token that received the citation. Detaching or deleting the document invalidates its previews. Public chat, previews, and token issuance ignore dashboard session cookies.

## Verify locally

Run Docker with Linux containers enabled. Tests create a disposable Postgres container on a random loopback port, verify its identity, run Alembic, and stop it afterward. The test connection comes from that container.

When Docker is unavailable, pytest skips the Postgres-backed tests and still runs the engine and other backend tests. Use `pytest -rs` to see the skip reason. Set `SOURCED_REQUIRE_DB_TESTS=1` in CI to make Docker unavailability a test error.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
python -m backend.tests.retrieval_parity
```

The retrieval comparison uses the engine evaluation's answerable, non-follow-up handbook questions and the same offline embeddings in both stores. It compares hybrid Hit@5 and mean reciprocal rank.

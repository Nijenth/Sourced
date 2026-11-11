# Sourced workspace API

Run these commands from the repository root with Python 3.12. Set `SOURCED_DATABASE_URL` to the Postgres connection URL in both the API and worker environments. The migration needs pgvector available on the server and permission to install it if the extension is absent. For a local run with keyword stand-ins, set `RAG_OFFLINE_MODE=1` in both environments.

```sh
python -m pip install -r backend/requirements.txt
python -m alembic -c backend/alembic.ini upgrade head
python -m uvicorn backend.app.main:app --ssl-keyfile path/to/key.pem --ssl-certfile path/to/cert.pem
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
| `GET /documents/{id}/pages/{page}.png` | Read a PNG of a page in a ready document. Page numbers start at 1. |
| `DELETE /documents/{id}` | Delete a document, its file, vectors, and cached previews. Repeated deletion returns `204`. |
| `POST /chat` | Ask over the workspace's ready documents. Optionally pass `document_ids` to limit retrieval to a subset. |

Keep the session and CSRF cookies from signup or login. Send the value of `__Host-sourced_csrf` in the `X-CSRF-Token` header for upload, deletion, chat, and logout. Only the liveness endpoint and account creation and sign-in are available without a session.

PDFs are stored in Postgres under their document IDs. Defaults allow 10 MiB per PDF, 80 pages, and 500,000 extracted characters. The page count, extraction, and preview operations have a 20-second timeout per operation. Previews are capped at 1,400 pixels on their longest side. Configure the limits with `MAX_UPLOAD_MB`, `MAX_UPLOAD_PAGES`, `SOURCED_MAX_PDF_CHARS`, and `SOURCED_PDF_TIMEOUT_SECONDS`.

The worker picks up queued uploads and can reclaim a job after its 15-minute lease expires. When the embedding model changes, the worker reindexes ready documents from their stored PDFs. Retrieval excludes documents indexed by a different model until they are ready again. Run the API and all workers with the same embedding configuration.

Hybrid search uses exact pgvector cosine distance on the allowed chunks and the engine's BM25 and rank fusion. Keyword and hybrid searches support up to 10,000 chunks per request. Select fewer documents or configure dense search for larger libraries.

## Verify locally

Run Docker with Linux containers enabled. Tests create a disposable Postgres container on a random loopback port, verify its identity, run Alembic, and stop it afterward. The test connection comes from that container.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
python -m backend.tests.retrieval_parity
```

The retrieval comparison uses the engine evaluation's answerable, non-follow-up handbook questions and the same offline embeddings in both stores. It compares hybrid Hit@5 and mean reciprocal rank.

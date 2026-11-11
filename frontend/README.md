# Sourced admin dashboard

Run the API, Alembic migrations, and ingestion worker as described in the [backend guide](../backend/README.md). For local dashboard development, bind the API to loopback and use HTTP between Vite and the API.

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

From `frontend/` with Node 20.19+ or 22.12+, run the following commands.

```sh
npm ci
npm run dev
```

Open Vite’s printed HTTPS URL. Vite uses a local development certificate and proxies `/api` to the API on port 8000. Set `SOURCED_API_URL` in the Vite process to use a different API origin. Serve the production build and `/api` from the same HTTPS origin and serve `index.html` for dashboard routes.

## Checks

From `frontend/` run `npm test` for the Vitest component suite and `npm run build` for TypeScript and the production bundle. Install the browser with `npx playwright install chromium`.

From the repository root, run the keyboard-only walkthrough with Docker running.

```sh
python -m frontend.e2e.run_live
```

The walkthrough uses a disposable pgvector database, a loopback API and worker, and the bundled fictional handbook. It covers sign-up, upload, asking a question, opening and closing a citation, usage, cancelling and confirming deletion, sign-out, and sign-in. Add `--screenshots` to capture the five pages under `frontend/screenshots/` with fictional review fixtures.

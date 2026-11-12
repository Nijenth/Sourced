# Deploying Sourced

Sourced ships as one Docker image: the API, the ingestion worker (running inside the API process), the admin dashboard and the Brightleaf demo page. It needs a Postgres database with the pgvector extension and an AI provider.

- [Free hosting: Render + Neon](#free-hosting-render--neon) for a public portfolio demo.
- [Your own computer: Docker Compose](#your-own-computer-docker-compose) for trying the whole system locally.
- [Checking a deployment](#checking-a-deployment) works for both.

## Free hosting: Render + Neon

| Piece | Service | Free plan |
| --- | --- | --- |
| API, worker, dashboard, demo | [Render](https://render.com) web service (Docker) | Sleeps after 15 minutes without visitors; the next visit wakes it in about a minute. 750 hours a month, enough for one always-available service. |
| Database | [Neon](https://neon.com) Postgres with pgvector | 1 GB storage; pauses after 5 minutes idle and resumes on the next query. |
| Answers and accuracy check | [GroqCloud](https://console.groq.com) | Free API key with rate limits. |
| Search embeddings | [Google AI Studio](https://aistudio.google.com) (Gemini) | Free API key. Google may use free-tier data to improve its products, so the free demo uses only the fictional handbook. |

Plans change; check each provider's pricing page before you rely on it.

### 1. Create the database (Neon)

1. Sign up at neon.com and create a project. Pick the same area as the Render service: the blueprint uses Virginia (US East), so choose **AWS US East (N. Virginia)**. US East keeps the server close to the AI providers and to visitors in the US and Europe; keeping the app and the database in the same region matters most, because every question makes several database queries.
2. Open **Connect**, turn **Connection pooling off**, and copy the connection string. It looks like `postgresql://user:password@ep-....aws.neon.tech/neondb?sslmode=require`. Sourced creates its tables and enables pgvector itself on first start.

Use the direct (unpooled) string: Sourced keeps its own connection pool.

### 2. Get the AI keys

- GroqCloud: console.groq.com → API Keys → create a key (`gsk_...`).
- Gemini: aistudio.google.com → Get API key.

Set a spending limit or keep both on free plans. The demo also limits itself to 200 questions a day (`SOURCED_WORKSPACE_QUESTIONS_PER_DAY`) and 10 questions a minute per visitor. Each workspace can store up to 100 documents and 250 MB, with at most 5 uploads waiting to be processed, and requests other than uploads are capped at 64 KB; `.env.example` lists the settings.

### 3. Deploy on Render

1. Sign up at render.com with your GitHub account.
2. **New → Blueprint**, pick the `Sourced` repository. Render reads `render.yaml`.
3. Fill in the values it asks for:
   - `SOURCED_DATABASE_URL`: the Neon string from step 1.
   - `GROQ_API_KEY` and `GEMINI_API_KEY`.
   - `SOURCED_DEMO_ADMIN_EMAIL` and `SOURCED_DEMO_ADMIN_PASSWORD` (12+ characters): your sign-in for the dashboard. On every start Sourced creates or updates this account, the fictional handbook and the demo widget.
4. **Apply**. The first build takes 5–10 minutes. When the service shows **Live**, its address is at the top, for example `https://sourced-ab12.onrender.com`.

What the blueprint sets for you: a generated `SOURCED_PREVIEW_SECRET`, Groq for answers and the accuracy check, Gemini for embeddings, no reranker (the free plan's memory is too small for it), the in-process worker, **sign-up closed** (strangers can't create accounts and upload files), and Render's and Cloudflare's proxy addresses in `SOURCED_TRUSTED_PROXIES` so per-visitor limits see real visitor addresses. `SOURCED_PUBLIC_ORIGIN` defaults to the service's Render address; set it only if you add a custom domain.

### 4. Try it

- `https://<your-service>.onrender.com/demo/`: the Brightleaf page with the chat bubble. The first question may take a few seconds while the handbook is indexed (once).
- `https://<your-service>.onrender.com/`: the dashboard. Sign in with the demo admin account.

Then run the [smoke test](#checking-a-deployment) and put the demo link at the top of the README and in the repository's **About → Website** field.

### Updating

Render redeploys automatically when `main` changes. Because `main` only accepts pull requests whose checks pass, every deploy has passed CI first.

## Your own computer: Docker Compose

Needs Docker Desktop (Linux containers).

1. Copy `.env.example` to `.env` and set:
   ```ini
   SOURCED_PREVIEW_SECRET=any-random-text-of-32-or-more-characters
   SOURCED_DEMO_ADMIN_EMAIL=you@example.com
   SOURCED_DEMO_ADMIN_PASSWORD=a long local password
   # Either real providers…
   GROQ_API_KEY=gsk_...
   GEMINI_API_KEY=...
   EMBEDDING_PROVIDER=gemini
   RERANKER=none
   # …or no keys at all:
   # RAG_OFFLINE_MODE=1
   ```
   To use Ollama on the same computer, set `OLLAMA_HOST=http://host.docker.internal:11434`.
2. `docker compose up --build`
3. Open http://localhost:8000 (dashboard) and http://localhost:8000/demo/.

The database lives in a Docker volume, so it survives restarts. `docker compose down --volumes` deletes it.

## Checking a deployment

```sh
python deploy/smoke_test.py https://<your-service>.onrender.com
```

It checks the API, the dashboard page and its security headers, the demo page, the widget script and the hosted chat page, then asks the demo a question until the handbook is indexed and the answer cites it. It uses only Python's standard library. CI runs the same script against the Docker image on every pull request.

# AGENTS.md: rules for AI coding agents in this repository

Sourced is a retrieval-augmented chatbot over PDFs. The `rag/` package is the engine; `app.py` is a Streamlit demo on top of it. A full-stack, API-first version (FastAPI backend, React admin dashboard, embeddable website widget, mobile support) is specified in `docs/fullstack-brief.md`, with the screens in `docs/design/`.

## Naming

- The product is called **Sourced** (it was called "DocChat" earlier; older commits use that name). Use "Sourced" in every user-facing title, page heading, document and README text.
- The GitHub repository is `Nijenth/Sourced`. A local checkout may be in a folder named `rag-chatbot` or `sourced`; don't rely on the folder name.
- New components follow the name: `sourced-api` (FastAPI backend), `sourced-web` (React admin dashboard), `sourced-widget` (embeddable chat), and `sourced-` prefixes for Docker services, images and database names.
- Sourced is built by the studio **Dittroid**. The studio's own website is a separate project; don't add it to this repository.
- The Python engine package stays `rag/`; don't rename it.

## Setup and checks

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                                   # must stay green: all pass, 1 expected failure (xfail)
python eval/run_eval.py --provider offline               # end-to-end smoke test, no API keys needed
python eval/run_eval.py --retrieval-only --provider offline   # retrieval comparison, no API keys needed
```

No API keys are needed for tests. `RAG_OFFLINE_MODE=1` runs the whole app with keyword stand-ins.

## Ownership

| Path | Owner | Rule for agents |
| --- | --- | --- |
| `rag/` | Engine maintainer | **Do not modify.** If you need an engine change, write the request in `docs/engine-requests.md` (what, why, proposed interface) and work around it until it lands. |
| `app.py` | Engine maintainer | Must keep working; don't refactor it as part of other work. |
| `tests/`, `eval/` | Engine maintainer | Don't edit existing tests or questions. Add new test files for new code. |
| `backend/`, `frontend/`, `widget/`, `demo/`, `deploy/` | Full-stack work | Yours, per `docs/fullstack-brief.md`. |
| `docs/design/` | Product owner | Read-only reference for the screens. Don't edit; ask if something is unclear. |

## Rules

- Never commit secrets. `.env` is git-ignored; use `.env.example` for new settings.
- Every change ships with tests. Run `pytest` before every commit.
- Keep the answer contract intact: answers come only from retrieved passages and pass the checks in `rag/citations.py`. Never display a blocked answer's text.
- Render model output and document text as plain text in any UI (no raw HTML, no clickable links taken from model output).
- Small, focused commits with clear messages. Work on a branch, not `main`.

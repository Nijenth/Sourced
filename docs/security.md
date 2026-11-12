# Security review status

## How Sourced was reviewed

Three read-only reviews by an AI coding agent (OpenAI Codex), each reading the code, tests and installed
libraries without running anything or using the network:

| Review | Revision | Findings |
| --- | --- | --- |
| Full audit | `9e505ca` | S01–S12 |
| Re-audit of the fixes | `3f2a5a1` | N01–N05 |
| Review after PR #15 | `dfe3ffb` | N06–N09 |

No human security professional has reviewed Sourced. Fixes were made by Claude (Anthropic's AI) and checked by
running the test suite locally against Postgres 16 with pgvector; for each fix, the matching test was also run with
the fix removed and failed. GitHub Actions runs the same tests on every pull request, including the browser
checks below.

## Findings

| ID | Finding | Status | Evidence |
| --- | --- | --- | --- |
| S01 | Request bodies read into memory before any check | Fixed | `backend/app/limits.py`; `backend/tests/test_request_limits.py`, multi-frame test in `test_reaudit.py`. Ingress limits on the host are not verified. |
| S02 | One workspace could use up shared upload, storage and PDF capacity | Partly fixed: per-workspace document, storage and queue limits and a cap on PDF readers. Rolling ingestion budgets, derived-storage accounting and the standalone sample API's quotas remain. | `documents.py` (`admit_upload`), `pdf_tasks.py` (`pdf_slots`); `test_fair_limits.py` |
| S03 | Streamlit demo rendered Markdown and links from documents and the model | Fixed (see N03, N08) | |
| S04 | Citation checks can't prove every claim; prompt injection | Open, reduced: citation, quote, number and judge checks; the evaluation grows with new cases | `rag/citations.py`, `rag/judge.py`, `eval/` |
| S05 | Which AI providers may see which documents | Open: decided per client deployment | |
| S06 | Widget answers rechecked only cited documents | Fixed | `public.py`; `test_widget_context.py`, `test_reaudit.py` |
| S07 | PDF reader runs with the service's permissions | Open: needs hosting with a sandboxed worker | |
| S08 | Preview links in logs; no audit trail of admin changes | Partly fixed: application access logs redact preview links. Proxy logs and an audit trail remain. | `widget_sessions.py`; `test_security_fixes.py` |
| S09 | One database account for setup and running | Open: set up per deployment | |
| S10 | Sessions survived a password change | Fixed, including a sign-in racing the change (N04). Fixed-window login throttling remains as designed. | `seed_demo.py`, `auth.py`; `test_security_fixes.py`, `test_reaudit.py` |
| S11 | Dependencies and build images not pinned | Open | |
| S12 | Backups and restore untested | Open: test a restore before real client data | |
| N01 | Database connections held during uploads and PDF work | Fixed: no connection is held while an upload arrives or a PDF is counted or drawn; uploads must arrive within `SOURCED_UPLOAD_READ_SECONDS` (default 180 s, about 0.5 Mbit/s for a 10 MB PDF) | `documents.py`, `public.py`; `test_reaudit.py` |
| N02 | Upload quota race relied on the database's default isolation | Fixed: explicit READ COMMITTED; tested with two uploads forced into the final check together, also with a REPEATABLE READ default | `documents.py`; `test_reaudit.py` |
| N03 | Streamlit linked web and email addresses after escaping | Fixed (see N08) | |
| N04 | A sign-in could race a password change | Fixed | `auth.py`; `test_reaudit.py` |
| N05 | Some new error responses lacked cross-origin headers | Partly fixed: dashboard 413s and widget preview errors carry them. A public chat request over 64 KB gets a 413 without them; the widget never sends one that large. | `main.py`, `public.py`; `test_reaudit.py` |
| N06 | An upload could be saved after its session was revoked | Fixed: the upload takes the admission lock first, then re-checks and share-locks the sign-in until it's saved | `documents.py`, `auth.py` (`current_member`); `test_reaudit.py` |
| N07 | A database call could run on the event loop | Fixed: the sign-in transaction ends in a worker thread | `documents.py` (`release_transaction`); `test_reaudit.py` |
| N08 | Word joiners missed some addresses and changed copied text | Fixed: untrusted text is shown as escaped plain text with `show()`; labels that Streamlit parses are fixed | `app.py`; `frontend/e2e/streamlit-text.spec.ts` (Chromium, runs in CI) checks the real `render_answer` |
| N09 | This page overstated the evidence | Fixed: this version | |

**Scope today:** the public demo uses a fictional handbook. Before a client's confidential documents, settle
S05, S07, S09 and S12 for that deployment.

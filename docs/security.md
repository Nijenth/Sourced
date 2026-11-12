# Security review status

Sourced has had two independent, read-only security reviews: a full audit at `9e505ca` and a re-audit of
the fixes at `3f2a5a1`. This page tracks each finding. Every fix ships with tests that fail on the code
before the fix.

| ID | Finding | Status | Where |
| --- | --- | --- | --- |
| S01 | Request bodies read into memory before any check | Fixed | `backend/app/limits.py` |
| S02 | One workspace could use up shared upload, storage and PDF capacity | Fixed in code; rolling ingestion budgets and derived-storage accounting are future work | `documents.py` (`admit_upload`), `pdf_tasks.py` (`pdf_slots`) |
| S03 | Streamlit demo rendered Markdown and links from documents and the model | Fixed; checked in a real browser | `app.py` (`md_escape`), `tests/test_app_browser.py` |
| S04 | Citation checks can't prove every claim; prompt injection | Ongoing: citation, quote, number and judge checks reduce it; the evaluation grows with new cases | `rag/citations.py`, `rag/judge.py`, `eval/` |
| S05 | Which AI providers may see which documents | Open: a per-client decision, made at onboarding | |
| S06 | Widget answers rechecked only cited documents | Fixed | `public.py` |
| S07 | PDF reader runs with the service's permissions | Open: needs hosting with a separate sandboxed worker | |
| S08 | Preview links in logs; no audit trail of admin changes | Logs fixed; audit trail is future work | `widget_sessions.py` |
| S09 | One database account for setup and running | Open: set up per deployment (Neon roles) | |
| S10 | Sessions survived a password change | Fixed, including a sign-in racing the change (N04) | `seed_demo.py`, `auth.py` |
| S11 | Dependencies and build images not pinned | Open | |
| S12 | Backups and restore untested | Open: test a Neon restore before real client data | |
| N01 | Database connections held during uploads and PDF work | Fixed; uploads must arrive within 60 s | `documents.py`, `public.py` |
| N02 | Quota race relied on the database's default isolation | Fixed: explicit READ COMMITTED; race test forces both uploads into the final check together | `documents.py` |
| N03 | Streamlit linked web and email addresses after escaping | Fixed; checked in a real browser | `app.py` |
| N04 | A sign-in could race a password change | Fixed | `auth.py` |
| N05 | Some new error responses lacked cross-origin headers | Fixed for the dashboard and widget previews | `main.py`, `public.py` |

**Scope today:** the public demo uses a fictional handbook. Before a client's confidential documents, settle
S05, S07, S09 and S12 for that deployment.

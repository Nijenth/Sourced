# Engine change requests

Full-stack work that needs a change in `rag/` writes it here: what, why, and the proposed interface.
See `docs/fullstack-brief.md` section 10 for requests already known.

## A machine-readable kind on ProviderError (M6)

**What:** give `rag.providers.ProviderError` a `kind` attribute: `"rate_limited"`, `"unavailable"` or `"misconfigured"`, set where the error is raised (`friendly_error`, the Ollama transport, the registry, the reranker loader).

**Why:** the API returns a short error code with every provider failure, and the worker retries only temporary ones. Today `backend/app/errors.py` infers the kind from the wording of the friendly message, so rewording a message in `rag/` could silently change how a failure is classified. `backend/tests/test_hardening.py::test_engine_provider_errors_map_to_short_codes` pins the current wording.

**Proposed interface:** `ProviderError(message, kind="unavailable")`, keeping `str(exc)` as the friendly message. The backend would read `getattr(exc, "kind", None)` and fall back to the text match.

## Stage timing hooks (M6)

**What:** an optional callback on `RAGPipeline` (for example `on_stage(name, seconds)`) for condense, retrieval, rerank, answer and judge.

**Why:** `backend/app/observability.py` measures stages by wrapping the store, chat model and reranker in proxies. It works, but it depends on which objects the pipeline calls; a hook would keep timings correct if the pipeline changes.

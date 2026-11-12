"""Short error codes that clients can act on, each with a fixed friendly message.

Provider failures are classified from the engine's ProviderError text (rag/ owns those messages and
doesn't expose a machine-readable kind yet; see docs/engine-requests.md). Raw provider errors never
reach a client: the dashboard and the public channels get one of the fixed messages below.
"""

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

RATE_LIMITED = "rate_limited"
PROVIDER_UNAVAILABLE = "provider_unavailable"
MISCONFIGURED = "misconfigured"
TOO_MANY_REQUESTS = "too_many_requests"
TRANSIENT = frozenset({RATE_LIMITED, PROVIDER_UNAVAILABLE})

logger = logging.getLogger(__name__)

# Lower-case fragments of the engine's ProviderError messages (rag/providers, rag/rerank).
_RATE_LIMIT_HINTS = ("rate-limiting", "rate limit", "daily limit", "out of credit", "quota")
_MISCONFIGURED_HINTS = (
    "rejected the api key", "needs the '", "doesn't recognise", "isn't set up", "isn't a chat provider",
    "isn't a provider this app knows", "can't create embeddings", "doesn't have the model", "unknown reranker",
    "couldn't load the reranker", "no provider for", "which needs",
)

ADMIN_MESSAGES = {
    RATE_LIMITED: "The AI provider is rate-limiting requests or its usage limit is used up. "
                  "Try again in a few minutes, or switch to another model.",
    PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable. Try again in a moment, or switch to another model.",
    MISCONFIGURED: "The AI provider isn't set up correctly. Check its API key and model name in the server settings.",
}
PUBLIC_MESSAGES = {
    RATE_LIMITED: "Chat is busy right now. Please try again in a few minutes.",
    PROVIDER_UNAVAILABLE: "Chat is temporarily unavailable. Please try again in a moment.",
    MISCONFIGURED: "Chat is unavailable right now. Please try again later.",
}


def provider_error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if any(hint in text for hint in _RATE_LIMIT_HINTS):
        return RATE_LIMITED
    if any(hint in text for hint in _MISCONFIGURED_HINTS):
        return MISCONFIGURED
    return PROVIDER_UNAVAILABLE


class CodedHTTPException(HTTPException):
    """An HTTP error whose JSON body carries a short `code` next to the friendly `detail`."""

    def __init__(self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(status_code, detail, headers)
        self.code = code


def log_summary(exc: BaseException) -> str:
    """The engine's friendly message, safe for logs: it names the provider and the problem but is built from
    templates (rag.providers.base.friendly_error), never from raw provider responses or keys."""
    return " ".join(str(exc).split())[:300]


def provider_failure(exc: BaseException, *, public: bool = False) -> CodedHTTPException:
    code = provider_error_code(exc)
    # Clients get a fixed message; the log line says which provider failed and how, for the operator.
    logger.warning("Provider operation failed (%s): %s", code, log_summary(exc))
    messages = PUBLIC_MESSAGES if public else ADMIN_MESSAGES
    headers = {"Retry-After": "60"} if code == RATE_LIMITED else None
    return CodedHTTPException(503, code, messages[code], headers)


async def coded_error(_request: Request, exc: CodedHTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)

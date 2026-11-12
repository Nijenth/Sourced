"""Reject oversized request bodies before any route reads them (security audit S01).

FastAPI reads a whole JSON body into memory before it checks who is asking or validates the fields, so without
this an anonymous caller could send a huge body to a public route and use up the server's memory. This layer
sits in front of every route:

- A declared Content-Length over the limit gets 413 without reading the body.
- A body without a declared length (chunked) is counted as it arrives and cut off at the limit.

The PDF upload route gets the upload size plus room for the multipart wrapping; it also checks the PDF's
own size itself. Everything else gets a small limit: the largest real request is a few kilobytes.
"""

import json

from .errors import CodedHTTPException
from .settings import DocumentLimits, positive_int

UPLOAD_PATH = "/api/v1/documents"
MULTIPART_ALLOWANCE = 64 * 1024
DEFAULT_MAX_BODY = 64 * 1024
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


TOO_LARGE = "request_too_large"
TOO_LARGE_MESSAGE = "The request is too large."


class BodyTooLarge(CodedHTTPException):
    """Raised while a route reads the body. It's an HTTP error so FastAPI passes it through as a 413."""

    def __init__(self):
        super().__init__(413, TOO_LARGE, TOO_LARGE_MESSAGE, {"Connection": "close"})


def body_limits() -> tuple[int, int]:
    """(limit for ordinary requests, limit for the PDF upload route), from the environment."""
    upload = DocumentLimits.from_env().bytes + MULTIPART_ALLOWANCE
    return positive_int("SOURCED_MAX_BODY_BYTES", DEFAULT_MAX_BODY), upload


class BodySizeLimit:
    def __init__(self, app, max_body: int | None = None, max_upload: int | None = None):
        self.app = app
        default_body, default_upload = body_limits()
        self.max_body = max_body or default_body
        self.max_upload = max_upload or default_upload

    def limit_for(self, scope) -> int:
        if scope["method"] == "POST" and scope.get("path", "").rstrip("/") == UPLOAD_PATH:
            return self.max_upload
        return self.max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > limit
            except ValueError:
                too_large = False  # the server rejects malformed lengths itself
            if too_large:
                await reject(send)
                return

        received = 0
        started = False

        async def counted_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except BodyTooLarge:
            if not started:
                await reject(send)


async def reject(send) -> None:
    body = json.dumps({"detail": TOO_LARGE_MESSAGE, "code": TOO_LARGE}).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})

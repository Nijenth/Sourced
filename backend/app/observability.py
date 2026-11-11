"""Request IDs on every response and log line, and per-stage timings for chat answers.

Nothing here logs question text, answers, document text or credentials: timing lines carry only the
channel, the outcome and durations.
"""

import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import MutableHeaders

from rag.pipeline import CONDENSE_SYSTEM

REQUEST_ID: ContextVar[str] = ContextVar("sourced_request_id", default="-")
REQUEST_ID_HEADER = "X-Request-ID"
_INCOMING_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
timing_logger = logging.getLogger("sourced.timing")
_factory_installed = False


def install_log_record_factory() -> None:
    """Give every log record a `request_id` attribute (\"-\" outside a request)."""
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.request_id = REQUEST_ID.get()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


class RequestIdFormatter(logging.Formatter):
    """A formatter that also works for records made before the record factory was installed."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return super().format(record)


class RequestIdMiddleware:
    """Use a well-formed incoming X-Request-ID or make one, and return it on the response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _INCOMING_ID.fullmatch(incoming) else uuid4().hex
        token = REQUEST_ID.set(request_id)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            REQUEST_ID.reset(token)


class Timings:
    """Milliseconds spent per pipeline stage for one question."""

    def __init__(self):
        self.started = time.perf_counter()
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - start) * 1000

    def log(self, channel: str, outcome: str) -> None:
        total = (time.perf_counter() - self.started) * 1000
        parts = " ".join(f"{name}_ms={ms:.0f}" for name, ms in sorted(self.stages.items()))
        timing_logger.info("chat channel=%s outcome=%s %s total_ms=%.0f", channel, outcome, parts, total)


class _Timed:
    """Delegate everything to the wrapped engine object; subclasses time one method."""

    def __init__(self, target, timings: Timings):
        self._target = target
        self._timings = timings

    def __getattr__(self, name):
        return getattr(self._target, name)


class TimedStore(_Timed):
    def search(self, *args, **kwargs):
        with self._timings.stage("retrieval"):
            return self._target.search(*args, **kwargs)


class TimedReranker(_Timed):
    def rerank(self, *args, **kwargs):
        with self._timings.stage("rerank"):
            return self._target.rerank(*args, **kwargs)


class TimedChat(_Timed):
    def __init__(self, target, timings: Timings, stage: str):
        super().__init__(target, timings)
        self._stage = stage

    def complete(self, system, messages, *args, **kwargs):
        stage = "condense" if system == CONDENSE_SYSTEM else self._stage
        with self._timings.stage(stage):
            return self._target.complete(system, messages, *args, **kwargs)

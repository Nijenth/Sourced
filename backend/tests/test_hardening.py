"""M6 hardening: error codes, sign-in limits, refunds, request IDs, timings, worker retries and cleanup."""

import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend.app import pdf_tasks
from backend.app.db.models import Document, LoginSession, QuestionLimit
from backend.app.errors import (
    MISCONFIGURED, PROVIDER_UNAVAILABLE, PUBLIC_MESSAGES, RATE_LIMITED, provider_error_code,
)
from backend.app.main import app
from backend.app.pdf_tasks import PdfTaskError, run_pdf_task
from backend.app.settings import DocumentLimits
from backend.app.worker import (
    MAX_ATTEMPTS, MISCONFIGURED_ERROR, PROVIDER_DOWN_ERROR, WAITING_TO_RETRY, cleanup, process_one,
)
from rag.offline import HashingEmbedder, KeywordChat
from rag.providers import ProviderError
from rag.providers.base import friendly_error

from .test_widgets import ORIGIN, ask_widget, client, create_widget
from .test_workspaces import ALPHA_TEXT, API, PASSWORD, QUESTION, ask, csrf, make_pdf, queue, signup

RAW = "upstream said: 429 Too Many Requests for key sk-live-secret"


def provider_error(raw: str, service: str = "Gemini") -> ProviderError:
    return ProviderError(friendly_error(service, Exception(raw)))


@pytest.mark.parametrize(("error", "code"), [
    (provider_error("429 rate limit exceeded"), RATE_LIMITED),
    (provider_error("RESOURCE_EXHAUSTED: quota PerDay exceeded"), RATE_LIMITED),
    (provider_error("503 Service Unavailable"), PROVIDER_UNAVAILABLE),
    (provider_error("connection reset"), PROVIDER_UNAVAILABLE),
    (provider_error("something odd"), PROVIDER_UNAVAILABLE),
    (provider_error("401 invalid api key"), MISCONFIGURED),
    (provider_error("404 model not found"), MISCONFIGURED),
    (ProviderError("GroqCloud isn't set up. Add GROQ_API_KEY to your settings."), MISCONFIGURED),
    (ProviderError("Ollama doesn't have the model 'qwen'. Download it with: ollama pull qwen"), MISCONFIGURED),
    (ProviderError("Couldn't reach Ollama at http://127.0.0.1:11434. Make sure the Ollama app is running."), PROVIDER_UNAVAILABLE),
])
def test_engine_provider_errors_map_to_short_codes(error, code):
    assert provider_error_code(error) == code


def failing_chat(monkeypatch, error):
    def complete(self, system, messages, max_tokens=900):
        raise error

    monkeypatch.setattr(KeywordChat, "complete", complete)


def limit_counts(sessions):
    with sessions() as db:
        return dict(db.execute(select(QuestionLimit.scope, func.sum(QuestionLimit.requests)).group_by(QuestionLimit.scope)).all())


def test_admin_provider_failure_returns_a_code_and_refunds_the_daily_allowance(workspace_client, sessions, monkeypatch, caplog):
    browser = workspace_client
    signup(browser, "alpha")
    queue(browser, make_pdf(ALPHA_TEXT))
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
    assert ask(browser).status_code == 200
    assert limit_counts(sessions)["workspace_day"] == 1

    failing_chat(monkeypatch, provider_error(RAW))
    with caplog.at_level(logging.INFO):
        response = ask(browser)
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == RATE_LIMITED
    assert "try again" in body["detail"].lower()
    assert "Gemini" not in response.text and "secret" not in response.text and "429" not in response.text
    assert response.headers["retry-after"] == "60"
    assert limit_counts(sessions)["workspace_day"] == 1

    # Every log line written while handling the request carries its ID, and none carries the question.
    request_id = response.headers["x-request-id"]
    handled = [record for record in caplog.records if getattr(record, "request_id", "-") == request_id]
    assert any(record.name == "backend.app.errors" for record in handled)
    assert any(record.name == "sourced.timing" and "outcome=error:rate_limited" in record.getMessage() for record in handled)
    assert all(QUESTION not in record.getMessage() and "secret" not in record.getMessage() for record in caplog.records)


def test_timings_are_logged_per_stage_without_content(workspace_client, sessions, caplog):
    browser = workspace_client
    signup(browser, "alpha")
    queue(browser, make_pdf(ALPHA_TEXT))
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
    with caplog.at_level(logging.INFO, logger="sourced.timing"):
        response = ask(browser, **{"X-Request-ID": "support-ticket-1234"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "support-ticket-1234"
    line, = [record for record in caplog.records if record.name == "sourced.timing"]
    message = line.getMessage()
    assert line.request_id == "support-ticket-1234"
    assert "channel=ask_test outcome=answered" in message
    assert "retrieval_ms=" in message and "answer_ms=" in message and "total_ms=" in message
    assert "Orchard" not in message and "verification" not in message


def test_request_ids_are_generated_or_replaced_when_malformed(workspace_client):
    generated = workspace_client.get(API + "/health")
    assert len(generated.headers["x-request-id"]) == 32
    assert workspace_client.get(API + "/health", headers={"X-Request-ID": "bad id\nInjected: 1"}).headers["x-request-id"] != "bad id"
    assert workspace_client.get(API + "/health", headers={"X-Request-ID": "short"}).headers["x-request-id"] != "short"


@pytest.fixture
def public_widget(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", "https://chat.sourced.example")
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_WIDGET_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setenv("SOURCED_IP_REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("SOURCED_WORKSPACE_QUESTIONS_PER_DAY", "100")
    monkeypatch.delenv("SOURCED_TRUSTED_PROXIES", raising=False)


def test_public_provider_failure_uses_visitor_wording_and_spares_the_visitor_limit(public_widget, sessions, monkeypatch):
    with client() as owner:
        signup(owner, "alpha")
        document = queue(owner, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        key = create_widget(owner, [document])["site_key"]

        failing_chat(monkeypatch, provider_error("503 Service Unavailable", "OpenAI"))
        for _ in range(3):  # more than the visitor's two questions a minute
            response = ask_widget(owner, key)
            assert response.status_code == 503
            assert response.json() == {"detail": PUBLIC_MESSAGES[PROVIDER_UNAVAILABLE], "code": PROVIDER_UNAVAILABLE}
            assert response.headers["access-control-allow-origin"] == ORIGIN
        counts = limit_counts(sessions)
        assert counts["ip_minute"] == 0 and counts["workspace_day"] == 0
        assert counts["widget_minute"] == 3  # the widget-wide cap still throttles an outage


def test_visitor_limit_still_applies_to_answered_questions(public_widget, sessions):
    with client() as owner:
        signup(owner, "alpha")
        document = queue(owner, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        key = create_widget(owner, [document])["site_key"]
        assert ask_widget(owner, key).status_code == 200
        assert ask_widget(owner, key).status_code == 200
        limited = ask_widget(owner, key)
        assert limited.status_code == 429
        assert limited.json()["code"] == "too_many_requests"


def sign_in(browser, email, password=PASSWORD):
    return browser.post(API + "/auth/login", json={"email": email, "password": password})


def test_sign_in_attempts_are_limited_per_account_and_network(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_LOGIN_ATTEMPTS_PER_EMAIL", "3")
    monkeypatch.setenv("SOURCED_LOGIN_ATTEMPTS_PER_IP", "8")
    with client("198.51.100.7") as browser, client("198.51.100.8") as elsewhere:
        signup(browser, "alpha")
        for _ in range(5):  # successful sign-ins don't use up the account's allowance
            assert sign_in(browser, "alpha@example.com").status_code == 200
        for _ in range(3):
            assert sign_in(elsewhere, "alpha@example.com", "wrong password!").status_code == 401
        locked = sign_in(browser, "alpha@example.com")
        assert locked.status_code == 429
        assert locked.json()["code"] == "too_many_requests"
        assert locked.json()["detail"].startswith("Too many sign-in attempts. Try again in ")
        assert 0 < int(locked.headers["retry-after"]) <= 15 * 60
        assert sign_in(browser, "ALPHA@example.com").status_code == 429  # same account, any spelling

        # One network trying many accounts is limited too: 3 attempts so far from this address, 8 allowed.
        for index in range(8):
            response = sign_in(elsewhere, f"user{index}@example.com", "wrong password!")
        assert response.status_code == 429
        with sessions() as db:
            subjects = db.scalars(select(QuestionLimit.subject).where(QuestionLimit.scope.like("login_%"))).all()
        assert subjects and not any("alpha" in subject or "198.51" in subject for subject in subjects)


def test_new_accounts_are_limited_per_network(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_SIGNUPS_PER_IP", "2")
    with client("203.0.113.9") as browser:
        signup(browser, "one")
        signup(browser, "two")
        response = browser.post(API + "/auth/signup", json={"email": "three@example.com", "password": PASSWORD})
        assert response.status_code == 429
        assert "Too many new accounts" in response.json()["detail"]


class FlakyEmbedder(HashingEmbedder):
    def __init__(self, failures, error):
        super().__init__()
        self.failures = failures
        self.error = error

    def embed(self, texts):
        if self.failures:
            self.failures -= 1
            raise self.error
        return super().embed(texts)


def document_row(sessions, document_id):
    with sessions() as db:
        row = db.get(Document, document_id)
        return SimpleNamespace(status=row.status, error=row.error, attempts=row.attempts, lease_until=row.lease_until)


def make_due(sessions, document_id):
    with sessions.begin() as db:
        db.execute(update(Document).where(Document.id == document_id).values(lease_until=func.now() - timedelta(seconds=1)))


def test_temporary_embedding_failures_are_retried_with_backoff(workspace_client, sessions):
    signup(workspace_client, "alpha")
    document = queue(workspace_client, make_pdf(ALPHA_TEXT))
    limits = DocumentLimits.from_env()
    embedder = FlakyEmbedder(1, provider_error("429 rate limit exceeded"))

    assert process_one(sessions, limits, embedder, retry_seconds=60)
    waiting = document_row(sessions, document)
    assert (waiting.status, waiting.error, waiting.attempts) == ("queued", WAITING_TO_RETRY, 1)
    assert waiting.lease_until > datetime.now(timezone.utc) + timedelta(seconds=50)
    assert workspace_client.get(f"{API}/documents/{document}").json()["error"] == WAITING_TO_RETRY
    assert not process_one(sessions, limits, embedder, retry_seconds=60)  # still backing off

    make_due(sessions, document)
    assert process_one(sessions, limits, embedder, retry_seconds=60)
    done = document_row(sessions, document)
    assert (done.status, done.error, done.attempts) == ("ready", None, 2)
    assert ask(workspace_client).json()["status"] == "answered"


def test_embedding_failures_stop_after_the_attempt_limit_or_at_once_when_misconfigured(workspace_client, sessions):
    signup(workspace_client, "alpha")
    limits = DocumentLimits.from_env()
    outage = queue(workspace_client, make_pdf(ALPHA_TEXT))
    embedder = FlakyEmbedder(MAX_ATTEMPTS, provider_error("503 Service Unavailable"))
    for _ in range(MAX_ATTEMPTS):
        assert process_one(sessions, limits, embedder, retry_seconds=60)
        make_due(sessions, outage)
    failed = document_row(sessions, outage)
    assert (failed.status, failed.error, failed.attempts) == ("failed", PROVIDER_DOWN_ERROR, MAX_ATTEMPTS)

    broken = queue(workspace_client, make_pdf(ALPHA_TEXT))
    assert process_one(sessions, limits, FlakyEmbedder(1, provider_error("401 invalid api key")), retry_seconds=60)
    misconfigured = document_row(sessions, broken)
    assert (misconfigured.status, misconfigured.error, misconfigured.attempts) == ("failed", MISCONFIGURED_ERROR, 1)


def test_cleanup_removes_only_expired_sessions_and_limit_windows(workspace_client, sessions):
    signup(workspace_client, "alpha")
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        live = db.scalar(select(LoginSession))
        live_hash = live.token_hash
        db.add(LoginSession(token_hash="0" * 64, csrf_hash="1" * 64, user_id=live.user_id,
                            workspace_id=live.workspace_id, expires_at=now - timedelta(minutes=1)))
        db.add_all([
            QuestionLimit(scope="ip_minute", subject="old", expires_at=now - timedelta(seconds=1), requests=4),
            QuestionLimit(scope="ip_minute", subject="current", expires_at=now + timedelta(minutes=1), requests=1),
        ])
    assert cleanup(sessions) == (1, 1)
    with sessions() as db:
        assert db.scalars(select(LoginSession.token_hash)).all() == [live_hash]
        remaining = set(db.scalars(select(QuestionLimit.subject)))
        assert "current" in remaining and "old" not in remaining  # the sign-up's own live window stays too
    assert workspace_client.get(API + "/auth/me").status_code == 200


def test_pdf_task_memory_errors_have_a_safe_message(monkeypatch):
    monkeypatch.setattr(pdf_tasks.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=4, stdout=b""))
    with pytest.raises(PdfTaskError, match="too much memory"):
        run_pdf_task("count", b"%PDF-", timeout=5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is only enforced on Linux")
def test_pdf_task_memory_limit_is_enforced():
    code = (
        "from backend.app.pdf_tasks import apply_memory_limit\n"
        "apply_memory_limit()\n"
        "try:\n    bytearray(1024 * 1024 * 1024)\nexcept MemoryError:\n    raise SystemExit(4)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], env={"SOURCED_PDF_MEMORY_MB": "256", "PATH": ""},
                            cwd=pdf_tasks.__file__.rsplit("backend", 1)[0], capture_output=True, timeout=30)
    assert result.returncode == 4, result.stderr.decode()


def test_logging_config_formats_request_ids(tmp_path):
    import json
    import logging.config
    from pathlib import Path

    config = json.loads((Path(__file__).resolve().parents[1] / "logging.json").read_text())
    log_file = tmp_path / "sourced.log"
    config["handlers"]["default"] = {"class": "logging.FileHandler", "formatter": "default", "filename": str(log_file)}
    previous = logging.getLogger().handlers[:], logging.getLogger().level
    try:
        logging.config.dictConfig(config)
        record = logging.LogRecord("plain", logging.INFO, __file__, 1, "before any request", None, None)
        logging.getLogger("plain").handle(record)
        logging.getLogger("sourced.timing").info("chat channel=widget outcome=answered")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = log_file.read_text()
        assert "[-] before any request" in text and "chat channel=widget" in text
    finally:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in previous[0]:
            root.addHandler(handler)
        root.setLevel(previous[1])

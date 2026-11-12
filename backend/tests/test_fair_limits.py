"""Security audit S02: one workspace can't take the whole server's storage, queue or PDF capacity."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app import documents, pdf_tasks
from backend.app.main import app
from backend.app.settings import DocumentLimits, UploadQuota
from backend.app.worker import process_one
from rag.offline import HashingEmbedder

from .test_workspaces import (
    ALPHA_TEXT,
    API,
    BETA_TEXT,
    PASSWORD,
    csrf,
    make_pdf,
    queue,
    signup,
    upload,
)

PDF = make_pdf(ALPHA_TEXT)


def quota(monkeypatch, **limits):
    values = {"documents": 100, "storage_bytes": 100 * 1024 * 1024, "pending": 5, "pending_total": 25, **limits}
    monkeypatch.setattr(documents.UploadQuota, "from_env", classmethod(lambda cls: UploadQuota(**values)))


def browser():
    return TestClient(app, base_url="https://sourced.example")


def process_all(sessions):
    while process_one(sessions, DocumentLimits.from_env(), HashingEmbedder()):
        pass


def test_document_count_limit_is_per_workspace(workspace_client, sessions, monkeypatch):
    quota(monkeypatch, documents=2)
    signup(workspace_client, "alpha")
    first = queue(workspace_client, PDF)
    queue(workspace_client, make_pdf(BETA_TEXT))
    process_all(sessions)
    refused = upload(workspace_client, PDF)
    assert refused.status_code == 409
    assert refused.json() == {"code": "workspace_full", "detail": (
        "This workspace has reached its limit of 2 documents. Delete one to upload another.")}
    # Another workspace isn't affected, and deleting a document makes room again.
    with browser() as other:
        signup(other, "beta")
        queue(other, PDF)
    assert workspace_client.delete(f"{API}/documents/{first}", headers=csrf(workspace_client)).status_code == 204
    queue(workspace_client, PDF)


def test_storage_limit_counts_stored_pdf_bytes(workspace_client, sessions, monkeypatch):
    quota(monkeypatch, storage_bytes=len(PDF) * 2 + 10)
    signup(workspace_client, "alpha")
    queue(workspace_client, PDF)
    queue(workspace_client, PDF)
    refused = upload(workspace_client, PDF)
    assert refused.status_code == 409 and refused.json()["code"] == "workspace_full"
    assert "storage limit" in refused.json()["detail"]


def test_pending_uploads_limit_per_workspace_and_for_the_server(workspace_client, sessions, monkeypatch):
    quota(monkeypatch, pending=1, pending_total=2)
    signup(workspace_client, "alpha")
    queue(workspace_client, PDF)
    waiting = upload(workspace_client, PDF)
    assert waiting.status_code == 429 and waiting.json()["code"] == "uploads_pending"
    assert waiting.headers["Retry-After"] == "30"
    with browser() as beta, browser() as gamma:
        signup(beta, "beta")
        queue(beta, PDF)
        signup(gamma, "gamma")
        busy = upload(gamma, PDF)
        assert busy.status_code == 503 and busy.json()["code"] == "server_busy"
        process_all(sessions)
        queue(gamma, PDF)
    queue(workspace_client, PDF)


def test_simultaneous_uploads_cannot_both_pass_the_limit(workspace_client, sessions, monkeypatch):
    quota(monkeypatch, documents=1)
    signup(workspace_client, "alpha")
    with browser() as second:
        assert second.post(f"{API}/auth/login", json={"email": "alpha@example.com", "password": PASSWORD}).status_code == 200
        # Both requests pass the quick early check, then race to save.
        start = threading.Barrier(2)

        def send(client):
            start.wait()
            return upload(client, PDF).status_code

        with ThreadPoolExecutor(2) as pool:
            codes = sorted(pool.map(send, [workspace_client, second]))
    assert codes == [202, 409]


def test_busy_pdf_slots_give_a_retryable_error(workspace_client, sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PDF_CONCURRENCY", "1")
    monkeypatch.setattr(pdf_tasks, "REQUEST_WAIT_SECONDS", 0.2)
    signup(workspace_client, "alpha")
    document = queue(workspace_client, PDF)
    process_all(sessions)

    slots = pdf_tasks.pdf_slots()
    assert slots.acquire(timeout=1)
    try:
        refused = upload(workspace_client, PDF)
        assert refused.status_code == 503
        assert refused.json()["code"] == "pdf_busy" and refused.headers["Retry-After"] == "10"
        page = workspace_client.get(f"{API}/documents/{document}/pages/1.png")
        assert page.status_code == 503 and page.json()["code"] == "pdf_busy"
    finally:
        slots.release()
    assert workspace_client.get(f"{API}/documents/{document}/pages/1.png").status_code == 200
    queue(workspace_client, PDF)


def test_pdf_slots_cap_simultaneous_work(monkeypatch):
    monkeypatch.setenv("SOURCED_PDF_CONCURRENCY", "2")
    running, peak, lock = 0, 0, threading.Lock()

    def slow(*_args):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return b"ok"

    monkeypatch.setattr(pdf_tasks, "_run", slow)
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: pdf_tasks.run_pdf_task("count", b"", timeout=5, background=True), range(6)))
    assert results == [b"ok"] * 6 and peak == 2


def test_invalid_concurrency_setting_is_refused(monkeypatch):
    monkeypatch.setenv("SOURCED_PDF_CONCURRENCY", "0")
    with pytest.raises(ValueError):
        pdf_tasks.pdf_slots()


def test_quota_settings_from_environment(monkeypatch):
    monkeypatch.setenv("SOURCED_WORKSPACE_MAX_DOCUMENTS", "7")
    monkeypatch.setenv("SOURCED_WORKSPACE_MAX_STORAGE_MB", "3")
    monkeypatch.delenv("SOURCED_WORKSPACE_MAX_PENDING", raising=False)
    monkeypatch.delenv("SOURCED_MAX_PENDING_DOCUMENTS", raising=False)
    assert UploadQuota.from_env() == UploadQuota(documents=7, storage_bytes=3 * 1024 * 1024, pending=5, pending_total=25)

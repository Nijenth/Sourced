"""Fixes for the security re-audit's findings N01, N02, N04, N05, plus the test gaps it pointed out."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from backend.app import auth, documents, main, pdf_tasks
from backend.app.chat import to_response
from backend.app.db import get_db
from backend.app.db.models import Document, PagePreview, WidgetDocument
from backend.app.errors import CodedHTTPException, coded_error
from backend.app.limits import BodySizeLimit
from backend.app.main import DashboardCors, app
from backend.app.observability import RequestIdMiddleware
from backend.app.seed_demo import seed
from backend.app.settings import DocumentLimits
from backend.app.store import DocumentHit
from backend.app.worker import process_one
from rag.citations import ANSWERED, Answer, Citation
from rag.documents import Chunk
from rag.offline import HashingEmbedder

from .test_fair_limits import PDF, quota
from .test_widgets import (  # noqa: F401 (fixture)
    ORIGIN,
    ask_widget,
    client,
    create_widget,
    public_env,
)
from .test_workspaces import API, PASSWORD, csrf, queue, signup, upload


def ready_document(browser, sessions):
    document = queue(browser, PDF)
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
    return document


@pytest.fixture
def tracked_sessions():
    """The database session each request uses, so a test can see whether one is held at a given moment."""
    opened = []

    def tracking_db(request: Request):
        with request.state.sessions() as db:
            opened.append(db)
            yield db

    app.dependency_overrides[get_db] = tracking_db
    try:
        yield opened
    finally:
        app.dependency_overrides.pop(get_db, None)


# N01 · no database connection is held while an upload arrives or a PDF is read or drawn.

def test_no_connection_is_held_while_uploads_arrive_or_pdfs_are_processed(
    workspace_client, sessions, tracked_sessions, monkeypatch,
):
    signup(workspace_client, "alpha")
    document = ready_document(workspace_client, sessions)
    held = {}
    real_parse, real_run = documents.MultiPartParser.parse, documents.run_pdf_task

    async def parse(self):
        held["while the upload arrives"] = tracked_sessions[-1].in_transaction()
        return await real_parse(self)

    def run(operation, *args, **kwargs):
        held[operation] = tracked_sessions[-1].in_transaction()
        return real_run(operation, *args, **kwargs)

    monkeypatch.setattr(documents.MultiPartParser, "parse", parse)
    monkeypatch.setattr(documents, "run_pdf_task", run)
    queue(workspace_client, PDF)
    assert workspace_client.get(f"{API}/documents/{document}/pages/1.png").status_code == 200
    assert workspace_client.post(f"{API}/documents/{document}/pages/1/highlight", headers=csrf(workspace_client),
                                 json={"quote": "verification phrase"}).status_code == 200
    assert held == {"while the upload arrives": False, "count": False, "preview": False, "highlight": False}


def test_a_document_deleted_while_its_page_is_drawn_is_not_served_or_cached(workspace_client, sessions, monkeypatch):
    signup(workspace_client, "alpha")
    document = ready_document(workspace_client, sessions)
    real_run = documents.run_pdf_task

    def run_then_delete(operation, *args, **kwargs):
        png = real_run(operation, *args, **kwargs)
        with sessions.begin() as db:
            db.execute(delete(Document).where(Document.id == document))
        return png

    monkeypatch.setattr(documents, "run_pdf_task", run_then_delete)
    assert workspace_client.get(f"{API}/documents/{document}/pages/1.png").status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PagePreview)) == 0


def test_signing_out_while_a_page_is_drawn_refuses_the_page(workspace_client, sessions, monkeypatch):
    signup(workspace_client, "alpha")
    document = ready_document(workspace_client, sessions)
    real_run = documents.run_pdf_task

    def run_then_sign_out(operation, *args, **kwargs):
        png = real_run(operation, *args, **kwargs)
        with sessions.begin() as db:
            db.execute(delete(auth.LoginSession))
        return png

    monkeypatch.setattr(documents, "run_pdf_task", run_then_sign_out)
    highlighted = workspace_client.post(f"{API}/documents/{document}/pages/1/highlight", headers=csrf(workspace_client),
                                        json={"quote": "verification phrase"})
    assert highlighted.status_code == 401


def test_a_widget_preview_is_refused_if_its_document_is_detached_while_drawn(public_env, sessions, monkeypatch):
    with client() as admin, client("192.0.2.11") as visitor:
        signup(admin, "alpha")
        document = ready_document(admin, sessions)
        key = create_widget(admin, [document])["site_key"]
        citation, = ask_widget(visitor, key).json()["citations"]
        real_run = documents.run_pdf_task

        def run_then_detach(operation, *args, **kwargs):
            png = real_run(operation, *args, **kwargs)
            with sessions.begin() as db:
                db.execute(delete(WidgetDocument).where(WidgetDocument.document_id == document))
            return png

        monkeypatch.setattr(documents, "run_pdf_task", run_then_detach)
        refused = visitor.get(citation["preview_url"], headers={"Origin": ORIGIN})
        assert refused.status_code == 404
        assert refused.headers["access-control-allow-origin"] == ORIGIN


def test_an_upload_that_arrives_too_slowly_is_cut_off(monkeypatch):
    monkeypatch.setenv("SOURCED_UPLOAD_READ_SECONDS", "1")

    class SlowRequest:
        headers = Headers({"content-type": "multipart/form-data; boundary=x"})
        state = SimpleNamespace(document_limits=SimpleNamespace(bytes=1024 * 1024))

        async def stream(self):
            yield b"--x\r\n"
            await asyncio.sleep(5)
            yield b""

    with pytest.raises(HTTPException) as refused:
        asyncio.run(documents.read_upload(SlowRequest(), None))
    assert refused.value.status_code == 408


# N07 · ending the sign-in transaction never runs on the event loop.

def test_upload_rollbacks_never_run_on_the_event_loop(workspace_client, sessions, monkeypatch):
    signup(workspace_client, "alpha")
    on_loop = []
    real_rollback = Session.rollback

    def rollback(self):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_rollback(self)

    monkeypatch.setattr(Session, "rollback", rollback)
    queue(workspace_client, PDF)
    assert on_loop and not any(on_loop)


# N02 · the final quota decision really is serialized, even when both uploads reach it together, and even if the
# database's default isolation is stricter than READ COMMITTED.

def race_two_uploads(workspace_client, monkeypatch):
    quota(monkeypatch, documents=1)
    signup(workspace_client, "alpha")
    together = threading.Barrier(2, timeout=30)
    real_lock = documents.lock_upload_admission

    def lock(db):
        together.wait()  # both requests passed the early check and now ask for the lock at the same moment
        return real_lock(db)

    monkeypatch.setattr(documents, "lock_upload_admission", lock)
    with TestClient(app, base_url="https://sourced.example") as second:
        assert second.post(f"{API}/auth/login", json={"email": "alpha@example.com", "password": PASSWORD}).status_code == 200
        with ThreadPoolExecutor(2) as pool:
            return sorted(pool.map(lambda browser: upload(browser, PDF).status_code, [workspace_client, second]))


def test_uploads_that_reach_the_final_check_together_cannot_both_pass(workspace_client, sessions, monkeypatch):
    assert race_two_uploads(workspace_client, monkeypatch) == [202, 409]


def test_the_race_holds_when_the_database_default_is_repeatable_read(sessions, monkeypatch):
    real_engine = main.database_engine
    monkeypatch.setattr(main, "database_engine", lambda: real_engine().execution_options(isolation_level="REPEATABLE READ"))
    with TestClient(app, base_url="https://sourced.example") as browser:
        assert race_two_uploads(browser, monkeypatch) == [202, 409]


# N06 · an upload waiting for the admission lock is refused if its sign-in ends meanwhile.

def test_an_upload_is_not_saved_if_its_session_is_revoked_while_it_waits(workspace_client, sessions, monkeypatch):
    signup(workspace_client, "alpha")
    with sessions() as holder:
        holder.execute(select(func.pg_advisory_xact_lock(documents.UPLOAD_ADMISSION_LOCK)))  # someone else is deciding
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(upload, workspace_client, PDF)
            deadline = time.monotonic() + 30
            with sessions() as watcher:
                while not watcher.scalar(select(func.count()).select_from(text("pg_locks")).where(
                    text("locktype = 'advisory' AND NOT granted"))):
                    assert time.monotonic() < deadline, "The upload never reached the admission lock."
                    time.sleep(0.05)
                    watcher.rollback()
            with sessions.begin() as revoker:  # signed out while the upload waits
                revoker.execute(delete(auth.LoginSession))
            holder.rollback()  # the lock is released and the upload continues
            assert pending.result(timeout=30).status_code == 401
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0


# N04 · a sign-in that checked the old password can't create a session after the password changes.

def test_a_sign_in_overlapping_a_password_change_gets_no_session(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", "https://chat.sourced.example")
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_DEMO_ADMIN_EMAIL", "demo@example.com")
    monkeypatch.delenv("SOURCED_DEMO_SITE_KEY", raising=False)
    seed(sessions, "demo@example.com", PASSWORD, "offline")
    real_forgive = auth.forgive_login

    def forgive_then_rotate(db, attempt):
        real_forgive(db, attempt)  # the old password was just accepted...
        seed(sessions, "demo@example.com", PASSWORD + " rotated", "offline")  # ...and is changed right now

    monkeypatch.setattr(auth, "forgive_login", forgive_then_rotate)
    with client() as browser:
        signed_in = browser.post(API + "/auth/login", json={"email": "demo@example.com", "password": PASSWORD})
        assert signed_in.status_code == 401
        assert browser.get(API + "/auth/me").status_code == 401
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(auth.LoginSession)) == 0


def test_a_concurrent_rehash_with_the_same_password_still_signs_in(sessions, monkeypatch):
    with client() as browser:
        signup(browser, "alpha")
    real_forgive = auth.forgive_login

    def forgive_then_rehash(db, attempt):
        real_forgive(db, attempt)
        with sessions.begin() as other:  # same password, new hash (as a parallel sign-in's rehash would do)
            user = other.scalar(select(auth.User).where(auth.User.email == "alpha@example.com"))
            user.password_hash = auth.passwords.hash(PASSWORD)

    monkeypatch.setattr(auth, "forgive_login", forgive_then_rehash)
    with client() as browser:
        assert browser.post(API + "/auth/login", json={"email": "alpha@example.com", "password": PASSWORD}).status_code == 200
        assert browser.get(API + "/auth/me").status_code == 200


# N05 · allowed cross-origin callers can read the new error responses.

def test_the_body_limit_sits_inside_dashboard_cors():
    order = [entry.cls for entry in app.user_middleware]
    assert order.index(RequestIdMiddleware) < order.index(DashboardCors) < order.index(BodySizeLimit)


def test_a_separate_dashboard_can_read_a_413(monkeypatch):
    monkeypatch.setenv("SOURCED_DASHBOARD_ORIGIN", "https://admin.sourced.example")
    inner = FastAPI()

    @inner.post("/api/v1/widgets")
    def create(body: dict):
        return body

    inner.add_exception_handler(CodedHTTPException, coded_error)
    stack = DashboardCors(BodySizeLimit(inner, max_body=100, max_upload=1000))
    with TestClient(stack) as browser:
        refused = browser.post("/api/v1/widgets", json={"name": "x" * 500}, headers={"Origin": "https://admin.sourced.example"})
    assert refused.status_code == 413
    assert refused.headers["access-control-allow-origin"] == "https://admin.sourced.example"


def test_a_busy_widget_preview_is_readable_by_the_allowed_origin(public_env, sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PDF_CONCURRENCY", "1")
    monkeypatch.setattr(pdf_tasks, "REQUEST_WAIT_SECONDS", 0.2)
    with client() as admin, client("192.0.2.11") as visitor:
        signup(admin, "alpha")
        document = ready_document(admin, sessions)
        key = create_widget(admin, [document])["site_key"]
        citation, = ask_widget(visitor, key).json()["citations"]
        slots = pdf_tasks.pdf_slots()
        assert slots.acquire(timeout=1)
        try:
            busy = visitor.get(citation["preview_url"], headers={"Origin": ORIGIN})
        finally:
            slots.release()
        assert busy.status_code == 503 and busy.json()["code"] == "pdf_busy"
        assert busy.headers["access-control-allow-origin"] == ORIGIN
        assert visitor.get(citation["preview_url"], headers={"Origin": ORIGIN}).status_code == 200


# Test gaps the re-audit noted.

def test_the_real_chat_path_lists_every_document_the_models_read():
    first, second = uuid4(), uuid4()
    hits = [DocumentHit(Chunk("a", "a.pdf", 1, "Alpha text."), 0.9, first),
            DocumentHit(Chunk("b", "b.pdf", 2, "Beta text."), 0.5, second)]
    answer = Answer(text="Alpha [1].", found=True, citations=[Citation(1, "a.pdf", 1, "Alpha text.", True)],
                    hits=hits, search_query="q", status=ANSWERED)
    response = to_response(answer, "q")
    assert response.context_document_ids == sorted([str(first), str(second)])
    assert "context_document_ids" not in response.model_dump() and "context_document_ids" not in response.model_dump_json()


def test_the_body_limit_counts_real_frames_and_never_passes_the_one_that_crosses_it():
    seen = []

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            seen.append(len(message.get("body", b"")))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    frames = [{"type": "http.request", "body": b"x" * 600, "more_body": True} for _ in range(3)]
    sent = []

    async def receive():
        return frames.pop(0)

    async def send(message):
        sent.append(message)

    async def call():
        scope = {"type": "http", "method": "POST", "path": "/api/v1/chat", "headers": []}
        await BodySizeLimit(inner, max_body=1000, max_upload=5000)(scope, receive, send)

    asyncio.run(call())
    assert seen == [600]  # the second frame, which crosses 1,000 bytes, never reached the app
    assert sent[0]["status"] == 413

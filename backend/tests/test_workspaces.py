import subprocess
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.pdfgen import canvas
from sqlalchemy import func, select, update

from backend.app import pdf_tasks
from backend.app.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from backend.app.db.models import Document, DocumentChunk, DocumentFile, LoginSession, PagePreview, User
from backend.app.main import app
from backend.app.pdf_tasks import PREVIEW_MAX_SIDE, PdfTaskError, run_pdf_task
from backend.app.settings import DocumentLimits
from backend.app.store import PgVectorStore
from backend.app.worker import claim_job, complete_job, process_one
from rag.offline import HashingEmbedder, KeywordChat

from .retrieval_parity import compare

API = "/api/v1"
PASSWORD = "fictional test passphrase"
QUESTION = "What is the Sourced workspace verification phrase?"
ALPHA_TEXT = "The Sourced workspace verification phrase is Orchard Lantern."
BETA_TEXT = "The Sourced workspace verification phrase is Meadow Compass."


def make_pdf(text="", *, pages=1, size=(612, 792)):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=size)
    for _ in range(pages):
        if text:
            pdf.drawString(25, size[1] - 40, text)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def csrf(client):
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


def signup(client, prefix):
    response = client.post(f"{API}/auth/signup", json={
        "email": f"{prefix}@example.com", "password": PASSWORD, "workspace_name": prefix,
    })
    assert response.status_code == 201, response.text
    return response


def upload(client, data):
    return client.post(f"{API}/documents", headers=csrf(client), files={"file": ("handbook.pdf", data, "application/pdf")})


def queue(client, data):
    response = upload(client, data)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return UUID(response.json()["id"])


def ask(client, ids=None, **headers):
    body = {"question": QUESTION}
    if ids is not None:
        body["document_ids"] = [str(value) for value in ids]
    return client.post(f"{API}/chat", headers={**csrf(client), **headers}, json=body)


def test_accounts_and_secure_sessions(workspace_client, sessions):
    client = workspace_client
    assert client.get(f"{API}/health").json() == {"status": "ok"}
    for path in ("/auth/me", "/documents", "/providers"):
        assert client.get(API + path).status_code == 401
    assert client.post(f"{API}/chat", json={"question": QUESTION}).status_code == 401

    invalid = "short-pass"
    response = client.post(f"{API}/auth/signup", json={"email": "alpha@example.com", "password": invalid})
    assert response.status_code == 422
    assert invalid not in response.text
    response = signup(client, "alpha")
    member = response.json()
    cookies = response.headers.get_list("set-cookie")
    cookie = next(value for value in cookies if value.startswith(SESSION_COOKIE))
    assert all(flag in cookie.lower() for flag in ("httponly", "samesite=strict", "secure", "path=/"))
    assert "domain=" not in cookie.lower()
    assert client.get(f"{API}/auth/me").json() == member
    assert any(item["name"] == "offline" and item["ready"] for item in client.get(f"{API}/providers").json())

    with sessions() as db:
        assert db.scalar(select(User.password_hash)).startswith("$argon2id$")
        assert db.scalar(select(LoginSession.token_hash)) != client.cookies[SESSION_COOKIE]
        assert db.scalar(select(LoginSession.csrf_hash)) != client.cookies[CSRF_COOKIE]

    login = {"email": "ALPHA@example.com", "password": PASSWORD}
    assert client.post(f"{API}/auth/signup", json=login).status_code == 409
    assert client.post(f"{API}/auth/login", json={**login, "password": "incorrect"}).status_code == 401
    response = client.post(f"{API}/auth/login", json=login)
    assert response.status_code == 200
    assert response.json() == member
    current_cookie = client.cookies[SESSION_COOKIE]
    assert client.post(f"{API}/auth/logout").status_code == 403
    assert client.post(f"{API}/auth/logout", headers={CSRF_HEADER: "x" * 43}).status_code == 403
    assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 204
    assert client.get(f"{API}/auth/me", headers={"Cookie": f"{SESSION_COOKIE}={current_cookie}"}).status_code == 401

    assert client.post(f"{API}/auth/login", json=login).status_code == 200
    with sessions.begin() as db:
        db.execute(update(LoginSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert client.get(f"{API}/auth/me").status_code == 401


def test_documents_chat_and_previews_are_isolated_between_workspaces(workspace_client, sessions):
    alpha = workspace_client
    alpha_workspace = UUID(signup(alpha, "alpha").json()["workspace_id"])
    alpha_id = queue(alpha, make_pdf(ALPHA_TEXT, size=(5000, 6000)))
    alpha_path = f"{API}/documents/{alpha_id}"
    alpha_preview = f"{alpha_path}/pages/1.png"
    assert alpha.get(alpha_path).json()["status"] == "queued"
    assert alpha.get(alpha_preview).status_code == 404
    assert ask(alpha).json()["status"] == "not_found"
    limits = DocumentLimits.from_env()
    embedder = HashingEmbedder()
    assert process_one(sessions, limits, embedder)

    with TestClient(app, base_url="https://sourced.example") as beta:
        beta_workspace = UUID(signup(beta, "beta").json()["workspace_id"])
        assert beta_workspace != alpha_workspace
        assert beta.get(f"{API}/documents", headers={"X-Workspace-ID": str(alpha_workspace)}).json() == []
        beta_id = queue(beta, make_pdf(BETA_TEXT))
        assert process_one(sessions, limits, embedder)

        for client, document_id, phrase, other in (
            (alpha, alpha_id, "Orchard Lantern", "Meadow Compass"),
            (beta, beta_id, "Meadow Compass", "Orchard Lantern"),
        ):
            documents = client.get(f"{API}/documents").json()
            assert [doc["id"] for doc in documents] == [str(document_id)]
            assert documents[0]["status"] == "ready" and documents[0]["progress"] == 100
            response = ask(client)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "answered"
            assert phrase in response.json()["text"]
            assert other not in response.text
            citation, = response.json()["citations"]
            assert citation["document_id"] == str(document_id)
            assert citation["source"] == "handbook.pdf"
            assert citation["verified"]
            assert citation["preview_url"] == f"{API}/documents/{document_id}/pages/1.png"
            assert ask(client, [document_id]).json()["status"] == "answered"

        assert beta.get(alpha_path).status_code == 404
        assert beta.get(alpha_preview).status_code == 404
        assert ask(beta, [alpha_id]).status_code == 404
        assert ask(beta, [alpha_id, beta_id]).status_code == 404
        assert beta.delete(alpha_path, headers=csrf(beta)).status_code == 204
        assert alpha.get(alpha_path).status_code == 200

        for mode in ("dense", "bm25", "hybrid"):
            assert PgVectorStore(sessions, embedder, beta_workspace, {alpha_id}).search(QUESTION, mode=mode) == []
            results = PgVectorStore(sessions, embedder, beta_workspace).search(QUESTION, mode=mode)
            assert results and {hit.document_id for hit in results} == {beta_id}

    assert alpha.get(f"{alpha_path}/pages/0.png").status_code == 404
    assert alpha.get(f"{alpha_path}/pages/2.png").status_code == 404
    response = alpha.get(alpha_preview)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(response.content)) as image:
        assert max(image.size) <= PREVIEW_MAX_SIDE
    assert alpha.get(alpha_preview).content == response.content
    with sessions() as db:
        rows = db.scalars(select(DocumentChunk)).all()
        assert len({row.engine_chunk_id for row in rows}) == 1
        assert len({row.id for row in rows}) == 2
        assert db.get(PagePreview, (alpha_id, 1)).png == response.content


def test_delete_removes_the_pdf_chunks_vectors_and_previews(workspace_client, sessions):
    client = workspace_client
    workspace_id = UUID(signup(client, "alpha").json()["workspace_id"])
    pdf = make_pdf(ALPHA_TEXT)
    document_id = queue(client, pdf)
    path = f"{API}/documents/{document_id}"
    embedder = HashingEmbedder()
    limits = DocumentLimits.from_env()
    subprocess.run([sys.executable, "-m", "backend.app.worker", "--once"], check=True, timeout=45)
    assert client.get(f"{path}/pages/1.png").status_code == 200
    with sessions() as db:
        assert db.get(DocumentFile, document_id).pdf == pdf
        assert db.get(Document, document_id).sha256 == sha256(pdf).hexdigest()
        assert len(db.scalar(select(DocumentChunk.embedding).where(DocumentChunk.document_id == document_id))) == 512
        assert db.get(PagePreview, (document_id, 1)) is not None
    assert client.delete(path).status_code == 403
    assert client.delete(path, headers=csrf(client)).status_code == 204
    assert client.delete(path, headers=csrf(client)).status_code == 204
    assert client.get(path).status_code == client.get(f"{path}/pages/1.png").status_code == 404
    assert ask(client).json()["status"] == "not_found"
    store = PgVectorStore(sessions, embedder, workspace_id)
    assert all(not store.search(QUESTION, mode=mode) for mode in ("dense", "bm25", "hybrid"))
    with sessions() as db:
        assert db.get(Document, document_id) is None
        assert db.get(DocumentFile, document_id) is None
        assert db.get(PagePreview, (document_id, 1)) is None
        assert db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document_id)) == 0
    assert not process_one(sessions, limits, embedder)


def test_chat_allowlist_also_scopes_blocked_passages(workspace_client, sessions, monkeypatch):
    client = workspace_client
    signup(client, "alpha")
    alpha_id = queue(client, make_pdf(ALPHA_TEXT))
    beta_id = queue(client, make_pdf(BETA_TEXT))
    embedder = HashingEmbedder()
    limits = DocumentLimits.from_env()
    assert process_one(sessions, limits, embedder)
    assert process_one(sessions, limits, embedder)
    for document_id, phrase in ((alpha_id, "Orchard Lantern"), (beta_id, "Meadow Compass")):
        result = ask(client, [document_id]).json()
        assert phrase in result["text"]
        assert {citation["document_id"] for citation in result["citations"]} == {str(document_id)}
    assert ask(client, []).json()["status"] == "not_found"

    def ungrounded(self, system, messages, max_tokens=900):
        return '{"coverage":"full","answer":"Fabricated answer 99999 [1]."}'

    monkeypatch.setattr(KeywordChat, "complete", ungrounded)
    response = ask(client, [beta_id])
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "unverified"
    assert result["citations"] == []
    assert {passage["document_id"] for passage in result["passages"]} == {str(beta_id)}
    assert "99999" not in response.text and "Orchard Lantern" not in response.text


def test_upload_limits_and_worker_failures(workspace_client, sessions):
    client = workspace_client
    signup(client, "alpha")
    assert client.post(f"{API}/documents", files={"file": ("handbook.pdf", make_pdf(ALPHA_TEXT))}).status_code == 403
    assert upload(client, b"%PDF-" + b"0" * (1024**2)).status_code == 413
    assert upload(client, b"not a PDF").status_code == 422
    assert upload(client, make_pdf(pages=81)).status_code == 422
    assert client.get(f"{API}/documents").json() == []

    blank_id = queue(client, make_pdf())
    text_id = queue(client, make_pdf(ALPHA_TEXT))
    limits = DocumentLimits(1024**2, 80, 30, 20)
    assert process_one(sessions, limits, HashingEmbedder())
    assert process_one(sessions, limits, HashingEmbedder())
    for doc_id, error in ((blank_id, "no selectable text"), (text_id, "too much text")):
        document = client.get(f"{API}/documents/{doc_id}").json()
        assert document["status"] == "failed"
        assert error in document["error"]

    class InvalidEmbedder(HashingEmbedder):
        def embed(self, texts):
            return [[float("nan")] for _ in texts]

    invalid_id = queue(client, make_pdf(ALPHA_TEXT))
    assert process_one(sessions, DocumentLimits.from_env(), InvalidEmbedder())
    document = client.get(f"{API}/documents/{invalid_id}").json()
    assert document["status"] == "failed"
    assert "embedding provider" in document["error"]
    assert ask(client).json()["status"] == "not_found"


def test_worker_reclaims_jobs_reindexes_and_respects_deletion(workspace_client, sessions):
    client = workspace_client
    workspace_id = UUID(signup(client, "alpha").json()["workspace_id"])
    document_id = queue(client, make_pdf(ALPHA_TEXT))
    original = HashingEmbedder()
    limits = DocumentLimits.from_env()
    job = claim_job(sessions, original.name)
    assert job is not None and job.document_id == document_id
    assert client.get(f"{API}/documents/{document_id}").json()["status"] == "processing"
    assert not process_one(sessions, limits, original)
    with sessions.begin() as db:
        db.get(Document, document_id).lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert process_one(sessions, limits, original)
    complete_job(sessions, job, original.name, [], [], "Stale worker failed.")
    assert client.get(f"{API}/documents/{document_id}").json()["status"] == "ready"

    class NewModel(HashingEmbedder):
        name = "offline:hashing-v2"

    replacement = NewModel(dims=256)
    store = PgVectorStore(sessions, replacement, workspace_id)
    assert store.search(QUESTION, mode="hybrid") == []
    assert process_one(sessions, limits, replacement)
    assert store.search(QUESTION, mode="hybrid")
    assert PgVectorStore(sessions, original, workspace_id).search(QUESTION, mode="hybrid") == []
    with sessions() as db:
        document = db.get(Document, document_id)
        assert document.embedding_model == replacement.name
        assert document.attempts == 1
        assert len(db.scalar(select(DocumentChunk.embedding))) == 256

    job = claim_job(sessions, original.name)
    assert job is not None and job.document_id == document_id
    assert client.delete(f"{API}/documents/{document_id}", headers=csrf(client)).status_code == 204
    complete_job(sessions, job, original.name, [], [], "Finished after deletion.")
    assert store.search(QUESTION, mode="hybrid") == []
    assert client.get(f"{API}/documents/{document_id}").status_code == 404


def test_unexpected_ingestion_error_fails_on_third_attempt(workspace_client, sessions):
    client = workspace_client
    signup(client, "alpha")
    document_id = queue(client, make_pdf(ALPHA_TEXT))
    path = f"{API}/documents/{document_id}"
    limits = DocumentLimits.from_env()

    class BrokenEmbedder(HashingEmbedder):
        def embed(self, texts):
            raise RuntimeError("Private diagnostic")

    embedder = BrokenEmbedder()
    for attempt in (1, 2):
        with pytest.raises(RuntimeError, match="Private diagnostic"):
            process_one(sessions, limits, embedder)
        assert not process_one(sessions, limits, embedder)
        with sessions.begin() as db:
            document = db.get(Document, document_id)
            assert document.status == "processing" and document.attempts == attempt
            document.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert process_one(sessions, limits, embedder)
    document = client.get(path).json()
    assert document["status"] == "failed"
    assert "after three attempts" in document["error"]
    assert "Private diagnostic" not in document["error"]
    with sessions() as db:
        record = db.get(Document, document_id)
        assert record.attempts == 3 and record.claim_id is None and record.lease_until is None
    assert not process_one(sessions, limits, embedder)


def test_expired_final_claim_fails_without_reclaiming_it(workspace_client, sessions):
    client = workspace_client
    signup(client, "alpha")
    exhausted_id = queue(client, make_pdf(ALPHA_TEXT))
    embedder = HashingEmbedder()
    for attempt in (1, 2, 3):
        job = claim_job(sessions, embedder.name)
        assert job.document_id == exhausted_id and job.attempts == attempt
        with sessions.begin() as db:
            db.get(Document, exhausted_id).lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    queued_id = queue(client, make_pdf(BETA_TEXT))
    assert process_one(sessions, DocumentLimits.from_env(), embedder)
    failed = client.get(f"{API}/documents/{exhausted_id}").json()
    assert failed["status"] == "failed" and "after three attempts" in failed["error"]
    assert client.get(f"{API}/documents/{queued_id}").json()["status"] == "ready"
    with sessions() as db:
        assert db.get(Document, exhausted_id).attempts == 3


@pytest.mark.parametrize("payload", [
    b'{"broken":',
    b'{"id":"one","source":"handbook.pdf","page":1,"text":"This should be in a list."}',
    b'[{"id":"one","source":"handbook.pdf","page":1}]',
    b'[{"id":"one","source":"handbook.pdf","page":true,"text":"Invalid page type."}]',
])
def test_malformed_extraction_is_a_pdf_task_error(monkeypatch, payload):
    monkeypatch.setattr(pdf_tasks, "run_pdf_task", lambda *args, **kwargs: payload)
    with pytest.raises(PdfTaskError, match="The PDF text could not be extracted"):
        pdf_tasks.extract_chunks(b"sample", "handbook.pdf", 1000, 20)


def test_pgvector_hybrid_retrieval_matches_engine(sessions):
    compare(sessions)


def test_pdf_subprocess_timeout_has_a_safe_error(monkeypatch):
    monkeypatch.setenv("SOURCED_DATABASE_URL", "a private database URL")

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 2
        assert "SOURCED_DATABASE_URL" not in kwargs["env"]
        raise subprocess.TimeoutExpired(command, 2)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(PdfTaskError, match="took too long"):
        run_pdf_task("extract", b"sample", "sample.pdf", "1000", timeout=2)

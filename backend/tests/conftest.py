import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.app.main import app

from .postgres import disposable_postgres


@pytest.fixture(scope="session")
def postgres():
    with disposable_postgres() as engine:
        yield engine


@pytest.fixture
def sessions(postgres, monkeypatch):
    with postgres.begin() as connection:
        connection.execute(text("TRUNCATE workspaces, users CASCADE"))
    monkeypatch.setenv("SOURCED_DATABASE_URL", postgres.url.render_as_string(hide_password=False))
    monkeypatch.setenv("RAG_OFFLINE_MODE", "1")
    monkeypatch.setenv("TOP_K", "5")
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    monkeypatch.setenv("MAX_UPLOAD_PAGES", "80")
    monkeypatch.setenv("SOURCED_MAX_PDF_CHARS", "500000")
    monkeypatch.setenv("SOURCED_PDF_TIMEOUT_SECONDS", "20")
    return sessionmaker(postgres)


@pytest.fixture
def workspace_client(sessions):
    with TestClient(app, base_url="https://sourced.example") as client:
        yield client

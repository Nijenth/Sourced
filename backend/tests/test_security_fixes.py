"""Security audit S10 (sessions end when the demo password changes) and S08 (preview grants stay out of logs)."""

import logging

from backend.app.seed_demo import seed
from backend.app.widget_sessions import RedactAccessCredentials

from .test_widgets import client
from .test_workspaces import API, PASSWORD

HOSTED = "https://chat.sourced.example"


def test_changing_the_demo_password_signs_out_existing_sessions(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", HOSTED)
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_DEMO_ADMIN_EMAIL", "demo@example.com")
    monkeypatch.delenv("SOURCED_DEMO_SITE_KEY", raising=False)
    seed(sessions, "demo@example.com", PASSWORD, "offline")
    with client() as old, client("192.0.2.11") as kept:
        for browser in (old, kept):
            assert browser.post(API + "/auth/login", json={"email": "demo@example.com", "password": PASSWORD}).status_code == 200
            assert browser.get(API + "/auth/me").status_code == 200

        # Re-running with the same password (every restart) keeps people signed in.
        seed(sessions, "demo@example.com", PASSWORD, "offline")
        assert kept.get(API + "/auth/me").status_code == 200

        seed(sessions, "demo@example.com", PASSWORD + " rotated", "offline")
        for browser in (old, kept):
            assert browser.get(API + "/auth/me").status_code == 401
        assert old.post(API + "/auth/login", json={
            "email": "demo@example.com", "password": PASSWORD + " rotated",
        }).status_code == 200
        assert old.get(API + "/auth/me").status_code == 200


def logged(target: str) -> str:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("192.0.2.1:1000", "GET", target, "1.1", 200), None)
    assert RedactAccessCredentials().filter(record)
    return record.args[2]


def test_preview_grants_are_redacted_from_access_logs():
    grant = "WyJwa18xMjMiLCJodHRwczovL2EuZXhhbXBsZSIsImQiLDYsMTcwMCwxLG51bGxd." + "a" * 64
    assert logged(f"/api/v1/public/previews/{grant}.png") == "/api/v1/public/previews/[redacted].png"
    assert grant not in logged(f"/api/v1/public/previews/{grant}.png?x=1")
    # Existing redactions and ordinary paths are unchanged.
    assert "sk_" not in logged("/c/pk_demo?secret=sk_" + "A" * 43)
    assert logged("/api/v1/public/chat?site_key=pk_demo") == "/api/v1/public/chat?site_key=pk_demo"

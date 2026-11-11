"""M7: same-origin dashboard, closed sign-up, the seeded demo and the in-process worker."""

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.db.models import Document, User, Widget, WidgetDocument
from backend.app.seed_demo import DEMO_WIDGET_NAME, seed
from backend.app.settings import DocumentLimits
from backend.app.web import mount_dashboard
from backend.app.worker import process_one
from rag.offline import HashingEmbedder

from .test_widgets import client
from .test_workspaces import ALPHA_TEXT, API, PASSWORD, csrf, make_pdf, signup

HOSTED = "https://chat.sourced.example"


def test_dashboard_is_served_without_shadowing_api_routes(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log('dashboard')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("outside the build")
    monkeypatch.setenv("SOURCED_WEB_DIST", str(tmp_path))
    app = FastAPI()

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    assert mount_dashboard(app)
    with TestClient(app) as browser:
        for path in ("/", "/documents", "/ask", "/widget/settings"):
            page = browser.get(path)
            assert page.status_code == 200 and "id=root" in page.text
            assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
            assert "script-src 'self'" in page.headers["content-security-policy"]
        assert browser.get("/assets/app-1234.js").text == "console.log('dashboard')"
        assert browser.get("/favicon.svg").text == "<svg/>"
        assert browser.get("/api/v1/health").json() == {"status": "ok"}
        for path in ("/api/v1/unknown", "/api", "/c/pk_missing", "/demo/", "/widget-assets/9.9.9/widget.js"):
            assert browser.get(path).status_code == 404, path
        assert "outside the build" not in browser.get("/../secret.txt").text
        assert "outside the build" not in browser.get("/%2e%2e/secret.txt").text


def test_no_dashboard_build_means_no_page_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCED_WEB_DIST", str(tmp_path / "missing"))
    assert not mount_dashboard(FastAPI())


def test_sign_up_can_be_closed(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_ALLOW_SIGNUP", "0")
    with client() as browser:
        response = browser.post(API + "/auth/signup", json={"email": "new@example.com", "password": PASSWORD})
        assert response.status_code == 403
        assert "closed" in response.json()["detail"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_seeded_demo_is_idempotent_and_serves_the_widget(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", HOSTED)
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_DEMO_ADMIN_EMAIL", "Demo@Example.com")
    monkeypatch.delenv("SOURCED_DEMO_SITE_KEY", raising=False)
    key = seed(sessions, "Demo@Example.com", PASSWORD, "offline")
    assert seed(sessions, "demo@example.com", PASSWORD + " changed", "offline") == key
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Document)) == 1
        widget = db.scalar(select(Widget))
        assert (widget.name, widget.access_mode, widget.hosted_page_enabled, widget.judge_enabled) == (
            DEMO_WIDGET_NAME, "public", True, False,
        )
        assert db.scalar(select(func.count()).select_from(WidgetDocument)) == 1
    assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())

    with client() as browser:
        page = browser.get("/demo/")
        assert page.status_code == 200
        assert f'data-site-key="{key}"' in page.text and "Brightleaf" in page.text
        assert browser.get("/demo", follow_redirects=False).headers["location"] == "/demo/"
        assert browser.get("/demo/site.css").status_code == 200
        # The demo page is on the Sourced origin, which the seeded widget allows through its hosted-page opt-in.
        answer = browser.post(API + "/public/chat", params={"site_key": key}, headers={
            "X-Sourced-Key": key, "Origin": HOSTED,
        }, json={"question": "How many days of annual leave do I get?"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["status"] == "answered"
        # The configured password replaced the old one.
        assert browser.post(API + "/auth/login", json={"email": "demo@example.com", "password": PASSWORD}).status_code == 401
        assert browser.post(API + "/auth/login", json={
            "email": "demo@example.com", "password": PASSWORD + " changed",
        }).status_code == 200


def test_demo_page_ignores_widgets_outside_the_demo_admins_workspace(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", HOSTED)
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_DEMO_ADMIN_EMAIL", "demo@example.com")
    monkeypatch.delenv("SOURCED_DEMO_SITE_KEY", raising=False)
    with client() as browser:
        signup(browser, "intruder")
        response = browser.post(API + "/widgets", headers=csrf(browser), json={
            "name": DEMO_WIDGET_NAME, "provider": "offline", "hosted_page_enabled": True,
        })
        assert response.status_code == 201
        assert browser.get("/demo/").status_code == 404


def test_embedded_worker_indexes_uploads_inside_the_api(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_EMBEDDED_WORKER", "1")
    with client() as browser:
        signup(browser, "alpha")
        uploaded = browser.post(API + "/documents", headers=csrf(browser),
                                files={"file": ("handbook.pdf", make_pdf(ALPHA_TEXT), "application/pdf")})
        assert uploaded.status_code == 202
        document = uploaded.json()["id"]
        deadline = time.monotonic() + 30
        while browser.get(f"{API}/documents/{document}").json()["status"] != "ready":
            assert time.monotonic() < deadline, "The embedded worker didn't index the upload."
            time.sleep(0.2)


def test_public_origin_defaults_to_renders_address(monkeypatch):
    from backend.app.channels import ChannelSettings

    monkeypatch.delenv("SOURCED_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://sourced-ab12.onrender.com/")
    assert ChannelSettings.from_env().public_origin == "https://sourced-ab12.onrender.com"
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", "https://chat.example.com")
    assert ChannelSettings.from_env().public_origin == "https://chat.example.com"

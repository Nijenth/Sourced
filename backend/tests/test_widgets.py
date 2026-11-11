import base64
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.app import channels, public
from backend.app.db.models import Widget
from backend.app.main import DashboardCors, app
from backend.app.settings import DocumentLimits
from backend.app.worker import process_one
from rag.offline import HashingEmbedder, KeywordChat

from .test_workspaces import ALPHA_TEXT, API, BETA_TEXT, QUESTION, csrf, make_pdf, queue, signup

PUBLIC = API + "/public"
ORIGIN = "https://people.brightleaf.example"
HOSTED = "https://chat.sourced.example"
FOREIGN = "https://unlisted.example"
FALLBACK = "Try the People team."


def test_dashboard_cors_only_grants_the_configured_origin_for_admin_routes(monkeypatch):
    async def response(_request):
        return JSONResponse({"ok": True})

    monkeypatch.setenv("SOURCED_DASHBOARD_ORIGIN", "https://admin.example.com")
    routes = [Route(API + "/widgets", response), Route(PUBLIC + "/chat", response)]
    with TestClient(DashboardCors(Starlette(routes=routes))) as browser:
        approved = browser.get(API + "/widgets", headers={"Origin": "https://admin.example.com"})
        assert approved.headers["access-control-allow-origin"] == "https://admin.example.com"
        assert approved.headers["access-control-allow-credentials"] == "true"
        assert "access-control-allow-origin" not in browser.get(API + "/widgets", headers={"Origin": FOREIGN}).headers
        assert "access-control-allow-origin" not in browser.get(PUBLIC + "/chat", headers={"Origin": "https://admin.example.com"}).headers


@pytest.fixture
def public_env(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", HOSTED)
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    monkeypatch.setenv("SOURCED_WIDGET_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setenv("SOURCED_IP_REQUESTS_PER_MINUTE", "100")
    monkeypatch.setenv("SOURCED_WORKSPACE_QUESTIONS_PER_DAY", "100")
    monkeypatch.delenv("SOURCED_TRUSTED_PROXIES", raising=False)


def client(ip="192.0.2.10"):
    return TestClient(app, base_url=HOSTED, client=(ip, 1000))


def create_widget(browser, ids=(), **settings):
    response = browser.post(API + "/widgets", headers=csrf(browser), json={
        "name": "People help", "title": "Ask the handbook", "provider": "offline",
        "allowed_origins": [ORIGIN], "document_ids": [str(id) for id in ids], "fallback_text": FALLBACK,
        **settings,
    })
    assert response.status_code == 201, response.text
    return response.json()


def ask_widget(browser, key, *, origin=ORIGIN, body=None, **headers):
    return browser.post(PUBLIC + "/chat", params={"site_key": key}, json=body or {"question": QUESTION}, headers={
        "X-Sourced-Key": key, "Origin": origin, **headers,
    })


def test_widget_settings_ownership_rotation_and_asset_integrity(public_env, sessions):
    with client() as owner, client("192.0.2.11") as other:
        signup(owner, "alpha")
        signup(other, "beta")
        owned = queue(owner, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        assert other.post(API + "/widgets", headers=csrf(other), json={"document_ids": [str(owned)]}).status_code == 404
        assert owner.post(API + "/widgets", headers=csrf(owner), json={"access_mode": "signed_in"}).status_code == 422
        assert owner.post(API + "/widgets", headers=csrf(owner), json={"allowed_origins": ["https://*.example.com"]}).status_code == 422
        queued = queue(owner, make_pdf(BETA_TEXT))
        assert owner.post(API + "/widgets", headers=csrf(owner), json={"document_ids": [str(queued)]}).status_code == 422
        widget = create_widget(owner, [owned])
        path = API + "/widgets/" + widget["id"]
        assert other.get(path).status_code == 404
        assert other.patch(path, headers=csrf(other), json={"title": "Other"}).status_code == 404
        assert other.delete(path, headers=csrf(other)).status_code == 404
        assert owner.patch(path, json={"title": "Help desk"}).status_code == 403
        assert owner.patch(path, headers=csrf(owner), json={"title": "Help desk"}).json()["title"] == "Help desk"

        script = owner.get(widget["script_url"])
        digest = base64.b64encode(hashlib.sha384(script.content).digest()).decode()
        assert script.status_code == 200 and "immutable" in script.headers["cache-control"]
        assert widget["integrity"] == "sha384-" + digest
        assert script.headers["access-control-allow-origin"] == "*"
        old_key = widget["site_key"]
        rotated = owner.post(path + "/rotate-keys", headers=csrf(owner), json={"site_key": True, "secret_key": True})
        assert rotated.status_code == 200
        keys = rotated.json()
        assert rotated.headers["cache-control"] == "private, no-store"
        assert keys["site_key"] != old_key and keys["secret_key"].startswith("sk_")
        with sessions() as db:
            assert db.get(Widget, UUID(widget["id"])).secret_key_hash == hashlib.sha256(keys["secret_key"].encode()).hexdigest()
        assert keys["secret_key"] not in owner.get(path).text
        assert ask_widget(owner, keys["secret_key"]).status_code == 404
        assert owner.get(PUBLIC + f"/widgets/{old_key}/config").status_code == 404
        assert owner.delete(path, headers=csrf(owner)).status_code == 204
        assert owner.get(PUBLIC + f"/widgets/{keys['site_key']}/config").status_code == 404


def test_public_chat_cors_document_scope_and_signed_previews(public_env, sessions, monkeypatch):
    with client() as admin, client("192.0.2.11") as visitor, client("192.0.2.12") as stranger:
        signup(admin, "alpha")
        visible = queue(admin, make_pdf(ALPHA_TEXT))
        queue(admin, make_pdf(BETA_TEXT))
        for _ in range(2):
            assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        widget = create_widget(admin, [visible], hosted_page_enabled=True)
        key = widget["site_key"]
        config_url = PUBLIC + f"/widgets/{key}/config"
        config = visitor.get(config_url, headers={"Origin": ORIGIN})
        assert set(config.json()) == {"title", "greeting", "brand_color", "fallback_text", "access_mode"}
        assert config.headers["access-control-allow-origin"] == ORIGIN
        assert "access-control-allow-credentials" not in config.headers
        assert visitor.get(config_url, headers={"Origin": FOREIGN}).status_code == 403
        assert visitor.get(widget["hosted_url"]).status_code == 200

        headers = {
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-sourced-key",
        }
        preflight = visitor.options(PUBLIC + "/chat", params={"site_key": key}, headers=headers)
        assert preflight.status_code == 204 and preflight.headers["access-control-allow-origin"] == ORIGIN
        assert "access-control-allow-credentials" not in preflight.headers
        assert visitor.options(PUBLIC + "/chat", params={"site_key": key}, headers={**headers, "Origin": FOREIGN}).status_code == 403
        assert visitor.post(PUBLIC + "/chat", headers={"X-Sourced-Key": key}, json={"question": QUESTION}).status_code == 403
        assert ask_widget(visitor, key, origin=FOREIGN).status_code == 403
        assert ask_widget(visitor, key, body={"question": QUESTION, "provider": "openai"}).status_code == 422
        assert ask_widget(visitor, key, body={"question": QUESTION, "document_ids": []}).status_code == 422
        answer = ask_widget(visitor, key)
        assert answer.status_code == 200 and answer.json()["status"] == "answered"
        assert "Orchard Lantern" in answer.json()["text"] and "Meadow Compass" not in answer.text
        assert answer.headers["access-control-allow-origin"] == ORIGIN
        citation, = answer.json()["citations"]
        assert citation["document_id"] == str(visible)
        preview = citation["preview_url"]
        png = visitor.get(preview, headers={"Origin": ORIGIN})
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        assert visitor.get(preview, headers={"Origin": FOREIGN}).status_code == 404
        assert stranger.get(preview, headers={"Origin": ORIGIN, "X-Forwarded-For": "192.0.2.11"}).status_code == 404
        assert visitor.get(preview[:-5] + ("0" if preview[-5] != "0" else "1") + ".png", headers={"Origin": ORIGIN}).status_code == 404
        with monkeypatch.context() as expired:
            expired.setattr(public, "time", SimpleNamespace(time=lambda: 2_000_000_000))
            assert visitor.get(preview, headers={"Origin": ORIGIN}).status_code == 404

        def ungrounded(self, system, messages, max_tokens=900):
            return '{"coverage":"full","answer":"Private diagnostic 99999 [1]."}'

        monkeypatch.setattr(KeywordChat, "complete", ungrounded)
        blocked = ask_widget(visitor, key)
        assert blocked.json()["status"] == "unverified"
        assert blocked.json()["passages"] == blocked.json()["citations"] == []
        assert blocked.json()["fallback"] == FALLBACK and "99999" not in blocked.text
        assert admin.delete(API + f"/documents/{visible}", headers=csrf(admin)).status_code == 204
        assert visitor.get(preview, headers={"Origin": ORIGIN}).status_code == 404
        assert admin.get(API + "/widgets/" + widget["id"]).json()["document_ids"] == []
        not_found = ask_widget(visitor, key, origin=HOSTED).json()
        assert not_found["status"] == "not_found" and not_found["fallback"] == FALLBACK
        usage = admin.get(API + "/usage").json()
        assert [(row["channel_label"], row["questions"], row["answered"], row["blocked"]) for row in usage] == [("People help", 3, 1, 1)]


def test_hosted_page_and_sourced_origin_require_per_widget_opt_in(public_env):
    with client() as owner, client("192.0.2.11") as visitor:
        signup(owner, "alpha")
        widget = create_widget(owner, allowed_origins=[ORIGIN, HOSTED])
        key = widget["site_key"]
        path = "/c/" + key
        settings_path = API + "/widgets/" + widget["id"]
        preflight_headers = {
            "Origin": HOSTED, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-sourced-key",
        }
        assert widget["hosted_page_enabled"] is False and widget["hosted_url"] is None
        assert visitor.get(path).status_code == 404
        denied = ask_widget(visitor, key, origin=HOSTED)
        assert denied.status_code == 403 and "access-control-allow-origin" not in denied.headers
        assert visitor.options(PUBLIC + "/chat", params={"site_key": key}, headers=preflight_headers).status_code == 403
        assert ask_widget(visitor, key).status_code == 200

        enabled = owner.patch(settings_path, headers=csrf(owner), json={"hosted_page_enabled": True})
        assert enabled.json()["hosted_url"] == HOSTED + path
        assert visitor.get(path).status_code == 200
        assert ask_widget(visitor, key, origin=HOSTED).status_code == 200
        assert visitor.options(PUBLIC + "/chat", params={"site_key": key}, headers=preflight_headers).status_code == 204
        assert owner.patch(settings_path, headers=csrf(owner), json={"hosted_page_enabled": False}).json()["hosted_url"] is None
        assert visitor.get(path).status_code == 404
        assert ask_widget(visitor, key, origin=HOSTED).status_code == 403


def test_new_widgets_default_accuracy_check_based_on_provider(public_env, monkeypatch):
    with client() as owner:
        signup(owner, "alpha")
        assert create_widget(owner)["judge_enabled"] is False

    monkeypatch.delenv("RAG_OFFLINE_MODE", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "fixture-model")
    with client() as owner:
        signup(owner, "beta")
        widget = create_widget(owner, provider="ollama")
        assert widget["judge_enabled"] is True
        disabled = create_widget(owner, provider="ollama", judge_enabled=False)
        assert disabled["judge_enabled"] is False
        edited = owner.patch(API + "/widgets/" + disabled["id"], headers=csrf(owner), json={"title": "Help"})
        assert edited.json()["judge_enabled"] is False


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 11, 12, 0, 15, tzinfo=timezone.utc)


def test_forwarded_ipv6_bucket_is_shared_by_limits_and_previews(public_env, sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_TRUSTED_PROXIES", "192.0.2.11, 10.2.0.0/24")
    monkeypatch.setenv("SOURCED_IP_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setattr(channels, "datetime", FixedClock)
    with client() as owner, client("192.0.2.11") as proxy:
        signup(owner, "alpha")
        doc = queue(owner, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        key = create_widget(owner, [doc])["site_key"]

        def forwarded(address):
            return {"X-Forwarded-For": f"203.0.113.5, {address}, 10.2.0.1"}

        initial = ask_widget(proxy, key, **forwarded("2001:db8:44:2::a"))
        assert initial.status_code == 200
        preview = initial.json()["citations"][0]["preview_url"]
        same_bucket = forwarded("2001:db8:44:2::b")
        different_bucket = forwarded("2001:db8:44:3::b")
        assert ask_widget(proxy, key, **same_bucket).status_code == 429
        assert ask_widget(proxy, key, **different_bucket).status_code == 200
        assert proxy.get(preview, headers={"Origin": ORIGIN, **same_bucket}).status_code == 200
        assert proxy.get(preview, headers={"Origin": ORIGIN, **different_bucket}).status_code == 404


@pytest.mark.parametrize(("setting", "different_widget", "different_ip"), [
    ("SOURCED_WIDGET_REQUESTS_PER_MINUTE", False, True),
    ("SOURCED_IP_REQUESTS_PER_MINUTE", True, False),
    ("SOURCED_WORKSPACE_QUESTIONS_PER_DAY", True, True),
])
def test_widget_ip_and_workspace_limits(public_env, monkeypatch, setting, different_widget, different_ip):
    monkeypatch.setattr(channels, "datetime", FixedClock)
    monkeypatch.setenv(setting, "1")
    with client() as admin, client("192.0.2.12") as other:
        signup(admin, "alpha")
        first = create_widget(admin)["site_key"]
        second = create_widget(admin)["site_key"] if different_widget else first
        assert ask_widget(admin, first).status_code == 200
        limited = ask_widget(other if different_ip else admin, second, **{"X-Forwarded-For": "203.0.113.20"})
        assert limited.status_code == 429
        assert limited.headers["access-control-allow-origin"] == ORIGIN
        assert int(limited.headers["retry-after"]) > 0
        if setting == "SOURCED_WORKSPACE_QUESTIONS_PER_DAY":
            assert admin.post(API + "/chat", headers=csrf(admin), json={"question": QUESTION}).status_code == 429

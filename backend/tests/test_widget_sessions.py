import base64
import json
from types import SimpleNamespace

from backend.app import widget_sessions
from backend.app.settings import DocumentLimits
from backend.app.worker import process_one
from rag.offline import HashingEmbedder

from .test_widgets import API, HOSTED, ORIGIN, PUBLIC, client, create_widget, public_env
from .test_workspaces import ALPHA_TEXT, QUESTION, csrf, make_pdf, queue, signup


def rotate(admin, widget, *, site_key=False, secret_key=True):
    response = admin.post(API + "/widgets/" + widget["id"] + "/rotate-keys", headers=csrf(admin), json={
        "site_key": site_key, "secret_key": secret_key,
    })
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def session(visitor, widget, secret, **body):
    return visitor.post(API + "/widgets/" + widget["id"] + "/session-tokens", json=body, headers={
        "Authorization": f"Bearer {secret}",
    })


def ask(visitor, token=None, *, hint=None, site_header=None, origin=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if site_header is not None:
        headers["X-Sourced-Key"] = site_header
    if origin is not None:
        headers["Origin"] = origin
    return visitor.post(PUBLIC + "/chat", params={"site_key": hint} if hint is not None else {}, headers=headers, json={"question": QUESTION})


def test_signed_in_widget_chat_and_previews_require_the_issuing_session(public_env, sessions, monkeypatch):
    with client() as admin, client("192.0.2.11") as visitor:
        signup(admin, "alpha")
        doc = queue(admin, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        widget = create_widget(admin, [doc], access_mode="signed_in", hosted_page_enabled=True)
        other = create_widget(admin, [doc], access_mode="signed_in")
        key = widget["site_key"]
        other_key = other["site_key"]
        path = API + "/widgets/" + widget["id"]
        assert widget["has_secret_key"] is False
        secret = rotate(admin, widget)["secret_key"]
        other_secret = rotate(admin, other)["secret_key"]
        settings = admin.get(path)
        assert settings.json()["has_secret_key"] is True
        assert secret not in settings.text
        assert admin.post(path + "/session-tokens", json={}, headers=csrf(admin)).status_code == 401
        assert session(visitor, other, secret).status_code == 401
        assert session(visitor, widget, key).status_code == 401
        assert session(visitor, widget, secret, user_ref="x" * 257).status_code == 422
        assert ask(visitor).status_code == 401
        assert ask(visitor, hint=key, site_header=key, origin=ORIGIN).status_code == 401

        issued = session(visitor, widget, secret, user_ref="member-42")
        assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
        token = issued.json()["token"]
        assert issued.json()["expires_in"] == 900
        payload = token.removeprefix("st_").split(".")[0]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["widget_id"] == widget["id"] and claims["user_ref"] == "member-42"
        other_token = session(visitor, other, other_secret).json()["token"]
        same_widget_token = session(visitor, widget, secret).json()["token"]
        assert token != same_widget_token
        assert ask(visitor, token, hint=other_key, origin=ORIGIN).status_code == 401
        assert ask(visitor, token, site_header=other_key, origin=ORIGIN).status_code == 401
        assert ask(visitor, token, origin="https://unlisted.example").status_code == 403
        assert ask(visitor, token[:-1] + ("a" if token[-1] != "a" else "b")).status_code == 401
        assert visitor.get(path, headers={"Authorization": "Bearer " + token}).status_code == 401
        assert session(visitor, widget, token).status_code == 401

        preflight = visitor.options(PUBLIC + "/chat", params={"site_key": key}, headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert preflight.status_code == 204
        assert "access-control-allow-credentials" not in preflight.headers
        native = ask(visitor, token)
        assert native.json()["status"] == "answered"
        answer = ask(visitor, token, hint=key, origin=ORIGIN)
        assert answer.json()["status"] == "answered"
        assert answer.headers["access-control-allow-origin"] == ORIGIN
        preview = answer.json()["citations"][0]["preview_url"]
        assert visitor.get(preview, headers={"Origin": ORIGIN}).status_code == 401
        for invalid in (key, other_token, same_widget_token):
            assert visitor.get(preview, headers={"Origin": ORIGIN, "Authorization": "Bearer " + invalid}).status_code == 401
        headers = {"Origin": ORIGIN, "Authorization": "Bearer " + token}
        assert visitor.get(preview, headers=headers).headers["content-type"] == "image/png"
        preflight = visitor.options(preview, headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization",
        })
        assert preflight.status_code == 204 and preflight.headers["access-control-allow-origin"] == ORIGIN
        with monkeypatch.context() as expired:
            expired.setattr(widget_sessions, "time", SimpleNamespace(time=lambda: claims["exp"]))
            assert ask(visitor, token, hint=key, origin=ORIGIN).status_code == 401
            assert visitor.get(preview, headers=headers).status_code == 401

        rotated = rotate(admin, widget)["secret_key"]
        assert rotated != secret
        assert session(visitor, widget, secret).status_code == 401
        assert ask(visitor, token).status_code == 401
        assert visitor.get(preview, headers=headers).status_code == 401
        assert ask(visitor, session(visitor, widget, rotated).json()["token"], origin=HOSTED).status_code == 200


def test_mode_changes_and_site_key_rotation_revoke_old_sessions(public_env):
    with client() as admin, client("192.0.2.11") as visitor:
        signup(admin, "alpha")
        widget = create_widget(admin, access_mode="signed_in")
        path = API + "/widgets/" + widget["id"]
        secret = rotate(admin, widget)["secret_key"]
        token = session(visitor, widget, secret).json()["token"]
        assert ask(visitor, token).status_code == 200
        assert admin.patch(path, json={"access_mode": "public"}, headers=csrf(admin)).status_code == 200
        assert session(visitor, widget, secret).status_code == 403
        assert ask(visitor, token).status_code == 401
        assert admin.patch(path, json={"access_mode": "signed_in"}, headers=csrf(admin)).status_code == 200
        assert ask(visitor, token).status_code == 401
        new_token = session(visitor, widget, secret).json()["token"]
        assert ask(visitor, new_token).status_code == 200
        rotate(admin, widget, site_key=True, secret_key=False)
        assert ask(visitor, new_token).status_code == 401

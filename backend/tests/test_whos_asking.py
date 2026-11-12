"""The API passes the fixed invitation through for greetings, without the "can't help" contact line."""

import json

from backend.app.settings import DocumentLimits
from backend.app.worker import process_one
from rag.citations import CLARIFY_TEXT
from rag.offline import HashingEmbedder, KeywordChat

from .test_widgets import ask_widget, client, create_widget
from .test_workspaces import ALPHA_TEXT, API, csrf, make_pdf, queue, signup


def greet(monkeypatch):
    def complete(self, system, messages, max_tokens=900):
        return json.dumps({"coverage": "clarify", "answer": "", "citations": []})
    monkeypatch.setattr(KeywordChat, "complete", complete)


def test_admin_and_widget_greetings_get_the_invitation(sessions, monkeypatch):
    monkeypatch.setenv("SOURCED_PUBLIC_ORIGIN", "https://chat.sourced.example")
    monkeypatch.setenv("SOURCED_PREVIEW_SECRET", "synthetic-preview-key-for-test-only-0000")
    with client() as owner:
        signup(owner, "alpha")
        document = queue(owner, make_pdf(ALPHA_TEXT))
        assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        key = create_widget(owner, [document])["site_key"]
        greet(monkeypatch)

        admin = owner.post(API + "/chat", headers=csrf(owner), json={"question": "hi", "provider": "offline"})
        assert admin.status_code == 200
        assert admin.json()["status"] == "not_found" and admin.json()["clarify"] is True
        assert admin.json()["text"] == CLARIFY_TEXT

        public = ask_widget(owner, key, body={"question": "I'm newly hired"})
        assert public.status_code == 200
        body = public.json()
        assert body["clarify"] is True and body["text"] == CLARIFY_TEXT and body["fallback"] == ""

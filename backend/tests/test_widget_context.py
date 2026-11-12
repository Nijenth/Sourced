"""Security audit S06: a widget answer is dropped if any document the models read was removed meanwhile."""

from uuid import UUID

import pytest
from sqlalchemy import delete

from backend.app.chat import ChatService
from backend.app.db.models import WidgetDocument
from backend.app.settings import DocumentLimits
from backend.app.worker import process_one
from rag.offline import HashingEmbedder

from .test_widgets import (  # noqa: F401 (fixture)
    ask_widget,
    client,
    create_widget,
    public_env,
)
from .test_workspaces import ALPHA_TEXT, BETA_TEXT, make_pdf, queue, signup


@pytest.mark.usefixtures("public_env")
def test_detaching_an_uncited_context_document_mid_answer_drops_the_answer(sessions, monkeypatch):
    with client() as admin, client("192.0.2.11") as visitor:
        signup(admin, "alpha")
        documents = {str(queue(admin, make_pdf(text))) for text in (ALPHA_TEXT, BETA_TEXT)}
        for _ in range(2):
            assert process_one(sessions, DocumentLimits.from_env(), HashingEmbedder())
        key = create_widget(admin, sorted(documents))["site_key"]
        first = ask_widget(visitor, key).json()
        cited, = {c["document_id"] for c in first["citations"]}
        context_only, = documents - {cited}

        real_ask = ChatService.ask
        detach = {"now": False}

        def ask(self, *args, **kwargs):
            response = real_ask(self, *args, **kwargs)
            # The models also read a passage from the second document, which the answer doesn't cite.
            response.context_document_ids = sorted(documents)
            if detach["now"]:  # an admin removes that document from the widget while the answer is generated
                with sessions.begin() as db:
                    db.execute(delete(WidgetDocument).where(WidgetDocument.document_id == UUID(context_only)))
            return response

        monkeypatch.setattr(ChatService, "ask", ask)
        kept = ask_widget(visitor, key).json()
        assert kept["status"] == "answered"
        assert {c["document_id"] for c in kept["citations"]} == {cited}
        assert "context_document_ids" not in kept  # internal only

        detach["now"] = True
        dropped = ask_widget(visitor, key).json()
        assert dropped["status"] == "not_found" and dropped["citations"] == []

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import chat
from backend.app.sample import app
from rag.citations import BLOCKED_TEXT, UNVERIFIED, Answer
from rag.judge import Judgement
from rag.offline import KeywordChat
from rag.pipeline import CONDENSE_SYSTEM, NOT_FOUND_TEXT
from rag.providers import PROVIDERS, ProviderError

CHAT_PATH = "/api/v1/chat"
LEAVE_QUESTION = "How many days of annual leave do I get?"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RAG_OFFLINE_MODE", "1")
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("TOP_K", "5")
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_providers_without_configuration(monkeypatch):
    monkeypatch.delenv("RAG_OFFLINE_MODE", raising=False)
    for provider in PROVIDERS.values():
        for name in provider.requires:
            monkeypatch.delenv(name, raising=False)

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        response = client.get("/api/v1/providers")
        assert response.status_code == 200
        assert response.json() == [
            {"name": provider.name, "label": provider.label, "ready": False}
            for provider in PROVIDERS.values() if provider.make_chat
        ]
        assert client.post(CHAT_PATH, json={"question": LEAVE_QUESTION}).status_code == 503


def test_offline_provider_is_ready(client):
    response = client.get("/api/v1/providers")
    assert response.status_code == 200
    assert [item for item in response.json() if item["ready"]] == [
        {"name": "offline", "label": KeywordChat.label, "ready": True}
    ]
    assert client.post(CHAT_PATH, json={"question": LEAVE_QUESTION, "provider": "openai"}).status_code == 503
    assert client.post(CHAT_PATH, json={"question": LEAVE_QUESTION, "provider": "missing"}).status_code == 422


def test_answered_with_verified_sample_citation(client):
    response = client.post(CHAT_PATH, json={
        "question": LEAVE_QUESTION,
        "provider": "offline",
        "document_ids": ["brightleaf-handbook"],
    })
    assert response.status_code == 200
    body = response.json()
    citation, = body["citations"]
    assert "24 days" in body["text"]
    assert "24 days" in citation["quote"]
    assert body == {
        "status": "answered",
        "text": body["text"],
        "partial": False,
        "searched_for": "",
        "reason": "",
        "fallback": "",
        "clarify": False,
        "citations": [{
            "number": citation["number"],
            "document_id": "brightleaf-handbook",
            "source": "Brightleaf_Studio_Employee_Handbook.pdf",
            "page": 6,
            "quote": citation["quote"],
            "verified": True,
            "preview_url": None,
        }],
        "passages": [],
        "judgement": None,
    }
    assert f"[{citation['number']}]" in body["text"]


def test_question_outside_handbook(client):
    response = client.post(CHAT_PATH, json={"question": "What is the dress code for client meetings?"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "not_found",
        "text": NOT_FOUND_TEXT,
        "partial": False,
        "searched_for": "",
        "reason": "",
        "fallback": "",
        "clarify": False,
        "citations": [],
        "passages": [],
        "judgement": None,
    }


@pytest.mark.parametrize(("reply", "status", "calls"), [
    (json.dumps({"coverage": "full", "answer": "Fabricated answer 99999 [1]."}), "unverified", 1),
    ("Fabricated answer 99999 in a malformed reply.", "invalid_output", 2),
])
def test_blocked_answer_never_leaks_model_reply(client, monkeypatch, reply, status, calls):
    model_calls = []

    def complete(self, system, messages, max_tokens=900):
        model_calls.append(system)
        return reply

    monkeypatch.setattr(KeywordChat, "complete", complete)
    response = client.post(CHAT_PATH, json={"question": LEAVE_QUESTION})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == status
    assert body["text"] == BLOCKED_TEXT
    assert body["reason"]
    assert body["citations"] == []
    assert any(passage["page"] == 6 for passage in body["passages"])
    for passage in body["passages"]:
        assert set(passage) == {"document_id", "source", "page", "text"}
        assert passage["document_id"] == "brightleaf-handbook"
    assert "Fabricated answer" not in response.text
    assert "99999" not in response.text
    assert "raw" not in body
    assert len(model_calls) == calls


def test_blocked_judge_diagnostics_do_not_repeat_answer():
    answer = Answer(
        text="Blocked model text",
        status=UNVERIFIED,
        found=False,
        reason="A check rejected Blocked model text",
        judgement=Judgement("unsupported", ["Blocked model text"], judge="Judge"),
    )
    response = chat.to_response(answer, LEAVE_QUESTION)
    assert response.text == BLOCKED_TEXT
    assert response.judgement.model_dump() == {"verdict": "unsupported", "problems": [], "judge": "Judge"}
    assert "Blocked model text" not in response.model_dump_json()


def test_history_is_used_for_follow_up(client, monkeypatch):
    complete = KeywordChat.complete

    def rewrite(self, system, messages, max_tokens=900):
        if system == CONDENSE_SYSTEM:
            assert "USER: Tell me about annual leave." in messages[-1]["content"]
            return LEAVE_QUESTION
        return complete(self, system, messages, max_tokens)

    monkeypatch.setattr(KeywordChat, "complete", rewrite)
    response = client.post(CHAT_PATH, json={
        "question": "How much do I get?",
        "history": [{"role": "user", "content": "Tell me about annual leave."}],
    })
    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert response.json()["searched_for"] == LEAVE_QUESTION


@pytest.mark.parametrize("payload", [
    {"question": "   "},
    {"question": "a" * 2001},
    {"history": [{"role": "system", "content": "A role controlled by the server."}]},
    {"history": [{"role": "user", "content": "a" * 601}]},
    {"history": [{"role": "user", "content": "Earlier question"}] * 7},
    {"judge_enabled": False},
])
def test_invalid_chat_request(client, payload):
    response = client.post(CHAT_PATH, json={"question": LEAVE_QUESTION, **payload})
    assert response.status_code == 422


def test_document_selection_excludes_every_other_document(client):
    body = {"question": LEAVE_QUESTION}
    assert client.post(CHAT_PATH, json={**body, "document_ids": ["other-document"]}).status_code == 404
    response = client.post(CHAT_PATH, json={**body, "document_ids": []})
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"


def test_index_is_reused_after_a_failed_build(client, monkeypatch):
    make_embedder = chat.make_embedder
    attempts = []

    def build_embedder():
        attempts.append(None)
        if len(attempts) == 1:
            raise ProviderError("private provider detail")
        return make_embedder()

    monkeypatch.setattr(chat, "make_embedder", build_embedder)
    response = client.post(CHAT_PATH, json={"question": LEAVE_QUESTION})
    assert response.status_code == 503
    assert "private provider detail" not in response.text
    for _ in range(2):
        response = client.post(CHAT_PATH, json={"question": LEAVE_QUESTION})
        assert response.status_code == 200
        assert response.json()["status"] == "answered"
    assert len(attempts) == 2

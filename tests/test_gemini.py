"""Gemini adapter, tested against a fake client (no key or network needed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")

from rag.providers import ProviderError, make_chat, make_embedder, setup_problems  # noqa: E402
from rag.providers.gemini_provider import THINKING_HEADROOM, GeminiChat, GeminiEmbedder  # noqa: E402


class FakeModels:
    def __init__(self, text="ok", dims=4, fail=None):
        self.text, self.dims, self.fail = text, dims, fail
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(("generate", model, contents, config))
        if self.fail:
            raise self.fail
        return SimpleNamespace(text=self.text)

    def embed_content(self, model, contents, config):
        self.calls.append(("embed", model, contents, config))
        if self.fail:
            raise self.fail
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * self.dims) for _ in contents])


def fake_client(**kw):
    return SimpleNamespace(models=FakeModels(**kw))


def _texts(content):
    return [p.text for p in content.parts]


def test_chat_maps_roles_and_system_prompt():
    client = fake_client(text='{"found": false}')
    chat = GeminiChat("key", "gemini-3.6-flash", client=client)
    out = chat.complete("SYSTEM", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
                                   {"role": "user", "content": "again"}], max_tokens=100)
    _, model, contents, config = client.models.calls[0]
    assert out == '{"found": false}' and model == "gemini-3.6-flash"
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert config.system_instruction == "SYSTEM"


def test_gemini_3_gets_low_thinking_and_headroom():
    client = fake_client()
    GeminiChat("key", "gemini-3.6-flash", client=client).complete("s", [{"role": "user", "content": "q"}], max_tokens=100)
    config = client.models.calls[0][3]
    assert config.max_output_tokens == 100 + THINKING_HEADROOM
    assert str(config.thinking_config.thinking_level).lower().endswith("low")


def test_gemini_2_5_gets_no_thinking_level():
    client = fake_client()
    GeminiChat("key", "gemini-2.5-flash", client=client).complete("s", [{"role": "user", "content": "q"}], max_tokens=100)
    config = client.models.calls[0][3]
    assert config.max_output_tokens == 100 and config.thinking_config is None


def test_chat_errors_become_friendly_provider_errors():
    client = fake_client(fail=RuntimeError("429 RESOURCE_EXHAUSTED secret-detail"))
    with pytest.raises(ProviderError, match="rate-limiting") as info:
        GeminiChat("key", client=client).complete("s", [{"role": "user", "content": "q"}])
    assert "secret-detail" not in str(info.value)


def test_embedding_2_uses_document_and_question_prefixes():
    client = fake_client()
    emb = GeminiEmbedder("key", "gemini-embedding-2", dimensions=4, client=client)
    assert len(emb.embed(["policy text"])) == 1
    assert len(emb.embed_query("how many days?")) == 4
    doc_call, query_call = client.models.calls
    assert _texts(doc_call[2][0]) == ["title: none | text: policy text"]
    assert _texts(query_call[2][0]) == ["task: question answering | query: how many days?"]
    assert doc_call[3].output_dimensionality == 4


def test_embedding_001_uses_task_types_instead():
    client = fake_client()
    emb = GeminiEmbedder("key", "gemini-embedding-001", dimensions=4, client=client)
    emb.embed(["policy text"])
    emb.embed_query("how many days?")
    (_, _, doc_contents, doc_cfg), (_, _, q_contents, q_cfg) = client.models.calls
    assert doc_contents == ["policy text"] and str(doc_cfg.task_type).endswith("RETRIEVAL_DOCUMENT")
    assert q_contents == ["how many days?"] and str(q_cfg.task_type).endswith("QUESTION_ANSWERING")


def test_embeddings_are_batched():
    client = fake_client()
    vectors = GeminiEmbedder("key", dimensions=4, batch_size=2, client=client).embed(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5 and len(client.models.calls) == 3


def test_registry_builds_gemini_for_both_jobs():
    env = {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-x", "GEMINI_EMBEDDING_DIMENSIONS": "1536"}
    assert setup_problems(env) == []
    assert make_chat("gemini", env).label == "Google Gemini (gemini-x)"
    assert make_embedder(env).name == "gemini:gemini-embedding-2:1536"
    assert make_chat("google", env).name == "gemini"

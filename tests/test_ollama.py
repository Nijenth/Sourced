"""Ollama adapter, tested with a fake HTTP transport (Ollama doesn't need to be running)."""
from __future__ import annotations

import pytest

from rag.providers import ProviderError, make_chat, make_embedder, setup_problems
from rag.providers.ollama_provider import OllamaChat, OllamaEmbedder, _HTTPError, normalise_host


class FakeOllama:
    def __init__(self, reply="ok", fail=None, dims=3):
        self.reply, self.fail, self.dims = reply, fail, dims
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self.fail:
            err = self.fail.pop(0) if isinstance(self.fail, list) else self.fail
            if err:
                raise err
        if path == "/api/chat":
            return {"message": {"role": "assistant", "content": self.reply}}
        return {"embeddings": [[0.1] * self.dims for _ in payload["input"]]}


def test_chat_sends_system_prompt_and_turns_thinking_off():
    fake = FakeOllama(reply='{"found": true}')
    out = OllamaChat("qwen3.6", transport=fake).complete("SYSTEM", [{"role": "user", "content": "q"}], max_tokens=120)
    path, payload = fake.calls[0]
    assert out == '{"found": true}' and path == "/api/chat"
    assert payload["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert payload["stream"] is False and payload["think"] is False
    assert payload["options"]["num_predict"] == 120


def test_label_drops_the_quantisation_suffix():
    assert OllamaChat("qwen3.6:35b-a3b-q4_K_M", transport=FakeOllama()).label == "Ollama (qwen3.6:35b-a3b, local)"
    assert OllamaChat("llama3.2:3b-instruct-fp16", transport=FakeOllama()).label == "Ollama (llama3.2:3b-instruct, local)"


def test_inline_think_blocks_are_removed():
    fake = FakeOllama(reply="<think>hmm</think>\n{\"found\": false}")
    assert OllamaChat("m", transport=fake).complete("s", [{"role": "user", "content": "q"}]) == '{"found": false}'


def test_model_without_think_support_is_retried_without_it():
    fake = FakeOllama(fail=[_HTTPError(400, '{"error":"model does not support thinking"}'), None])
    chat = OllamaChat("llama", transport=fake)
    assert chat.complete("s", [{"role": "user", "content": "q"}]) == "ok"
    assert "think" in fake.calls[0][1] and "think" not in fake.calls[1][1]


def test_missing_model_says_how_to_download_it():
    fake = FakeOllama(fail=_HTTPError(404, '{"error":"model \\"x\\" not found"}'))
    with pytest.raises(ProviderError, match="ollama pull x"):
        OllamaChat("x", transport=fake).complete("s", [{"role": "user", "content": "q"}])


def test_nomic_embeddings_get_document_and_question_prefixes():
    fake = FakeOllama()
    emb = OllamaEmbedder("nomic-embed-text", transport=fake)
    emb.embed(["policy text"])
    emb.embed_query("how many days?")
    assert fake.calls[0][1]["input"] == ["search_document: policy text"]
    assert fake.calls[1][1]["input"] == ["search_query: how many days?"]


def test_unknown_embedding_models_get_no_prefix_and_are_batched():
    fake = FakeOllama()
    vectors = OllamaEmbedder("some-embedder", batch_size=2, transport=fake).embed(["a", "b", "c"])
    assert len(vectors) == 3 and len(fake.calls) == 2 and fake.calls[0][1]["input"] == ["a", "b"]


def test_host_formats_from_ollama_are_understood():
    assert normalise_host("0.0.0.0") == "http://127.0.0.1:11434"
    assert normalise_host("127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert normalise_host("http://myserver:9000/") == "http://myserver:9000"


def test_unreachable_ollama_gives_a_clear_message():
    chat = OllamaChat("m", host="http://127.0.0.1:9", timeout=2)  # nothing listens on port 9
    with pytest.raises(ProviderError, match="Ollama"):
        chat.complete("s", [{"role": "user", "content": "q"}])


def test_registry_turns_ollama_on_with_a_model_name():
    env = {"OLLAMA_MODEL": "qwen3.6", "EMBEDDING_PROVIDER": "ollama"}
    assert setup_problems(env) == []
    assert make_chat("ollama", env).label == "Ollama (qwen3.6, local)"
    assert make_embedder(env).name == "ollama:nomic-embed-text"
    assert make_embedder({**env, "EMBEDDING_MODEL": "embeddinggemma"}).name == "ollama:embeddinggemma"

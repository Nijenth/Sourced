"""GroqCloud adapter, tested with a fake OpenAI-style client (no key or network needed)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag.providers import ProviderError, make_chat, make_embedder, setup_problems
from rag.providers.groq_provider import BASE_URL, REASONING_HEADROOM, GroqChat


class FakeCompletions:
    def __init__(self, content="ok", fail=None):
        self.content, self.fail, self.calls = content, fail, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def fake_client(**kw):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**kw)))


def test_gpt_oss_gets_low_reasoning_and_headroom():
    client = fake_client(content='{"found": true}')
    out = GroqChat("key", "openai/gpt-oss-120b", client=client).complete("SYS", [{"role": "user", "content": "q"}], 100)
    call = client.chat.completions.calls[0]
    assert out == '{"found": true}'
    assert call["messages"][0] == {"role": "system", "content": "SYS"}
    assert call["extra_body"] == {"reasoning_effort": "low"}
    assert call["max_completion_tokens"] == 100 + REASONING_HEADROOM


def test_qwen_thinking_is_switched_off_and_think_tags_removed():
    client = fake_client(content="<think>...</think> answer")
    out = GroqChat("key", "qwen/qwen3-32b", client=client).complete("s", [{"role": "user", "content": "q"}], 100)
    call = client.chat.completions.calls[0]
    assert out == "answer" and call["extra_body"] == {"reasoning_effort": "none"}
    assert call["max_completion_tokens"] == 100


def test_other_models_get_no_extra_fields():
    client = fake_client()
    GroqChat("key", "some-model", client=client).complete("s", [{"role": "user", "content": "q"}], 100)
    assert "extra_body" not in client.chat.completions.calls[0]


def test_errors_are_reported_as_groq():
    client = fake_client(fail=RuntimeError("Error code: 401 - invalid api key"))
    with pytest.raises(ProviderError, match="Groq rejected the API key"):
        GroqChat("key", client=client).complete("s", [{"role": "user", "content": "q"}])


def test_registry_builds_groq_against_groqs_endpoint():
    env = {"GROQ_API_KEY": "test-key", "GROQ_MODEL": "openai/gpt-oss-20b"}
    chat = make_chat("groq", env)
    assert chat.label == "GroqCloud (openai/gpt-oss-20b)"
    assert str(chat._client.base_url).rstrip("/") == BASE_URL


def test_groq_alone_cannot_index_documents():
    env = {"GROQ_API_KEY": "test-key"}
    problems = setup_problems(env)
    assert len(problems) == 1 and "embeddings" in problems[0]
    with pytest.raises(ProviderError):
        make_embedder({**env, "EMBEDDING_PROVIDER": "groq"})
    # Paired with local embeddings it's fully set up
    assert setup_problems({**env, "OLLAMA_MODEL": "m", "EMBEDDING_PROVIDER": "ollama"}) == []

"""Provider registry: which adapters are available, and clear errors when one isn't set up."""
from __future__ import annotations

import pytest

from rag.providers import (
    PROVIDERS,
    ProviderError,
    chat_provider_names,
    embedding_provider_name,
    friendly_error,
    make_chat,
    make_embedder,
    setup_problems,
)

OPENAI = {"OPENAI_API_KEY": "test-key"}
ANTHROPIC = {"ANTHROPIC_API_KEY": "test-key"}


def test_nothing_set_up_reports_both_jobs():
    assert chat_provider_names({}) == []
    assert embedding_provider_name({}) is None
    assert len(setup_problems({})) == 2


def test_offline_mode_needs_no_keys():
    env = {"RAG_OFFLINE_MODE": "1"}
    assert chat_provider_names(env) == ["offline"]
    assert setup_problems(env) == []
    assert make_chat("offline", env).name == "offline"
    assert make_embedder(env).name == "offline:hashing"


def test_openai_alone_covers_both_jobs():
    assert chat_provider_names(OPENAI) == ["openai"]
    assert embedding_provider_name(OPENAI) == "openai"
    assert setup_problems(OPENAI) == []
    assert make_embedder(OPENAI).name == "openai:text-embedding-3-small"


def test_anthropic_alone_cannot_index_documents():
    assert chat_provider_names(ANTHROPIC) == ["anthropic"]
    problems = setup_problems(ANTHROPIC)
    assert len(problems) == 1 and "embeddings" in problems[0]


def test_chat_only_provider_cannot_be_the_embedding_provider():
    env = {**OPENAI, **ANTHROPIC, "EMBEDDING_PROVIDER": "anthropic"}
    assert "can't create embeddings" in setup_problems(env)[0]
    with pytest.raises(ProviderError):
        make_embedder(env)


def test_chat_provider_setting_goes_first():
    env = {**OPENAI, **ANTHROPIC}
    assert chat_provider_names(env) == ["openai", "anthropic"]
    assert chat_provider_names({**env, "CHAT_PROVIDER": "anthropic"}) == ["anthropic", "openai"]


def test_models_come_from_settings():
    assert make_chat("openai", {**OPENAI, "OPENAI_MODEL": "my-model"}).label == "OpenAI (my-model)"
    assert make_embedder({**OPENAI, "EMBEDDING_MODEL": "my-embed"}).name == "openai:my-embed"


def test_old_claude_name_still_works():
    chat = make_chat("claude", {**ANTHROPIC, "CLAUDE_MODEL": "claude-x"})
    assert chat.name == "anthropic" and "claude-x" in chat.label


def test_unknown_or_unconfigured_provider_raises_clear_error():
    with pytest.raises(ProviderError, match="isn't a chat provider"):
        make_chat("nope", OPENAI)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        make_chat("anthropic", OPENAI)


def test_every_provider_declares_what_it_can_do():
    for name, p in PROVIDERS.items():
        assert p.name == name and p.label
        assert p.make_chat or p.make_embedder
        if p.make_embedder:
            assert p.default_embedding_model


def test_friendly_errors_hide_raw_details():
    assert "rejected the API key" in friendly_error("X", Exception("Error 401: invalid api key sk-123"))
    assert "rate-limiting" in friendly_error("X", Exception("429 Too Many Requests"))
    assert "sk-123" not in friendly_error("X", Exception("weird failure sk-123"))


def test_friendly_errors_tell_daily_limits_and_outages_apart():
    daily = Exception("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert "daily limit" in friendly_error("X", daily)
    assert "temporarily unavailable" in friendly_error("X", Exception("503 UNAVAILABLE: model overloaded"))

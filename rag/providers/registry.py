"""Pick and build providers from settings.

To add a provider: write `<name>_provider.py` with a `PROVIDER`, then add it to `PROVIDERS` below.
"""
from __future__ import annotations

import os

from . import anthropic_provider, gemini_provider, groq_provider, ollama_provider, openai_provider
from .base import ChatModel, Embedder, Env, Provider, ProviderError

# Order = order in the model picker.
PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in (
        openai_provider.PROVIDER,
        anthropic_provider.PROVIDER,
        gemini_provider.PROVIDER,
        groq_provider.PROVIDER,
        ollama_provider.PROVIDER,
    )
}
# Order tried for embeddings when EMBEDDING_PROVIDER isn't set.
EMBEDDING_ORDER: tuple[str, ...] = ("openai", "gemini", "ollama")
ALIASES = {"claude": "anthropic", "google": "gemini"}


def _env(env: Env | None) -> Env:
    return os.environ if env is None else env


def _canonical(name: str) -> str:
    name = name.strip().lower()
    return ALIASES.get(name, name)


def offline_mode(env: Env | None = None) -> bool:
    return _env(env).get("RAG_OFFLINE_MODE", "").lower() in {"1", "true", "yes"}


def chat_provider_names(env: Env | None = None) -> list[str]:
    """Chat providers ready to use, with CHAT_PROVIDER (if set and ready) first."""
    env = _env(env)
    if offline_mode(env):
        return ["offline"]
    names = [n for n, p in PROVIDERS.items() if p.make_chat and p.configured(env)]
    preferred = _canonical(env.get("CHAT_PROVIDER", ""))
    if preferred in names:
        names.remove(preferred)
        names.insert(0, preferred)
    return names


def make_chat(name: str, env: Env | None = None) -> ChatModel:
    env = _env(env)
    name = _canonical(name)
    if name == "offline":
        from ..offline import KeywordChat

        return KeywordChat()
    p = PROVIDERS.get(name)
    if p is None or p.make_chat is None:
        raise ProviderError(f"'{name}' isn't a chat provider this app knows.")
    if not p.configured(env):
        raise ProviderError(f"{p.label} isn't set up. Add {', '.join(p.requires)} to your settings.")
    return p.make_chat(env)


def embedding_provider_name(env: Env | None = None) -> str | None:
    """EMBEDDING_PROVIDER if set, else the first ready provider in EMBEDDING_ORDER."""
    env = _env(env)
    if offline_mode(env):
        return "offline"
    chosen = env.get("EMBEDDING_PROVIDER", "").strip()
    if chosen:
        return _canonical(chosen)
    for name in EMBEDDING_ORDER:
        if PROVIDERS[name].configured(env):
            return name
    return None


def _embedding_problem(env: Env) -> str | None:
    name = embedding_provider_name(env)
    if name is None:
        ready = ", ".join(n for n in EMBEDDING_ORDER) or "none"
        return f"No provider for embeddings (needed to index documents). Set up one of: {ready}."
    if name == "offline":
        return None
    p = PROVIDERS.get(name)
    if p is None:
        return f"EMBEDDING_PROVIDER '{name}' isn't a provider this app knows."
    if p.make_embedder is None:
        return f"{p.label} can't create embeddings. Set EMBEDDING_PROVIDER to one of: {', '.join(EMBEDDING_ORDER)}."
    if not p.configured(env):
        return f"Embeddings use {p.label}, which needs {', '.join(p.requires)}."
    return None


def make_embedder(env: Env | None = None) -> Embedder:
    env = _env(env)
    problem = _embedding_problem(env)
    if problem:
        raise ProviderError(problem)
    name = embedding_provider_name(env)
    if name == "offline":
        from ..offline import HashingEmbedder

        return HashingEmbedder()
    p = PROVIDERS[name]
    model = env.get("EMBEDDING_MODEL") or p.default_embedding_model or ""
    return p.make_embedder(env, model)


def setup_problems(env: Env | None = None) -> list[str]:
    """Plain-language list of what's missing before the app can run. Empty means ready."""
    env = _env(env)
    problems = []
    emb = _embedding_problem(env)
    if emb:
        problems.append(emb)
    if not chat_provider_names(env):
        ready = ", ".join(p.label for p in PROVIDERS.values() if p.make_chat)
        problems.append(f"No provider for answers. Set up one of: {ready}.")
    return problems
